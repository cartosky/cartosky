from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

PNG_CONTENT_TYPE = "image/png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GIF_CONTENT_TYPE = "image/gif"
GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
MAX_SHARE_PNG_BYTES = 10 * 1024 * 1024

# Share overhaul Phase 3 gate feedback: TWF posts can carry client-generated
# GIFs, so the media upload accepts both formats (same 10 MB cap — the GIF
# frame caps keep real exports a few MB at most).
SUPPORTED_SHARE_MEDIA: dict[str, dict] = {
    PNG_CONTENT_TYPE: {"signatures": (PNG_SIGNATURE,), "extension": ".png"},
    GIF_CONTENT_TYPE: {"signatures": GIF_SIGNATURES, "extension": ".gif"},
}
_KNOWN_EXTENSIONS = (".png", ".gif")
_FILENAME_SAFE_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")
_RUN_SAFE_RE = re.compile(r"[^a-z0-9_]+")


@dataclass
class ShareMediaError(Exception):
    status_code: int
    code: str
    message: str


def _slugify(value: str | None) -> str:
    cleaned = (value or "").strip().lower().replace("_", "-")
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = _SLUG_SAFE_RE.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned


def _sanitize_filename_token(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = _FILENAME_SAFE_RE.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned


def _sanitize_run(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = _RUN_SAFE_RE.sub("", cleaned)
    return cleaned.strip("_")


def _parse_forecast_hour(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def build_share_png_filename_hint(
    *,
    model: str | None = None,
    run: str | None = None,
    fh: str | int | None = None,
    variable: str | None = None,
    region: str | None = None,
) -> str | None:
    model_part = _sanitize_filename_token(model)
    run_part = _sanitize_run(run)
    forecast_hour = _parse_forecast_hour(fh)
    variable_part = _slugify(variable)
    region_part = _slugify(region)

    parts = ["cartosky"]
    if model_part:
        parts.append(model_part)
    if run_part:
        parts.append(run_part)
    if forecast_hour is not None:
        parts.append(f"fh{forecast_hour}")
    if variable_part:
        parts.append(variable_part)
    if region_part:
        parts.append(region_part)

    if len(parts) == 1:
        return None
    return f"{'_'.join(parts)}.png"


def validate_share_png_upload(data: bytes, *, content_type: str) -> None:
    """Validate share PNG bytes before persisting to object storage."""
    _validate_upload(data, content_type=content_type)


def _validate_upload(data: bytes, *, content_type: str) -> None:
    normalized_content_type = (content_type or "").strip().lower()
    media_spec = SUPPORTED_SHARE_MEDIA.get(normalized_content_type)
    if media_spec is None:
        raise ShareMediaError(
            status_code=400,
            code="INVALID_CONTENT_TYPE",
            message="Only PNG or GIF uploads are supported.",
        )
    if not data:
        raise ShareMediaError(
            status_code=400,
            code="EMPTY_FILE",
            message="Uploaded file is empty.",
        )
    if len(data) > MAX_SHARE_PNG_BYTES:
        raise ShareMediaError(
            status_code=413,
            code="FILE_TOO_LARGE",
            message="Upload exceeds the 10 MB limit.",
        )
    if not any(data.startswith(signature) for signature in media_spec["signatures"]):
        raise ShareMediaError(
            status_code=400,
            code="INVALID_IMAGE",
            message="Uploaded file does not match its image type.",
        )


def _r2_settings() -> dict[str, str]:
    settings = {
        "endpoint": os.environ.get("R2_ENDPOINT", "").strip(),
        "bucket": os.environ.get("R2_BUCKET", "").strip(),
        "access_key": os.environ.get("R2_ACCESS_KEY", "").strip(),
        "secret_key": os.environ.get("R2_SECRET_KEY", "").strip(),
        "public_base": os.environ.get("R2_PUBLIC_BASE", "").strip().rstrip("/"),
    }
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise ShareMediaError(
            status_code=500,
            code="SHARE_MEDIA_MISCONFIGURED",
            message="Share media upload is not configured.",
        )
    return settings


def _build_object_name(filename_hint: str | None, *, now: datetime, extension: str = ".png") -> str:
    random_suffix = secrets.token_hex(4)
    if filename_hint:
        stem = filename_hint.strip().rsplit("/", 1)[-1]
        for known_extension in _KNOWN_EXTENSIONS:
            if stem.lower().endswith(known_extension):
                stem = stem[: -len(known_extension)]
                break
        stem = _sanitize_filename_token(stem)
        if stem:
            return f"{stem}_{random_suffix}{extension}"

    timestamp = now.strftime("%Y%m%dT%H%M%SZ").lower()
    return f"cartosky_{timestamp}_{random_suffix}{extension}"


def upload_share_png(
    *,
    data: bytes,
    filename_hint: str | None = None,
    content_type: str = PNG_CONTENT_TYPE,
) -> dict[str, str]:
    _validate_upload(data, content_type=content_type)
    normalized_content_type = (content_type or "").strip().lower()
    extension = SUPPORTED_SHARE_MEDIA[normalized_content_type]["extension"]
    settings = _r2_settings()
    now = datetime.now(UTC)
    object_name = _build_object_name(filename_hint, now=now, extension=extension)
    key = f"share/{now.strftime('%Y/%m/%d')}/{object_name}"
    url = f"{settings['public_base']}/{key}"

    client = boto3.client(
        "s3",
        endpoint_url=settings["endpoint"],
        aws_access_key_id=settings["access_key"],
        aws_secret_access_key=settings["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    try:
        client.put_object(
            Bucket=settings["bucket"],
            Key=key,
            Body=data,
            ContentType=normalized_content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
    except (BotoCoreError, ClientError) as exc:
        raise ShareMediaError(
            status_code=502,
            code="UPLOAD_FAILED",
            message="Failed to upload share image.",
        ) from exc

    return {"key": key, "url": url}
