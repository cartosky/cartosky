/**
 * Mobile chrome geometry — map viewer redesign Phase 8 (§8).
 *
 * One place owns the three-state numbers so the map slot, the timeline row,
 * the sheet and the tests can never disagree. Everything here is pure: the
 * components read it, nothing here reads the DOM.
 *
 * The load-bearing invariant is that the map element's bottom inset
 * (timeline + peek) is CONSTANT across all three states — the sheet, the day
 * strip, the readout, the badge and the chip are overlays. That is what makes
 * the §9 "map height unchanged through the drag" contract hold by
 * construction rather than by animation timing.
 */

/** §8 State A: the top bar. */
export const MOBILE_BAR_PX = 52;
/** §8 State A: the one-row timeline (TimelineTrack's existing compact-64). */
export const MOBILE_TIMELINE_PX = 64;
/** §8 State A: the sheet peek — run state only. */
export const MOBILE_PEEK_PX = 84;
/** §8: "shrink the sheet peek first, then the timeline" — the floors. */
const MOBILE_PEEK_MIN_PX = 64;
const MOBILE_TIMELINE_MIN_PX = 48;
/** §8: the map is at least this fraction of the viewport at rest. */
export const MOBILE_MAP_MIN_FRACTION = 0.72;
/** §8 State C: the map strip above the full snap never drops below this. */
export const MOBILE_MAP_MIN_VISIBLE_PX = 180;
/** §8 State C snap fractions. */
const MOBILE_HALF_FRACTION = 0.5;
const MOBILE_FULL_FRACTION = 0.62;
/** §8 State B: the overlays revert this long after pointerup. */
export const MOBILE_SCRUB_REVERT_MS = 400;

/** The sheet is never "closed" on the viewer — State A includes the peek. */
export type MobileSheetSnap = "peek" | "half" | "full";
/** Rail order (§8 State C): Source → View → Legend. */
export type MobileSheetSection = "source" | "view" | "legend";

/**
 * A one-shot request to expand the sheet and reveal a section — raised by the
 * bar's ⌕ and by the tour. `token` makes repeat requests distinguishable
 * without the consumer having to diff object identity.
 */
export type MobileSheetRequest = {
  section: MobileSheetSection;
  /** ⌕ (decision 2): open the region row's existing location search. */
  focusSearch?: boolean;
  token: number;
};

export type MobileChromeGeometry = {
  timelinePx: number;
  peekPx: number;
  /** timeline + peek — the map element's constant bottom inset. */
  bottomInsetPx: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * §8: 72% is a floor, not a target. At 390×844 this returns the documented
 * 64/84/148; on a viewport short enough that 148 px would push the map under
 * 72%, the peek shrinks first and the timeline second.
 *
 * The shrunken values are FLOORED, never rounded: the budget is a spend cap,
 * and rounding a fractional allowance up overspends it by a pixel — which is
 * exactly enough to break the floor it was computed to protect (at H = 660 the
 * peek allowance is 68.8 px; rounding to 69 yields a 0.7197 map fraction,
 * flooring to 68 yields 0.7212).
 *
 * HARD MINIMUM COLLISION — the floor is not reachable on every viewport. The
 * chrome cannot shrink below
 *
 *   MOBILE_BAR_PX + MOBILE_PEEK_MIN_PX + MOBILE_TIMELINE_MIN_PX
 *     = 52 + 64 + 48 = 164 px
 *
 * so the 72 % map fraction requires
 *
 *   164 <= viewportHeight * (1 - MOBILE_MAP_MIN_FRACTION)
 *   viewportHeight >= 164 / 0.28 ≈ 585.7 px
 *
 * Below roughly 586 px of viewport the two constraints are mutually
 * exclusive and the MINIMUMS WIN: the map lands under 72 % and the peek and
 * timeline stay at 64 / 48. That is deliberate — §8's stated priority on short
 * viewports is that the controls remain usable (44 px targets inside a 48 px
 * timeline row), not that a map-fraction number is preserved by shrinking the
 * chrome into unusability.
 */
export function mobileChromeGeometry(viewportHeight: number): MobileChromeGeometry {
  const budget = viewportHeight * (1 - MOBILE_MAP_MIN_FRACTION) - MOBILE_BAR_PX;
  let peekPx = MOBILE_PEEK_PX;
  let timelinePx = MOBILE_TIMELINE_PX;
  if (peekPx + timelinePx > budget) {
    peekPx = Math.floor(clamp(budget - timelinePx, MOBILE_PEEK_MIN_PX, MOBILE_PEEK_PX));
  }
  if (peekPx + timelinePx > budget) {
    timelinePx = Math.floor(clamp(budget - peekPx, MOBILE_TIMELINE_MIN_PX, MOBILE_TIMELINE_PX));
  }
  return { timelinePx, peekPx, bottomInsetPx: timelinePx + peekPx };
}

/**
 * Sheet height for a snap. The full snap is clamped so §8's ≥180 px of map
 * survives on short viewports — the clamp is live, not dead code: it binds
 * below roughly a 635 px viewport.
 */
export function mobileSheetHeightPx(
  snap: MobileSheetSnap,
  viewportHeight: number,
  peekPx: number,
): number {
  if (snap === "peek") {
    return peekPx;
  }
  const ceiling = viewportHeight - MOBILE_BAR_PX - MOBILE_MAP_MIN_VISIBLE_PX;
  const fraction = snap === "full" ? MOBILE_FULL_FRACTION : MOBILE_HALF_FRACTION;
  return Math.round(clamp(viewportHeight * fraction, peekPx, Math.max(peekPx, ceiling)));
}

/** Handle taps cycle forward through the snaps (peek → half → full → peek). */
export function nextMobileSnap(snap: MobileSheetSnap): MobileSheetSnap {
  return snap === "peek" ? "half" : snap === "half" ? "full" : "peek";
}
