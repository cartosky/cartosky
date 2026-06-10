import { useAuth } from "@clerk/react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

export function ProtectedRoute() {
  const { isLoaded, isSignedIn } = useAuth();
  const location = useLocation();

  if (!isLoaded) {
    return <SiteLoadingOverlay visible label="Loading session" />;
  }

  if (!isSignedIn) {
    const redirectUrl = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?redirect_url=${redirectUrl}`} replace />;
  }

  return <Outlet />;
}
