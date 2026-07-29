import { NavLink } from "react-router-dom";

import { BRAND_LOGO_SRC } from "@/lib/branding";

/**
 * Static viewer chrome backdrop: AppLayout paints it under the real viewer
 * header, and App's Suspense boundary uses it while the lazy chunk loads.
 *
 * Its geometry comes from the Phase 6 seam (`--viewer-topbar-height`,
 * `--viewer-rail-width`, `data-viewer-rail`), which index.html resolves before
 * the bundle and App keeps up to date — so boot shell → fallback → React hand
 * off without a jump. Outside the viewer (e.g. /compare) nothing is set and the
 * component renders exactly what it always did: a 56 px header, no rail.
 */
export function ViewerSiteHeaderFallback() {
  return (
    <>
      <header className="fixed inset-x-0 top-0 z-[80]">
        <div
          aria-hidden="true"
          className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
          style={{ willChange: "transform" }}
        />
        <div className="viewer-chrome-fallback-row relative z-10 flex items-center gap-3 px-4 md:px-5">
          <NavLink to="/" className="flex min-h-8 shrink-0 items-center font-semibold tracking-tight text-white pointer-coarse:min-h-11">
            <img
              src={BRAND_LOGO_SRC}
              alt="CartoSky"
              className="viewer-chrome-fallback-logo block w-auto max-w-none"
            />
          </NavLink>
        </div>
      </header>
      <div
        aria-hidden="true"
        className="viewer-chrome-fallback-rail fixed bottom-0 left-0 z-[76] border-r border-[#1a3a5c]/60 bg-[#030e1a]/[0.92] backdrop-blur-md"
      />
    </>
  );
}
