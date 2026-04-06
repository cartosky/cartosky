import { useCallback, useRef, useState } from "react";
import { FRAME_STATUS_BADGE_MS } from "@/lib/app-utils";

export interface UseFrameStatusBadgeReturn {
  /** The current transient status message, or `null` if none is showing. */
  frameStatusMessage: string | null;
  /** Display a transient status badge that auto-clears after FRAME_STATUS_BADGE_MS. */
  showTransientFrameStatus: (message: string) => void;
  /** Immediately clear any active frame-status badge and cancel its timer. */
  clearFrameStatusTimer: () => void;
}

/**
 * Manages the transient frame-status badge shown over the map
 * (e.g. "Starting playback", "Buffering grid frames").
 *
 * The badge auto-dismisses after `FRAME_STATUS_BADGE_MS` milliseconds
 * and can also be cleared programmatically.
 */
export function useFrameStatusBadge(): UseFrameStatusBadgeReturn {
  const [frameStatusMessage, setFrameStatusMessage] = useState<string | null>(null);
  const frameStatusTimerRef = useRef<number | null>(null);

  const clearFrameStatusTimer = useCallback(() => {
    if (frameStatusTimerRef.current !== null) {
      window.clearTimeout(frameStatusTimerRef.current);
      frameStatusTimerRef.current = null;
    }
    setFrameStatusMessage(null);
  }, []);

  const showTransientFrameStatus = useCallback((message: string) => {
    setFrameStatusMessage(message);
    if (frameStatusTimerRef.current !== null) {
      window.clearTimeout(frameStatusTimerRef.current);
    }
    frameStatusTimerRef.current = window.setTimeout(() => {
      frameStatusTimerRef.current = null;
      setFrameStatusMessage(null);
    }, FRAME_STATUS_BADGE_MS);
  }, []);

  return { frameStatusMessage, showTransientFrameStatus, clearFrameStatusTimer };
}
