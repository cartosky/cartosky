import { Outlet } from "react-router-dom";
import SiteHeader from "../components/SiteHeader";

export default function AppLayout() {
  return (
    <div className="h-svh min-h-svh flex flex-col overflow-hidden bg-background text-foreground">
      <SiteHeader variant="app" />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
