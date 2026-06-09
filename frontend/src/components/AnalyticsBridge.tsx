import { useUser } from "@clerk/react";
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { fetchTwfStatus } from "@/lib/admin-api";
import { captureAnalyticsPageview, syncAnalyticsAuthStatus } from "@/lib/analytics";

export function AnalyticsBridge() {
  const { user } = useUser();
  const clerkUserId = user?.id ?? null;
  const location = useLocation();

  useEffect(() => {
    if (clerkUserId === null) {
      return;
    }

    let cancelled = false;

    async function loadAuthStatus() {
      try {
        const status = await fetchTwfStatus();
        if (cancelled) {
          return;
        }
        syncAnalyticsAuthStatus(clerkUserId, status);
      } catch {
        // Ignore analytics identity failures.
      }
    }

    void loadAuthStatus();
    return () => {
      cancelled = true;
    };
  }, [clerkUserId]);

  useEffect(() => {
    captureAnalyticsPageview(location.pathname, location.search);
  }, [location.pathname, location.search]);

  return null;
}