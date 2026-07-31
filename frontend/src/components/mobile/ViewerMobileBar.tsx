import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@clerk/react";
import {
  GitCompareArrows,
  Info,
  MessageSquareText,
  MoreHorizontal,
  PlayCircle,
  Share2,
  TrendingUp,
  UserRound,
} from "lucide-react";

import { AttributionDialog } from "@/components/ViewerTopBar";
import { BRAND_LOGO_SRC } from "@/lib/branding";
import { MOBILE_BAR_PX } from "@/lib/viewer-mobile";
import { cn } from "@/lib/utils";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

/**
 * Phase 8 mobile bar (§8 State A): 52 px — logo, destination links, Share,
 * and the overflow menu.
 *
 * Decision 3: Share moves here, labeled, out of the bottom controls row. The
 * `•••` overflow carries Send feedback, Compare, Replay tour, Attribution and
 * Sign in — deliberately NOT Keyboard shortcuts: the keyboard-centric
 * ShortcutSheet is meaningless on touch. That is the one §6.1-vs-mobile
 * divergence and it is recorded in the plan.
 *
 * Every target is 44 px inside the 52 px bar; nothing here owns viewer state.
 */
const BAR_ICON_BUTTON_CLASSNAME =
  "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[9px] border border-white/[0.09] bg-white/[0.05] text-white/70 transition-[background,color,border-color] duration-100 hover:border-white/18 hover:bg-white/[0.09] hover:text-white";

const BAR_MENU_ITEM_CLASSNAME =
  "flex min-h-11 w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] font-medium text-white/82 transition-colors hover:bg-cyan-300/12 hover:text-cyan-50";

const PANEL_CLASSNAME =
  "overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.92] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md";

const SECTION_LINKS = [
  { to: "/viewer", label: "Viewer" },
  { to: "/forecast", label: "Forecast" },
  { to: "/climate", label: "Climate" },
] as const;

export function ViewerMobileBar() {
  const toolbar = useViewerToolbar();
  const navigate = useNavigate();
  const { isLoaded, isSignedIn } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [attributionOpen, setAttributionOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuTop, setMenuTop] = useState(MOBILE_BAR_PX + 8);

  useEffect(() => {
    if (!menuOpen) return undefined;
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
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", place);
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
    <div
      data-testid="viewer-mobile-bar"
      className="relative z-10 flex items-center gap-1 px-2"
      style={{ height: `${MOBILE_BAR_PX}px` }}
    >
      <NavLink to="/" className="flex h-11 shrink-0 items-center font-semibold tracking-tight text-white">
        <img src={BRAND_LOGO_SRC} alt="CartoSky" className="block h-10 w-auto max-w-none" />
      </NavLink>

      <nav
        aria-label="Product sections"
        data-testid="viewer-mobile-section-switcher"
        className="flex min-w-0 flex-1 items-stretch self-stretch"
      >
        {SECTION_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) => cn(
              "relative inline-flex min-h-11 min-w-11 flex-1 items-center justify-center border-b-2 px-1 font-sans text-[11px] font-medium transition-colors",
              isActive
                ? "border-cyan-300/80 text-cyan-100"
                : "border-transparent text-white/58 hover:text-white/90",
            )}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex shrink-0 items-center gap-1">
        {toolbar?.onShare ? (
          <button
            type="button"
            onClick={toolbar.onShare}
            data-tour-target="share-button"
            className="inline-flex h-11 items-center gap-1 rounded-[9px] border border-cyan-300/22 bg-cyan-300/[0.10] px-2 text-[12px] font-semibold text-cyan-100 transition-colors duration-100 hover:bg-cyan-300/[0.16]"
          >
            <Share2 className="h-3.5 w-3.5" />
            Share
          </button>
        ) : null}

        <button
          ref={triggerRef}
          type="button"
          data-testid="mobile-bar-overflow-trigger"
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
      </div>

      {menuOpen ? createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label="More viewer actions"
          data-testid="mobile-bar-overflow-menu"
          className={cn(PANEL_CLASSNAME, "fixed right-3 z-[90] w-[232px] p-1.5")}
          style={{ top: menuTop }}
        >
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => toolbar?.onFeedback?.())}>
            <MessageSquareText className="h-4 w-4 text-white/54" />
            Send feedback
          </button>
          <button
            type="button"
            role="menuitem"
            className={BAR_MENU_ITEM_CLASSNAME}
            onClick={runItem(() => navigate(toolbar?.compareHref ?? "/compare"))}
          >
            <GitCompareArrows className="h-4 w-4 text-white/54" />
            Compare
          </button>
          {toolbar?.onSoundingModeToggle ? (
            <button
              type="button"
              role="menuitem"
              data-testid="sounding-toggle"
              aria-pressed={toolbar.soundingMode === true}
              disabled={toolbar.soundingAvailable === false}
              className={cn(
                BAR_MENU_ITEM_CLASSNAME,
                toolbar.soundingMode && "bg-cyan-300/12 text-cyan-50",
                toolbar.soundingAvailable === false && "cursor-not-allowed opacity-40",
              )}
              onClick={runItem(() => toolbar?.onSoundingModeToggle?.())}
            >
              <TrendingUp className="h-4 w-4 text-white/54" />
              {toolbar.soundingAvailable === false ? "Sounding (HRRR only)" : "Sounding"}
            </button>
          ) : null}
          <button type="button" role="menuitem" className={BAR_MENU_ITEM_CLASSNAME} onClick={runItem(() => toolbar?.onReplayTour?.())}>
            <PlayCircle className="h-4 w-4 text-white/54" />
            Replay tour
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

export default ViewerMobileBar;
