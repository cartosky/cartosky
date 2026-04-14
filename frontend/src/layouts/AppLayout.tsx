import { Outlet, useLocation } from "react-router-dom";
import SiteHeader from "../components/SiteHeader";

export default function AppLayout() {
  const location = useLocation();
  const isAdminRoute = location.pathname.startsWith("/admin");
  // On the viewer route, App.tsx renders SiteHeader itself (inside the
  // ViewerToolbarContext.Provider) so that the header can read toolbar state.
  const isViewerRoute = location.pathname === "/viewer";

  return (
    <div
      className={
        isAdminRoute
          ? "min-h-svh flex flex-col overflow-x-hidden bg-background text-foreground"
          : "h-svh min-h-svh flex flex-col overflow-hidden bg-background text-foreground"
      }
    >
      {!isViewerRoute && <SiteHeader variant="app" />}
      <div className={isAdminRoute ? "flex-1 min-h-0" : "flex flex-1 min-h-0 overflow-hidden"}>
        <Outlet />
      </div>
    </div>
  );
}
