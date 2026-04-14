import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { BRAND_LOGO_SRC } from "@/lib/branding";

type NavItemProps = {
  to: string;
  label: string;
  onClick?: () => void;
  className?: string;
};

type TwfStatus =
  | { linked: false; admin?: boolean }
  | { linked: true; admin?: boolean; member_id: number; display_name: string; photo_url?: string | null };

function getApiBase(): string {
  const fromEnv = (import.meta as any)?.env?.VITE_API_BASE as string | undefined;
  const base = (fromEnv ?? "https://api.cartosky.com").trim();
  return base.replace(/\/$/, "");
}

function NavItem({ to, label, onClick, className }: NavItemProps) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        [
          "text-sm font-medium transition px-3 py-1.5 rounded-md",
          isActive
            ? "text-white bg-white/10"
            : "text-white/70 hover:text-white hover:bg-white/10",
          className ?? "",
        ].join(" ")
      }
    >
      {label}
    </NavLink>
  );
}

export default function SiteHeader({ variant }: { variant: "marketing" | "app" }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [twfStatus, setTwfStatus] = useState<TwfStatus>({ linked: false });
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const isAppVariant = variant === "app";
  const isMarketingVariant = variant === "marketing";
  const showAppNav = isAppVariant && location.pathname !== "/viewer";
  const logoClassName = isMarketingVariant
    ? "block h-12 w-auto max-w-none"
    : "block h-12 w-auto max-w-none";
  const accountLabel = twfStatus.linked ? twfStatus.display_name : "Login";
  const accountPhotoUrl = twfStatus.linked ? twfStatus.photo_url : null;
  const adminEnabled = twfStatus.admin === true;
  const referenceNavClassName =
    "text-xs font-medium uppercase tracking-[0.18em] text-white/54 hover:text-white/82 hover:bg-white/[0.06]";

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${getApiBase()}/auth/twf/status`, {
      method: "GET",
      credentials: "include",
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) {
          throw new Error(`Status request failed (${r.status})`);
        }
        return (await r.json()) as TwfStatus;
      })
      .then((status) => setTwfStatus(status))
      .catch((e: unknown) => {
        if ((e as any)?.name === "AbortError") return;
        setTwfStatus({ linked: false });
      });

    return () => controller.abort();
  }, []);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileMenuOpen) {
      return;
    }

    function onPointerDown(event: MouseEvent | TouchEvent) {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (menuRef.current?.contains(target)) {
        return;
      }
      setMobileMenuOpen(false);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMobileMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [mobileMenuOpen]);

  return (
    <header className="sticky top-0 z-[60] border-b border-white/8 bg-[#08111f]/78 backdrop-blur-2xl">
      <div
        className={
          isAppVariant
            ? "flex h-14 items-center gap-3 px-4 md:px-5"
            : "mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 md:gap-6 md:px-8"
        }
      >
        <NavLink to="/" className="flex shrink-0 items-center font-semibold tracking-tight text-white">
          <img src={BRAND_LOGO_SRC} alt="CartoSky" className={logoClassName} />
        </NavLink>

        {isMarketingVariant ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavLink
              to="/viewer"
              className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3.5 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)] transition duration-150 hover:brightness-105"
            >
              Viewer
            </NavLink>
            <NavItem to="/forecast" label="Forecast" className="text-white/82 hover:text-white" />
            <NavItem to="/models" label="Models" className={referenceNavClassName} />
            <NavItem to="/variables" label="Variables" className={referenceNavClassName} />
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
            <NavLink
              to="/login"
              className="ml-2 rounded-lg px-3 py-2 text-sm text-white/72 transition duration-150 hover:text-white"
            >
              <span className="inline-flex items-center gap-2">
                {accountPhotoUrl ? (
                  <img src={accountPhotoUrl} alt="" className="h-5 w-5 rounded-full object-cover" />
                ) : null}
                <span>{accountLabel}</span>
              </span>
            </NavLink>
          </nav>
        ) : null}

        {showAppNav ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavItem to="/viewer" label="Viewer" />
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
          </nav>
        ) : null}

        {isMarketingVariant ? (
          <div className="ml-auto flex items-center gap-2 md:hidden" ref={menuRef}>
          <NavLink
            to="/viewer"
            className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)]"
          >
            Viewer
          </NavLink>
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-white/15 bg-white/5 text-white hover:bg-white/10"
            aria-label="Open menu"
            aria-expanded={mobileMenuOpen}
            aria-controls="mobile-site-nav"
            onClick={() => setMobileMenuOpen((open) => !open)}
          >
            <span className="sr-only">{mobileMenuOpen ? "Close menu" : "Open menu"}</span>
            <span className="flex w-4 flex-col gap-1.5">
              <span className="block h-0.5 w-4 rounded bg-current" />
              <span className="block h-0.5 w-4 rounded bg-current" />
              <span className="block h-0.5 w-4 rounded bg-current" />
            </span>
          </button>

          {mobileMenuOpen ? (
            <nav
              id="mobile-site-nav"
              className="absolute right-0 top-[calc(100%+0.5rem)] z-[70] w-[min(92vw,360px)] rounded-2xl border border-white/15 bg-black/90 p-2.5 text-white shadow-[0_20px_52px_rgba(0,0,0,0.72)] backdrop-blur-xl"
              aria-label="Site navigation"
            >
              <div className="flex flex-col gap-1">
                <NavItem
                  to="/viewer"
                  label="Viewer"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-white/90 hover:text-white"
                />
                <NavItem
                  to="/forecast"
                  label="Forecast"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-white/90 hover:text-white"
                />
                <NavItem
                  to="/models"
                  label="Models"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-white/90 hover:text-white"
                />
                <NavItem
                  to="/variables"
                  label="Variables"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-white/90 hover:text-white"
                />
                {adminEnabled ? (
                  <NavItem
                    to="/admin"
                    label="Admin"
                    onClick={() => setMobileMenuOpen(false)}
                    className="text-white/90 hover:text-white"
                  />
                ) : null}
                <div className="my-1 h-px bg-white/10" />
                <NavItem
                  to="/login"
                  label={accountLabel}
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-white/90 hover:text-white"
                />
              </div>
            </nav>
          ) : null}
          </div>
        ) : null}
      </div>
    </header>
  );
}
