import { type ComponentType } from "react";
import { useAuth } from "@clerk/react";
import { Activity, BarChart3, ClipboardCheck, Gauge, MessageSquareText, Waypoints } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

function AdminNavItem(props: { to: string; label: string; icon: ComponentType<{ className?: string }> }) {
  const { to, label, icon: Icon } = props;
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        [
          "inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-all duration-150",
          isActive
            ? "border-cyan-300/24 bg-cyan-300/[0.1] text-cyan-50 shadow-[0_10px_24px_rgba(0,0,0,0.18)]"
            : "border-white/10 bg-white/[0.03] text-white/70 hover:border-white/18 hover:bg-white/[0.06] hover:text-white",
        ].join(" ")
      }
    >
      <Icon className="h-4 w-4" />
      {label}
    </NavLink>
  );
}

export default function AdminLayout() {
  const { isLoaded } = useAuth();

  return (
    <div className="relative min-h-[calc(100vh-3.5rem)] overflow-x-hidden bg-[#07111f] text-white">
      <div
        aria-hidden="true"
        className="absolute inset-0"
        style={{
          backgroundImage: `
            radial-gradient(1200px 720px at 15% 10%, rgba(72,160,220,0.14), transparent 56%),
            radial-gradient(900px 620px at 82% 18%, rgba(52,211,203,0.08), transparent 58%),
            linear-gradient(to bottom, rgba(6,12,24,0.82), rgba(6,12,24,0.96))
          `,
        }}
      />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(7,17,31,0.2),rgba(7,17,31,0.62))]" />

      <div className="relative mx-auto max-w-[1500px] px-4 pb-5 pt-[4.5rem] md:px-5 md:pb-6 md:pt-[4.75rem]">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-[1.6rem] border border-white/10 bg-[#0b1526]/88 px-5 py-3 shadow-[0_18px_52px_rgba(0,0,0,0.22)]">
          <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-cyan-200/72">
            CartoSky Admin
          </div>
          <nav className="flex flex-wrap gap-2">
            <AdminNavItem to="/admin/overview" label="Overview" icon={Gauge} />
            <AdminNavItem to="/admin/analytics" label="Analytics" icon={BarChart3} />
            <AdminNavItem to="/admin/observability" label="Observability" icon={Activity} />
            <AdminNavItem to="/admin/traces" label="Traces" icon={Waypoints} />
            <AdminNavItem to="/admin/status" label="Pipeline Status" icon={ClipboardCheck} />
            <AdminNavItem to="/admin/feedback" label="Feedback" icon={MessageSquareText} />
          </nav>
        </div>

        <main className="min-w-0">
          {isLoaded ? (
            <Outlet />
          ) : (
            <SiteLoadingOverlay visible label="Loading admin session" />
          )}
        </main>
      </div>
    </div>
  );
}
