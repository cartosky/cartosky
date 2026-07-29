import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useNavigate } from "react-router-dom";
import { Show, UserButton, useAuth } from "@clerk/react";
import {
  GitCompareArrows,
  Info,
  Keyboard,
  MessageSquareText,
  MoreHorizontal,
  PlayCircle,
  Share2,
  UserRound,
  X,
} from "lucide-react";

import { BRAND_LOGO_SRC } from "@/lib/branding";
import { clerkUserButtonProps } from "@/lib/clerk-appearance";
import { cn } from "@/lib/utils";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

/**
 * Phase 6 top bar (§6.1). 48 px tall: logo → `/`, independent destination
 * links, and — on the right — labeled Compare and Share actions, the `•••`
 * overflow, and the existing Clerk account affordance.
 * The center stays empty. Every selector moved into the rail; nothing in this
 * bar owns viewer state.
 *
 * `⌘K Jump to…` from §6.1 is deferred (plan decision 1): no palette/search
 * infrastructure exists and the §6 gate does not require it. No placeholder.
 */
const BAR_ICON_BUTTON_CLASSNAME =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[7px] border border-white/[0.09] bg-white/[0.05] text-white/62 transition-[background,color,border-color] duration-100 hover:border-white/18 hover:bg-white/[0.09] hover:text-white pointer-coarse:h-11 pointer-coarse:w-11";

const BAR_MENU_ITEM_CLASSNAME =
  "flex min-h-8 w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] font-medium text-white/82 transition-colors hover:bg-cyan-300/12 hover:text-cyan-50 pointer-coarse:min-h-11";

const PANEL_CLASSNAME =
  "overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.92] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md";

const SECTION_LINKS = [
  { to: "/viewer", label: "Viewer" },
  { to: "/forecast", label: "Forecast" },
  { to: "/climate", label: "Climate" },
] as const;

function AttributionDialog({ onClose }: { onClose: () => void }) {
  return createPortal(
    <>
      <div className="fixed inset-0 z-[95]" aria-hidden="true" onClick={onClose} />
      <div
        role="dialog"
        aria-label="Map attribution"
        className={cn(PANEL_CLASSNAME, "fixed right-4 top-[3.75rem] z-[96] w-[248px] px-4 py-3")}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-['IBM_Plex_Mono',monospace] text-[12px] font-medium uppercase tracking-[0.14em] text-cyan-300/60">
              Attribution
            </div>
            <div className="mt-0.5 text-[11px] text-white/52">Basemap &amp; tile sources</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close attribution"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-white/32 transition-colors hover:text-white/72 pointer-coarse:h-11 pointer-coarse:w-11"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1 text-[12px] leading-relaxed text-white/44">
          Maps:{" "}
          <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center justify-center rounded-md px-1.5 underline underline-offset-2 transition-colors hover:text-white/80 pointer-coarse:min-h-11 pointer-coarse:min-w-11">MapLibre</a>
          {" "}·{" "}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center justify-center rounded-md px-1.5 underline underline-offset-2 transition-colors hover:text-white/80 pointer-coarse:min-h-11 pointer-coarse:min-w-11">OSM</a>
          {" "}·{" "}
          <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center justify-center rounded-md px-1.5 underline underline-offset-2 transition-colors hover:text-white/80 pointer-coarse:min-h-11 pointer-coarse:min-w-11">CARTO</a>
        </div>
      </div>
    </>,
    document.body,
  );
}

export function ViewerTopBar() {
  const toolbar = useViewerToolbar();
  const navigate = useNavigate();
  const { isLoaded, isSignedIn } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [attributionOpen, setAttributionOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuTop, setMenuTop] = useState(56);

  useEffect(() => {
    if (!menuOpen) return;
    const place = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (rect) setMenuTop(rect.bottom + 8);
    };
    place();
    function onPointerDown(event: MouseEvent | TouchEvent) {
      if (!(event.target instanceof Node)) return;
      if (triggerRef.current?.contains(event.target)) return;
      if (menuRef.current?.contains(event.target)) return;
      setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const runItem = (action: () => void) => () => {
    setMenuOpen(false);
    action();
  };

  return (
    <div className="relative z-10 flex h-12 items-center gap-2 px-4 md:gap-3 md:px-5">
      {/* min-h-11 under a coarse pointer keeps the §2.1 44 px floor without
          growing the 48 px bar. The high-resolution lockup is rendered at
          36 px so its wordmark stays crisp without crowding the nav. */}
      <NavLink to="/" className="flex min-h-8 shrink-0 items-center font-semibold tracking-tight text-white pointer-coarse:min-h-11">
        <img src={BRAND_LOGO_SRC} alt="CartoSky" className="block h-9 w-auto max-w-none" />
      </NavLink>

      <nav
        aria-label="Product sections"
        data-testid="viewer-section-switcher"
        className="flex items-stretch gap-4 self-stretch"
      >
        {SECTION_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) => cn(
              "relative inline-flex min-h-8 items-center border-b-2 px-0.5 pt-0.5 text-[13px] font-medium transition-colors duration-100 pointer-coarse:min-h-11",
              isActive
                ? "border-cyan-300/80 text-cyan-100"
                : "border-transparent text-white/58 hover:text-white/90",
            )}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      {/* §6.1: the center stays empty. */}
      <div className="ml-auto flex items-center gap-1.5">
        <NavLink
          to={toolbar?.compareHref ?? "/compare"}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-[7px] border border-white/[0.11] bg-white/[0.045] px-3 text-[13px] font-semibold text-white/76 transition-colors duration-100 hover:border-white/20 hover:bg-white/[0.08] hover:text-white pointer-coarse:min-h-11"
        >
          <GitCompareArrows className="h-3.5 w-3.5" />
          Compare
        </NavLink>
        {toolbar?.onShare ? (
          <button
            type="button"
            onClick={toolbar.onShare}
            data-tour-target="share-button"
            className="inline-flex min-h-8 items-center gap-1.5 rounded-[7px] border border-cyan-300/22 bg-cyan-300/[0.10] px-3 text-[13px] font-semibold text-cyan-100 transition-colors duration-100 hover:bg-cyan-300/[0.16] pointer-coarse:min-h-11"
          >
            <Share2 className="h-3.5 w-3.5" />
            Share
          </button>
        ) : null}

        <button
          ref={triggerRef}
          type="button"
          data-testid="viewer-overflow-trigger"
          data-tour-target="feedback-button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="More"
          title="More"
          onClick={() => setMenuOpen((open) => !open)}
          className={cn(BAR_ICON_BUTTON_CLASSNAME, menuOpen && "border-cyan-300/25 bg-cyan-300/[0.12] text-cyan-200")}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>

        <Show when="signed-in">
          <div className="flex h-8 items-center pointer-coarse:h-11">
            <UserButton {...clerkUserButtonProps} />
          </div>
        </Show>
        <Show when="signed-out">
          <NavLink
            to="/login"
            aria-label="Sign in"
            title="Sign in"
            className={BAR_ICON_BUTTON_CLASSNAME}
          >
            <UserRound className="h-4 w-4" />
          </NavLink>
        </Show>
      </div>

      {menuOpen ? createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label="More viewer actions"
          data-testid="viewer-overflow-menu"
          className={cn(PANEL_CLASSNAME, "fixed right-4 z-[90] w-[232px] p-1.5")}
          style={{ top: menuTop }}
        >
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => toolbar?.onFeedback?.())}>
            <MessageSquareText className="h-4 w-4 text-white/54" />
            Send feedback
          </button>
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => toolbar?.onReplayTour?.())}>
            <PlayCircle className="h-4 w-4 text-white/54" />
            Replay tour
          </button>
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => toolbar?.onOpenShortcuts?.())}>
            <Keyboard className="h-4 w-4 text-white/54" />
            Keyboard shortcuts
          </button>
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => setAttributionOpen(true))}>
            <Info className="h-4 w-4 text-white/54" />
            Attribution
          </button>
          <div aria-hidden="true" className="my-1 h-px bg-white/[0.08]" />
          <button
            type="button"
            role="menuitem"
            className={BAR_MENU_ITEM_CLASSNAME}
            onClick={runItem(() => navigate(isLoaded && isSignedIn ? "/account" : "/login"))}
          >
            <UserRound className="h-4 w-4 text-white/54" />
            {isLoaded && isSignedIn ? "Account" : "Sign in"}
          </button>
        </div>,
        document.body,
      ) : null}

      {attributionOpen ? <AttributionDialog onClose={() => setAttributionOpen(false)} /> : null}
    </div>
  );
}
