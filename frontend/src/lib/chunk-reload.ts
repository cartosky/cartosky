export const CHUNK_RELOAD_SESSION_KEY = "cartosky:lazy-chunk-reload";

export function isRecoverableChunkError(error: unknown): boolean {
  const message = (
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : String((error as any)?.message ?? "")
  ).toLowerCase();

  return (
    message.includes("dynamically imported module")
    || message.includes("failed to fetch dynamically imported module")
    || message.includes("error loading dynamically imported module")
    || message.includes("importing a module script failed")
    || message.includes("chunkloaderror")
    || message.includes("unable to preload css")
    || message.includes("loading css chunk")
  );
}

export function markChunkReloadAttempted(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    if (window.sessionStorage.getItem(CHUNK_RELOAD_SESSION_KEY) === "1") {
      return false;
    }
    window.sessionStorage.setItem(CHUNK_RELOAD_SESSION_KEY, "1");
    return true;
  } catch {
    return true;
  }
}

export function clearChunkReloadAttempt(): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.removeItem(CHUNK_RELOAD_SESSION_KEY);
  } catch {
    // Ignore session storage failures and continue without persistence.
  }
}
