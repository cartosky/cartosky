import { API_V4_BASE } from "@/lib/config";

const SHARE_MEDIA_UPLOAD_TIMEOUT_MS = 30_000;

export type ShareMediaUploadParams = {
  blob: Blob;
  filename: string;
  model?: string | null;
  run?: string | null;
  fh?: number | null;
  variable?: string | null;
  region?: string | null;
};

export type ShareMediaUploadResult = {
  ok: true;
  key: string;
  url: string;
};

type ApiErrorInfo = {
  code?: string;
  message: string;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

async function readApiError(response: Response): Promise<ApiErrorInfo | null> {
  try {
    const body = (await response.json()) as unknown;
    if (!isObject(body)) {
      return null;
    }
    const err = body.error;
    if (!isObject(err)) {
      return null;
    }
    const message = typeof err.message === "string" ? err.message.trim() : "";
    if (!message) {
      return null;
    }
    const code = typeof err.code === "string" && err.code.trim() ? err.code.trim() : undefined;
    return { code, message };
  } catch {
    return null;
  }
}

export async function uploadShareMedia(params: ShareMediaUploadParams): Promise<ShareMediaUploadResult> {
  const formData = new FormData();
  formData.append("file", params.blob, params.filename);
  if (params.model) {
    formData.append("model", params.model);
  }
  if (params.run) {
    formData.append("run", params.run);
  }
  if (Number.isFinite(params.fh)) {
    formData.append("fh", String(Math.max(0, Math.round(Number(params.fh)))));
  }
  if (params.variable) {
    formData.append("variable", params.variable);
  }
  if (params.region) {
    formData.append("region", params.region);
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), SHARE_MEDIA_UPLOAD_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${API_V4_BASE}/share/media`, {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("Screenshot upload timed out. Try again in a moment.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const apiError = await readApiError(response);
    throw new Error(apiError?.message || `Upload failed (${response.status})`);
  }

  const body = (await response.json()) as unknown;
  if (
    !isObject(body) ||
    body.ok !== true ||
    typeof body.key !== "string" ||
    typeof body.url !== "string" ||
    !body.key.trim() ||
    !body.url.trim()
  ) {
    throw new Error("Unexpected upload response from server.");
  }

  return {
    ok: true,
    key: body.key.trim(),
    url: body.url.trim(),
  };
}
