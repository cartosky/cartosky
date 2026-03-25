const VIEWER_DEBUG_QUERY_KEY = "twf_debug_viewer";
const VIEWER_DEBUG_STORAGE_KEY = "twf_debug_viewer";

export function isViewerDebugEnabled(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    const params = new URLSearchParams(window.location.search);
    const queryValue = params.get(VIEWER_DEBUG_QUERY_KEY);
    if (queryValue === "1" || queryValue === "true") {
      window.localStorage.setItem(VIEWER_DEBUG_STORAGE_KEY, "1");
      return true;
    }
    if (queryValue === "0" || queryValue === "false") {
      window.localStorage.removeItem(VIEWER_DEBUG_STORAGE_KEY);
      return false;
    }
    return window.localStorage.getItem(VIEWER_DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function viewerDebugLog(scope: string, payload: Record<string, unknown>): void {
  if (!isViewerDebugEnabled()) {
    return;
  }
  console.debug(`[viewer-debug] ${scope}`, payload);
}
