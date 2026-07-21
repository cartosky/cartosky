import { useEffect, useState } from "react";
import { useAuth, useClerk } from "@clerk/react";

export type ClerkLoadState = {
  state: "loading" | "ready" | "failed";
  /** Set when state === "failed": how the failure was detected. */
  failureReason: "error_status" | "timeout" | null;
};

// ClerkJS loads from Clerk's domain at runtime; when that script is blocked
// (ad/content blockers) or fails, isLoaded stays false forever with only a
// console error. The SDK does emit a "status" event ("error") we can watch,
// and the watchdog timer covers hangs that never emit anything.
const CLERK_LOAD_TIMEOUT_MS = 15_000;

type ClerkStatusEmitter = {
  on?: (event: "status", handler: (status: string) => void, opts?: { notify?: boolean }) => void;
  off?: (event: "status", handler: (status: string) => void) => void;
};

export function useClerkLoadState(): ClerkLoadState {
  const { isLoaded } = useAuth();
  const clerk = useClerk() as unknown as ClerkStatusEmitter;
  const [failureReason, setFailureReason] = useState<"error_status" | "timeout" | null>(null);

  useEffect(() => {
    if (isLoaded) {
      return;
    }
    const handleStatus = (status: string) => {
      if (status === "error") {
        setFailureReason((current) => current ?? "error_status");
      }
    };
    // notify: true replays the current status, so late subscribers still see
    // an "error" that fired before this component mounted.
    clerk.on?.("status", handleStatus, { notify: true });
    const timer = window.setTimeout(() => {
      setFailureReason((current) => current ?? "timeout");
    }, CLERK_LOAD_TIMEOUT_MS);
    return () => {
      clerk.off?.("status", handleStatus);
      window.clearTimeout(timer);
    };
  }, [clerk, isLoaded]);

  if (isLoaded) {
    return { state: "ready", failureReason: null };
  }
  return failureReason ? { state: "failed", failureReason } : { state: "loading", failureReason: null };
}
