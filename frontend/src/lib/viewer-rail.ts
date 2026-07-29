/**
 * Rail state model — map viewer redesign Phase 6 (§6.3 / §6.4).
 *
 * The default expanded/collapsed state is computed from the width the map
 * would have with the rail expanded, never from the raw viewport width, so a
 * 1400 px laptop and a 1400 px browser window on a monitor behave the same.
 * A user override is persisted per breakpoint class, so a laptop preference
 * never follows the user to an external display.
 *
 * IMPORTANT: `index.html` inlines the same constants, the same formula and the
 * same storage keys for the pre-bundle boot shell. Any change here must be
 * mirrored there (search for `viewer-rail.ts` in index.html).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { readRailModePreference, writeRailModePreference } from "@/lib/app-utils";
import type { TimeAxisMode } from "@/lib/time-axis";

export const RAIL_EXPANDED_PX = 288;
export const RAIL_COLLAPSED_PX = 72;
/** §6.4: below this the mobile sheet owns the chrome (Phase 8 territory). */
export const RAIL_MOBILE_MAX_WIDTH = 767;
/** §6.4: expanded when the map still gets at least this much width. */
export const RAIL_WIDE_MIN_MAP_WIDTH = 1024;
/** Named per the §6.4 formula; currently nothing else eats horizontal space. */
export const RAIL_MAP_INSETS_PX = 0;

export type RailBreakpointClass = "wide" | "narrow";
export type RailState = "expanded" | "collapsed";
/** Phase 7 (§6.3 / §7.2): the collapsed rail's third item targets the legend. */
export type RailSection = "source" | "view" | "legend";

export const RAIL_MODE_STORAGE_KEYS: Record<RailBreakpointClass, string> = {
  wide: "twf.rail.mode.wide",
  narrow: "twf.rail.mode.narrow",
};

/** availableMapWidth = viewportWidth − 288 − mapInsets (expanded width, always). */
export function availableMapWidth(viewportWidth: number): number {
  return viewportWidth - RAIL_EXPANDED_PX - RAIL_MAP_INSETS_PX;
}

export function railBreakpointClass(viewportWidth: number): RailBreakpointClass {
  return availableMapWidth(viewportWidth) >= RAIL_WIDE_MIN_MAP_WIDTH ? "wide" : "narrow";
}

export function railDefaultState(breakpointClass: RailBreakpointClass): RailState {
  return breakpointClass === "wide" ? "expanded" : "collapsed";
}

export function railWidthPx(state: RailState): number {
  return state === "expanded" ? RAIL_EXPANDED_PX : RAIL_COLLAPSED_PX;
}

/**
 * SOURCE availability line (§5.4 / §6.2). Reuses the Phase 5 truth inputs — the
 * `ready_through_fh` / `expected_max_fh` scalars — so "hours available" can
 * only be spoken when the run is actually complete (§9 acceptance grep).
 */
export function railAvailabilityLine(params: {
  mode: TimeAxisMode;
  frames: number[];
  /** `undefined` = manifest predates the scalar (legacy); `null` = nothing ready. */
  readyThroughFh?: number | null;
  expectedMaxFh?: number | null;
}): string | null {
  const { mode, readyThroughFh, expectedMaxFh } = params;
  const frames = Array.from(new Set(params.frames.filter(Number.isFinite))).sort((a, b) => a - b);

  if (mode === "observed") {
    return frames.length > 0 ? `${frames.length} frames observed` : "No observations loaded";
  }
  if (mode === "valid") {
    return frames.length > 0 ? "Outlook period published" : null;
  }
  if (frames.length === 0) {
    return "No frames yet";
  }
  const first = frames[0];
  const last = frames[frames.length - 1];
  if (readyThroughFh === undefined) {
    return `${frames.length} frames · FH ${first}–${last}`;
  }
  const horizon = Number.isFinite(expectedMaxFh ?? Number.NaN) ? Number(expectedMaxFh) : null;
  if (readyThroughFh === null) {
    return horizon !== null ? `Building to FH ${horizon}` : "No frames ready yet";
  }
  if (horizon !== null && horizon > readyThroughFh) {
    return `Building · ready through FH ${readyThroughFh} of ${horizon}`;
  }
  return `${readyThroughFh} hours available`;
}

/** `Updated 18 min ago` from `RunManifestResponse.last_updated` (§6.2). */
export function railFreshnessLabel(iso: string | null | undefined, nowMs = Date.now()): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return null;
  const minutes = Math.floor(Math.max(0, nowMs - parsed) / 60_000);
  if (minutes < 1) return "Updated just now";
  if (minutes < 60) return `Updated ${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Updated ${hours} hr ago`;
  return `Updated ${Math.floor(hours / 24)} d ago`;
}

export type ViewerRailStateReturn = {
  breakpointClass: RailBreakpointClass;
  state: RailState;
  isExpanded: boolean;
  widthPx: number;
  toggle: () => void;
  /**
   * `persist: false` updates the session state without writing the
   * localStorage override — for programmatic expansion (e.g. the tour),
   * which is not a user preference (§6.4).
   */
  expandTo: (section: RailSection, opts?: { persist?: boolean }) => void;
  /** Section the last `expandTo` asked for; cleared once the rail scrolls to it. */
  pendingSection: RailSection | null;
  clearPendingSection: () => void;
};

type Overrides = Record<RailBreakpointClass, RailState | null>;

function readOverrides(): Overrides {
  return {
    wide: readRailModePreference("wide"),
    narrow: readRailModePreference("narrow"),
  };
}

function currentViewportWidth(): number {
  return typeof window === "undefined" ? 1280 : window.innerWidth;
}

export function useViewerRailState(): ViewerRailStateReturn {
  const [breakpointClass, setBreakpointClass] = useState<RailBreakpointClass>(
    () => railBreakpointClass(currentViewportWidth()),
  );
  const [overrides, setOverrides] = useState<Overrides>(() => readOverrides());
  const [pendingSection, setPendingSection] = useState<RailSection | null>(null);

  // Same listener shape as useViewerLayoutMode so the two never disagree.
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const update = () => setBreakpointClass(railBreakpointClass(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);

  const state = overrides[breakpointClass] ?? railDefaultState(breakpointClass);

  const setState = useCallback((next: RailState, persist = true) => {
    if (persist) {
      // A toggle writes only the current class's key. Written outside the
      // updater so StrictMode's double-invoke never repeats the side effect.
      writeRailModePreference(breakpointClass, next);
    }
    setOverrides((current) => ({ ...current, [breakpointClass]: next }));
  }, [breakpointClass]);

  const toggle = useCallback(() => {
    setState(state === "expanded" ? "collapsed" : "expanded");
  }, [setState, state]);

  const expandTo = useCallback((section: RailSection, opts?: { persist?: boolean }) => {
    setState("expanded", opts?.persist ?? true);
    setPendingSection(section);
  }, [setState]);

  const clearPendingSection = useCallback(() => setPendingSection(null), []);

  return useMemo(() => ({
    breakpointClass,
    state,
    isExpanded: state === "expanded",
    widthPx: railWidthPx(state),
    toggle,
    expandTo,
    pendingSection,
    clearPendingSection,
  }), [breakpointClass, clearPendingSection, expandTo, pendingSection, state, toggle]);
}
