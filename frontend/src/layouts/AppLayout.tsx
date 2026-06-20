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
  const isRoadmapRoute = location.pathname === "/roadmap";
  const isScrollableAppRoute = isAdminRoute || isRoadmapRoute;
  const isViewerRoute = location.pathname === "/viewer";
  const isCompareRoute = location.pathname === "/compare";
  // Viewer and compare share the same non-scrollable, fixed-height shell. The
  // layout class is keyed off isScrollableAppRoute (admin/roadmap), so both of
  // these routes already fall into the fixed-height branch below.
  const isViewerLike = isViewerRoute || isCompareRoute;

  return (
    <div
      className={
        isScrollableAppRoute
          ? "min-h-svh flex flex-col overflow-x-hidden bg-background text-foreground"
          : "h-svh min-h-svh flex flex-col overflow-hidden bg-background text-foreground"
      }
    >
      {isViewerLike ? <ViewerSiteHeaderFallback /> : <SiteHeader variant="app" />}

      <div className={isScrollableAppRoute ? "flex-1 min-h-0 w-full" : "flex flex-1 min-h-0 overflow-hidden"}>
        <Suspense fallback={isViewerLike ? <ViewerMapSkeleton /> : <AdminRouteSuspenseFallback />}>
          <Outlet />
          <BootstrapCompleteMarker />
        </Suspense>
      </div>
    </div>
  );
}
