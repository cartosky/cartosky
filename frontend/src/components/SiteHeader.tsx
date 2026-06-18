import { useEffect, useRef, useState } from "react";
import { Show, UserButton, useAuth } from "@clerk/react";
import { NavLink, useLocation } from "react-router-dom";

import { PrefetchNavLink, routePrefetchIntentHandlers } from "@/components/PrefetchLink";

import { BRAND_LOGO_SRC } from "@/lib/branding";
import { API_ORIGIN } from "@/lib/config";
import { clerkUserButtonProps } from "@/lib/clerk-appearance";
import { billingEnabled, pricingPreviewEnabled } from "@/lib/entitlements";
import { useFeedbackContext } from "@/lib/feedback-context";
import { clerkJwtTemplate } from "@/lib/admin-api";

type NavItemProps = {
  to: string;
  label: string;
  onClick?: () => void;
  className?: string;
};

function NavItem({ to, label, onClick, className }: NavItemProps) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      {...routePrefetchIntentHandlers(to)}
      className={({ isActive }) =>
        [
          "text-sm font-medium transition px-3 py-1.5 rounded-md",
          isActive ? "text-white bg-white/10" : "text-white/70 hover:text-white hover:bg-white/10",
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
  const [adminEnabled, setAdminEnabled] = useState(false);
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const { openFeedback } = useFeedbackContext();

  const isAppVariant = variant === "app";
  const isMarketingVariant = variant === "marketing";
  const showPricingNav = billingEnabled || pricingPreviewEnabled;
  const showAppNav = isAppVariant;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function loadAdminStatus() {
      if (!isLoaded || !isSignedIn) {
        setAdminEnabled(false);
        return;
      }

      try {
        const token = await getToken({ template: clerkJwtTemplate() });
        if (!token) {
          if (!cancelled) setAdminEnabled(false);
          return;
        }

        const response = await fetch(`${API_ORIGIN}/api/v4/auth/me`, {
          method: "GET",
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Admin auth check failed (${response.status})`);
        }
        const body = (await response.json()) as { is_admin?: boolean };
        if (!cancelled) setAdminEnabled(body.is_admin === true);
      } catch (error: unknown) {
        if ((error as { name?: string } | undefined)?.name === "AbortError") return;
        if (!cancelled) setAdminEnabled(false);
      }
    }

    void loadAdminStatus();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [getToken, isLoaded, isSignedIn]);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    function onPointerDown(event: MouseEvent | TouchEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest('[class*="cl-userButtonPopover"]')) return;
      setMobileMenuOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileMenuOpen(false);
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
    <header className="fixed inset-x-0 top-0 z-[80]">
      <div
        aria-hidden="true"
        className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
        style={{ willChange: "transform" }}
      />
      <div
        className={
          isAppVariant
            ? "relative z-10 flex h-14 items-center gap-3 px-4 md:px-5"
            : "relative z-10 mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 md:gap-6 md:px-8"
        }
      >
        <NavLink to="/" className="flex shrink-0 items-center font-semibold tracking-tight text-white">
          <img
            src={BRAND_LOGO_SRC}
            alt="CartoSky"
            className="block h-12 w-auto max-w-none"
          />
        </NavLink>

        {isMarketingVariant ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <PrefetchNavLink
              to="/viewer"
              className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3.5 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)] transition duration-150 hover:brightness-105"
            >
              Viewer
            </PrefetchNavLink>
            <NavItem to="/forecast" label="Forecast" className="ml-2 text-white/72 hover:text-white" />
            <NavItem to="/climate" label="Climate" className="ml-0 text-white/72 hover:text-white" />
            {showPricingNav ? <NavItem to="/pricing" label="Pricing" /> : null}
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
            <button
              type="button"
              onClick={openFeedback}
              title="Send feedback"
              aria-label="Send feedback"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-white/70 transition hover:bg-white/10 hover:text-white"
            >
              Feedback
            </button>
            <Show when="signed-out">
              <NavLink
                to="/login"
                className="ml-3 rounded-lg px-2 py-2 text-sm text-white/62 transition duration-150 hover:text-white/88"
              >
                Login
              </NavLink>
            </Show>
            <Show when="signed-in">
              <div className="ml-3 flex h-9 items-center">
                <UserButton {...clerkUserButtonProps} />
              </div>
            </Show>
          </nav>
        ) : null}

        {showAppNav ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavItem to="/viewer" label="Viewer" />
            {showPricingNav ? <NavItem to="/pricing" label="Pricing" /> : null}
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
            <button
              type="button"
              onClick={openFeedback}
              title="Send feedback"
              aria-label="Send feedback"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-white/70 transition hover:bg-white/10 hover:text-white"
            >
              Feedback
            </button>
          </nav>
        ) : null}

        {isMarketingVariant ? (
          <div className="ml-auto flex items-center gap-2 md:hidden" ref={menuRef}>
            <PrefetchNavLink
              to="/viewer"
              className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)]"
            >
              Viewer
            </PrefetchNavLink>
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
                  <NavItem to="/viewer" label="Viewer" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  <NavItem to="/forecast" label="Forecast" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  <NavItem to="/climate" label="Climate" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  {showPricingNav ? (
                    <NavItem to="/pricing" label="Pricing" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  ) : null}
                  {adminEnabled ? (
                    <NavItem to="/admin" label="Admin" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  ) : null}
                  <button
                    type="button"
                    onClick={() => { setMobileMenuOpen(false); openFeedback(); }}
                    className="flex w-full items-center rounded-md px-3 py-1.5 text-left text-sm font-medium text-white/90 transition hover:bg-white/10 hover:text-white"
                  >
                    Feedback
                  </button>
                  <Show when="signed-out">
                    <NavItem to="/login" label="Login" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  </Show>
                  <Show when="signed-in">
                    <div className="flex items-center justify-between rounded-md px-3 py-2">
                      <PrefetchNavLink
                        to="/account"
                        onClick={() => setMobileMenuOpen(false)}
                        className="text-sm font-medium text-white/90 transition hover:text-white"
                      >
                        Account
                      </PrefetchNavLink>
                      <UserButton {...clerkUserButtonProps} />
                    </div>
                  </Show>
                </div>
              </nav>
            ) : null}
          </div>
        ) : null}
      </div>
    </header>
  );
}
