import { Suspense } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { AdminRouteSuspenseFallback } from "@/components/route-suspense-fallbacks";
import { ViewerMapSkeleton } from "@/components/ViewerMapSkeleton";
import { ViewerSiteHeaderFallback } from "@/components/ViewerSiteHeaderFallback";
import { BootstrapCompleteMarker } from "@/lib/bootstrap-loading";
import SiteHeader from "../components/SiteHeader";

export default function AppLayout() {
  const location = useLocation();
  const isAdminRoute = location.pathname.startsWith("/admin");
  const isViewerRoute = location.pathname === "/viewer";

  return (
    <div
      className={
        isAdminRoute
          ? "min-h-svh flex flex-col overflow-x-hidden bg-background text-foreground"
          : "h-svh min-h-svh flex flex-col overflow-hidden bg-background text-foreground"
      }
    >
      {isViewerRoute ? <ViewerSiteHeaderFallback /> : <SiteHeader variant="app" />}

      <div className={isAdminRoute ? "flex-1 min-h-0 w-full" : "flex flex-1 min-h-0 overflow-hidden"}>
        <Suspense fallback={isViewerRoute ? <ViewerMapSkeleton /> : <AdminRouteSuspenseFallback />}>
          <Outlet />
          <BootstrapCompleteMarker />
        </Suspense>
      </div>
    </div>
  );
}
