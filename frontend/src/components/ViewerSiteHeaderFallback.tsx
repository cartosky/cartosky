import { NavLink } from "react-router-dom";

import { BRAND_LOGO_SRC } from "@/lib/branding";

export function ViewerSiteHeaderFallback() {
  return (
    <header className="fixed inset-x-0 top-0 z-[80]">
      <div
        aria-hidden="true"
        className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
        style={{ willChange: "transform" }}
      />
      <div className="relative z-10 flex h-14 items-center gap-3 px-4 md:px-5">
        <NavLink to="/" className="flex shrink-0 items-center font-semibold tracking-tight text-white">
          <img
            src={BRAND_LOGO_SRC}
            alt="CartoSky"
            className="block h-12 w-auto max-w-none"
          />
        </NavLink>
      </div>
    </header>
  );
}
