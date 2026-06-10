import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { BootstrapCompleteMarker } from "@/lib/bootstrap-loading";
import { MarketingRouteSuspenseFallback } from "@/components/route-suspense-fallbacks";
import SiteFooter from "../components/SiteFooter";
import SiteHeader from "../components/SiteHeader";

export default function MarketingLayout() {
  return (
    <div className="relative min-h-svh overflow-x-hidden bg-[#07111f] text-white">
      <div
        aria-hidden="true"
        className="absolute md:fixed inset-0 -z-10"
        style={{
          backgroundImage: `
            radial-gradient(900px 520px at 50% 0%, rgba(34,211,238,0.08), transparent 60%),
            radial-gradient(800px 600px at 12% 78%, rgba(37,99,235,0.08), transparent 65%),
            linear-gradient(180deg, rgba(7,17,31,1), rgba(8,18,34,1))
          `,
          backgroundSize: "auto",
          backgroundPosition: "center",
        }}
      />
      <div
        aria-hidden="true"
        className="absolute md:fixed inset-0 -z-10 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_45%,rgba(0,0,0,0.55)_100%)]"
      />

      <SiteHeader variant="marketing" />

      <main className="mx-auto max-w-6xl px-5 md:px-8 py-12 md:py-16">
        <Suspense fallback={<MarketingRouteSuspenseFallback />}>
          <Outlet />
          <BootstrapCompleteMarker />
        </Suspense>
      </main>

      <SiteFooter />
    </div>
  );
}
