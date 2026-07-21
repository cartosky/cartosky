import { useEffect } from "react";

import { captureProductAnalyticsEvent } from "@/lib/analytics";
import { useClerkLoadState } from "@/hooks/useClerkLoadState";

// Fires once per page session; guards StrictMode double-effects too.
let reported = false;

/** Reports a Clerk load failure (blocked/hung ClerkJS script) to analytics. */
export function ClerkLoadFailureReporter() {
  const { state, failureReason } = useClerkLoadState();

  useEffect(() => {
    if (state !== "failed" || reported) {
      return;
    }
    reported = true;
    captureProductAnalyticsEvent("auth_load_failed", {
      provider: "clerk",
      reason: failureReason,
    });
  }, [state, failureReason]);

  return null;
}
