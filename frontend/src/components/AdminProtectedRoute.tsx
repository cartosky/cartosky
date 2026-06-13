import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";
import { fetchAdminAuthStatus, type TwfStatus } from "@/lib/admin-api";

export function AdminProtectedRoute() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const authStatus = await fetchAdminAuthStatus();
        if (!cancelled) {
          setStatus(authStatus);
        }
      } catch {
        if (!cancelled) {
          setStatus({ linked: false, admin: false });
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading || status === null) {
    return <SiteLoadingOverlay visible label="Checking admin access" />;
  }

  if (!status.linked || !status.admin) {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
