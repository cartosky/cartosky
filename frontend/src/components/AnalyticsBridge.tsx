import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { fetchTwfStatus } from "@/lib/admin-api";
import { captureAnalyticsPageview, syncAnalyticsAuthStatus } from "@/lib/analytics";

export function AnalyticsBridge() {
  const location = useLocation();

  useEffect(() => {
    let cancelled = false;

    async function loadAuthStatus() {
      try {
        const status = await fetchTwfStatus();
        if (cancelled) {
          return;
        }
        syncAnalyticsAuthStatus(status);
      } catch {
        // Ignore analytics identity failures.
      }
    }

    void loadAuthStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    captureAnalyticsPageview(location.pathname, location.search);
  }, [location.pathname, location.search]);

  return null;
}