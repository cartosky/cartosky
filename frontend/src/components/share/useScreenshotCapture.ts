// Screenshot capture/upload state for the share modal (share overhaul Phase 2).
// Extracted verbatim from twf-share-modal.tsx — behavior-preserving move only.
// Owns the client (live-canvas) and server (headless) capture paths, upload to
// share media, and the auto-capture-on-open effect.

import { useCallback, useEffect, useRef, useState } from "react";

import { clerkJwtTemplate } from "@/lib/admin-api";
import { API_ORIGIN, SERVER_SCREENSHOT_ENABLED } from "@/lib/config";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import type { LegendPayload } from "@/components/map-legend";
import { uploadShareMedia } from "@/lib/share_media";
import { screenshotFilename, screenshotUrlForState } from "@/components/share/share-utils";

type GeneratedScreenshot = {
  blob: Blob;
  blobUrl: string;
  filename: string;
  state: ScreenshotExportState;
};

export type UseScreenshotCaptureParams = {
  open: boolean;
  permalink: string;
  buildScreenshotState?: () => ScreenshotExportState | null;
  getLegend?: () => LegendPayload | null;
  getDraftDataUrl?: () => Promise<string | null>;
  captureMapPng?: () => Promise<string | null>;
  clerkLoaded: boolean;
  isSignedIn: boolean | undefined;
  getToken: (options: { template: string }) => Promise<string | null>;
  twfFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
};

export function useScreenshotCapture({
  open,
  permalink,
  buildScreenshotState,
  getLegend,
  getDraftDataUrl,
  captureMapPng,
  clerkLoaded,
  isSignedIn,
  getToken,
  twfFetch,
}: UseScreenshotCaptureParams) {
  const wasOpenRef = useRef(false);
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const [screenshotError, setScreenshotError] = useState<string | null>(null);
  const [draftDataUrl, setDraftDataUrl] = useState<string | null>(null);
  const [screenshotBlob, setScreenshotBlob] = useState<Blob | null>(null);
  const [screenshotBlobUrl, setScreenshotBlobUrl] = useState<string | null>(null);
  const [screenshotStateSnapshot, setScreenshotStateSnapshot] = useState<ScreenshotExportState | null>(null);
  const [screenshotFilenameValue, setScreenshotFilenameValue] = useState("cartosky-map-screenshot.png");
  const [screenshotUploadBusy, setScreenshotUploadBusy] = useState(false);
  const [screenshotUploadError, setScreenshotUploadError] = useState<string | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null);
  const [screenshotKey, setScreenshotKey] = useState<string | null>(null);
  const [includeScreenshotInPost, setIncludeScreenshotInPost] = useState(false);
  const [hasAttemptedAutoScreenshot, setHasAttemptedAutoScreenshot] = useState(false);

  const canPrepareScreenshot = Boolean(buildScreenshotState);

  // WYSIWYG local capture (share overhaul Phase 1): read the live map canvas
  // and compose overlay/legend/logo on top. Works signed-out, no server render.
  const generateClientScreenshot = useCallback(async (): Promise<GeneratedScreenshot | null> => {
    setScreenshotError(null);
    if (!buildScreenshotState || !captureMapPng) {
      setScreenshotError("Screenshot export is unavailable right now.");
      return null;
    }

    const state = buildScreenshotState();
    if (!state) {
      setScreenshotError("Map is still loading. Try again in a moment.");
      return null;
    }

    setScreenshotBusy(true);
    try {
      const capturedMapDataUrl = await captureMapPng();
      if (!capturedMapDataUrl) {
        throw new Error("Map capture unavailable. Retry the screenshot.");
      }
      const stateWithCapture: ScreenshotExportState = { ...state, capturedMapDataUrl };
      const { exportViewerScreenshotPng } = await import("@/lib/screenshot_export");
      const blob = await exportViewerScreenshotPng(stateWithCapture, {
        legend: getLegend?.() ?? null,
      });
      const objectUrl = URL.createObjectURL(blob);
      const filename = screenshotFilename(state);
      setScreenshotBlob(blob);
      setScreenshotStateSnapshot(stateWithCapture);
      setScreenshotFilenameValue(filename);
      setScreenshotUploadError(null);
      setScreenshotUrl(null);
      setScreenshotKey(null);
      setIncludeScreenshotInPost(true);
      setScreenshotBlobUrl((previous) => {
        if (previous) {
          URL.revokeObjectURL(previous);
        }
        return objectUrl;
      });
      setDraftDataUrl(null);
      return {
        blob,
        blobUrl: objectUrl,
        filename,
        state: stateWithCapture,
      };
    } catch (error) {
      const message = error instanceof Error && error.message
        ? error.message
        : "Screenshot generation failed.";
      setScreenshotError(message);
      return null;
    } finally {
      setScreenshotBusy(false);
    }
  }, [buildScreenshotState, captureMapPng, getLegend]);

  const generateServerScreenshot = useCallback(async (): Promise<GeneratedScreenshot | null> => {
    setScreenshotError(null);
    const state = buildScreenshotState?.() ?? null;
    if (!state) {
      setScreenshotError("Map is still loading. Try again in a moment.");
      return null;
    }
    setScreenshotBusy(true);
    try {
      if (!permalink) {
        throw new Error("No permalink available.");
      }
      const screenshotRenderUrl = screenshotUrlForState(permalink, state);
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 35000);
      let response: Response;
      try {
        response = await twfFetch(`${API_ORIGIN}/api/v4/share/screenshot`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: screenshotRenderUrl,
            basemap: state.basemapMode ?? "light",
          }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeoutId);
      }
      if (!response.ok) {
        throw new Error(`Server screenshot failed (${response.status})`);
      }
      const blob = await response.blob();
      const arrayBuffer = await blob.arrayBuffer();
      const bytes = new Uint8Array(arrayBuffer);
      let binary = "";
      const chunkSize = 0x8000;
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        const chunk = bytes.subarray(offset, offset + chunkSize);
        binary += String.fromCharCode(...chunk);
      }
      const dataUrl = `data:image/png;base64,${btoa(binary)}`;
      // Use the returned image's real dimensions so the compose keeps its
      // aspect (no cover-crop). Viewer renders are 1280×720, but compare split
      // composites are wider than 16:9 — hardcoding 1280×720 cropped their
      // left/right edges (no-silent-crop rule).
      const captureDims = await new Promise<{ width: number; height: number } | null>((resolve) => {
        const image = new Image();
        image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
        image.onerror = () => resolve(null);
        image.src = dataUrl;
      });
      const stateWithCapture: ScreenshotExportState = {
        ...state,
        capturedMapDataUrl: dataUrl,
        viewportWidth: captureDims?.width ?? 1280,
        viewportHeight: captureDims?.height ?? 720,
        isMobile: false,
      };
      const { exportViewerScreenshotPng } = await import("@/lib/screenshot_export");
      const finalBlob = await exportViewerScreenshotPng(
        stateWithCapture,
        { legend: getLegend?.() ?? null },
      );
      const objectUrl = URL.createObjectURL(finalBlob);
      const filename = screenshotFilename(state);
      setScreenshotBlob(finalBlob);
      setScreenshotStateSnapshot(stateWithCapture);
      setScreenshotFilenameValue(filename);
      setScreenshotUploadError(null);
      setScreenshotUrl(null);
      setScreenshotKey(null);
      setIncludeScreenshotInPost(true);
      setScreenshotBlobUrl((previous) => {
        if (previous) {
          URL.revokeObjectURL(previous);
        }
        return objectUrl;
      });
      setDraftDataUrl(null);
      return { blob: finalBlob, blobUrl: objectUrl, filename, state: stateWithCapture };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "";
      const message =
        error instanceof Error && error.name === "AbortError"
          ? "Screenshot timed out. Try again."
          : errorMessage === "Sign in to CartoSky before connecting TWF."
            ? "Sign in to CartoSky before generating a share image."
          : error instanceof Error
            ? errorMessage
            : "Server screenshot failed.";
      setScreenshotError(message);
      return null;
    } finally {
      setScreenshotBusy(false);
    }
  }, [buildScreenshotState, getLegend, permalink, twfFetch]);

  // Reset screenshot state each time the modal opens (split out of the old
  // single open-reset effect; same open-transition guard semantics).
  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      setDraftDataUrl(null);
      return;
    }
    if (wasOpenRef.current) {
      return;
    }
    wasOpenRef.current = true;
    setScreenshotBusy(false);
    setScreenshotError(null);
    setScreenshotBlob(null);
    setScreenshotFilenameValue("cartosky-map-screenshot.png");
    setScreenshotStateSnapshot(null);
    setScreenshotUploadBusy(false);
    setScreenshotUploadError(null);
    setScreenshotUrl(null);
    setScreenshotKey(null);
    setIncludeScreenshotInPost(true);
    setHasAttemptedAutoScreenshot(false);
    setScreenshotBlobUrl((previous) => {
      if (previous) {
        URL.revokeObjectURL(previous);
      }
      return null;
    });
    if (getDraftDataUrl && SERVER_SCREENSHOT_ENABLED) {
      void getDraftDataUrl().then((dataUrl) => {
        if (dataUrl) setDraftDataUrl(dataUrl);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    return () => {
      if (screenshotBlobUrl) {
        URL.revokeObjectURL(screenshotBlobUrl);
      }
    };
  }, [screenshotBlobUrl]);

  useEffect(() => {
    if (!open || hasAttemptedAutoScreenshot || !canPrepareScreenshot) {
      return;
    }
    // Wait for the auth state before picking a capture path: signed-in keeps
    // the server render (its preview doubles as the TWF post artifact), while
    // signed-out uses the instant live-canvas capture.
    if (!clerkLoaded) {
      return;
    }
    if (screenshotBusy || screenshotUploadBusy || screenshotBlobUrl) {
      return;
    }
    setHasAttemptedAutoScreenshot(true);
    void (SERVER_SCREENSHOT_ENABLED && isSignedIn
      ? generateServerScreenshot()
      : generateClientScreenshot());
  }, [
    canPrepareScreenshot,
    clerkLoaded,
    generateServerScreenshot,
    generateClientScreenshot,
    hasAttemptedAutoScreenshot,
    isSignedIn,
    open,
    screenshotBlobUrl,
    screenshotBusy,
    screenshotUploadBusy,
  ]);

  const uploadScreenshot = async (options?: {
    blob?: Blob | null;
    filename?: string | null;
    state?: ScreenshotExportState | null;
  }): Promise<string | null> => {
    const blob = options?.blob ?? screenshotBlob;
    const filename = options?.filename ?? screenshotFilenameValue;
    const state = options?.state ?? screenshotStateSnapshot;

    if (!blob) {
      setScreenshotUploadError("Generate a screenshot before uploading.");
      return null;
    }

    setScreenshotUploadBusy(true);
    setScreenshotUploadError(null);
    setScreenshotUrl(null);
    setScreenshotKey(null);

    try {
      if (!clerkLoaded) {
        throw new Error("Checking CartoSky sign-in status.");
      }
      if (!isSignedIn) {
        throw new Error("Sign in to CartoSky before uploading a share image.");
      }
      const token = await getToken({ template: clerkJwtTemplate() });
      if (!token) {
        throw new Error("Unable to load CartoSky auth token.");
      }

      const result = await uploadShareMedia({
        blob,
        filename,
        authToken: token,
        model: state?.model ?? null,
        run: state?.run ?? null,
        fh: state?.fh ?? null,
        variable: state?.variable.key || state?.variable.label || null,
        region: state?.region?.id ?? null,
      });
      setScreenshotUrl(result.url);
      setScreenshotKey(result.key);
      setIncludeScreenshotInPost(true);
      return result.url;
    } catch (error) {
      const message = error instanceof Error && error.message
        ? error.message
        : "Screenshot upload failed.";
      setScreenshotUploadError(message);
      return null;
    } finally {
      setScreenshotUploadBusy(false);
    }
  };

  const handlePrepareScreenshot = async () => {
    setScreenshotError(null);
    if (screenshotBusy || screenshotUploadBusy) {
      return;
    }
    await (SERVER_SCREENSHOT_ENABLED && isSignedIn
      ? generateServerScreenshot()
      : generateClientScreenshot());
  };

  const ensurePreparedScreenshot = async (): Promise<string | null> => {
    if (!includeScreenshotInPost) {
      return null;
    }
    if (screenshotUrl) {
      return screenshotUrl;
    }
    if (screenshotBusy || screenshotUploadBusy) {
      setScreenshotError("Screenshot is still generating — wait a moment and try again.");
      return null;
    }
    const generated = screenshotBlob
      ? {
          blob: screenshotBlob,
          filename: screenshotFilenameValue,
          state: screenshotStateSnapshot,
        }
      : await (SERVER_SCREENSHOT_ENABLED
          ? generateServerScreenshot()
          : generateClientScreenshot());
    if (!generated) {
      return null;
    }
    const uploadedUrl = await uploadScreenshot({
      blob: generated.blob,
      filename: generated.filename,
      state: generated.state,
    });
    return uploadedUrl;
  };

  return {
    canPrepareScreenshot,
    screenshotBusy,
    screenshotError,
    draftDataUrl,
    screenshotBlob,
    screenshotBlobUrl,
    screenshotFilenameValue,
    screenshotUploadBusy,
    screenshotUploadError,
    includeScreenshotInPost,
    setHasAttemptedAutoScreenshot,
    handlePrepareScreenshot,
    ensurePreparedScreenshot,
  };
}
