/**
 * Hover intent for the map sample readout.
 *
 * WHY THIS EXISTS. Operator report (Globe v1.1): click-dragging the globe to
 * spin it pops the sample tooltip, and it can stick for the whole pan. Two
 * separate mechanisms are needed, and both belong in ONE place so the flat map
 * and the globe — and the viewer and the compare panes, which share the same
 * `MapCanvas` hover handler — cannot drift apart:
 *
 *   (a) DRAG SUPPRESSION. While the pointer is down on the map, or the map
 *       reports itself as moving, nothing is sampled and nothing is shown, and
 *       anything already visible is hidden at once. Mid-drag hover is never
 *       intent. The map-moving half deliberately outlives the pointer release
 *       so drag inertia (and programmatic ease/fly) is covered too.
 *   (b) STATIONARY DWELL. The readout first appears only once the cursor has
 *       held still — within `HOVER_DWELL_TOLERANCE_PX` — for `HOVER_DWELL_MS`.
 *       Once it is up it tracks the cursor live with no re-dwell, so ordinary
 *       exploration stays fluid.
 *
 * SUPPRESSION IS UPSTREAM OF SAMPLING, not of rendering: the caller hands this
 * class a thunk that performs the hover callback, and a suppressed or
 * pre-dwell move never runs it. No request is issued for a hover that was
 * never intent, which is what the e2e assertions pin (on the network, not on
 * tooltip visibility).
 *
 * Everything downstream is untouched: the globe disc gate runs BEFORE this,
 * longitude normalization and the sample LRU cache key run AFTER it, and the
 * city-label batch values never pass through here at all.
 *
 * No DOM and no MapLibre types, so the whole state machine is unit-testable
 * with mocked timers (`tests/unit/hover-intent.test.ts`).
 */

/**
 * How long the cursor must hold still before the readout appears.
 *
 * OPERATOR-TUNABLE. 150 ms is short enough that a deliberate point-and-read
 * feels immediate and long enough that a sweep across the map never lights it
 * up. Raise it if the readout still feels twitchy; lower it if pointing feels
 * laggy. It is the only number that governs first appearance.
 *
 * END-TO-END LATENCY IS NOT 150 ms. Measured at ~365 ms from the cursor
 * settling to the readout painting: this dwell, plus `use-sample-tooltip.ts`'s
 * own 80 ms DEBOUNCE_MS, plus the sample fetch and the React commit. Tune
 * against that number, not against this one.
 *
 * FLAGGED, NOT CHANGED: that 80 ms debounce predates the dwell and is now
 * arguably redundant — the dwell already establishes intent, and the debounce
 * only adds latency behind it. Removing it would take ~80 ms off the figure
 * above, but it is a separate behaviour change with its own e2e timing
 * exposure, so it is left for the operator to decide after feel-testing.
 */
export const HOVER_DWELL_MS = 150;

/**
 * Pixel radius that still counts as "stationary". A real hand holding a mouse
 * still jitters by 1-3 px; 4 px absorbs that without absorbing intent to move.
 * The same tolerance doubles as the per-event distance that counts as "fast"
 * once the readout is up (see `HOVER_FAST_MOVE_HIDE_MS`).
 */
export const HOVER_DWELL_TOLERANCE_PX = 4;

/**
 * HIDE RULE, stated once so it cannot be inferred wrongly from the code.
 *
 * A visible readout hides when ANY of these happens:
 *   1. a drag/map-move starts, or the pointer goes down;
 *   2. the pointer leaves the map (or, on the globe, leaves the disc) —
 *      the caller drives that through `leave()`;
 *   3. the cursor moves FAST — i.e. every pointer event for a continuous
 *      stretch longer than `HOVER_FAST_MOVE_HIDE_MS` steps more than
 *      `HOVER_DWELL_TOLERANCE_PX` — after which a fresh dwell must be earned.
 *
 * Rule 3 is what stops the readout trailing the cursor across the map, and the
 * "continuous" qualifier is what stops slow exploration from flickering: a
 * single big jump (or a coalesced event after a stall) resets nothing, and one
 * step back inside the tolerance clears the fast streak entirely. At the ~8-16
 * ms cadence browsers deliver pointer moves at, >4 px per event is ~250-500
 * px/s sustained for a tenth of a second — well above unhurried exploration
 * and well below a deliberate sweep.
 */
export const HOVER_FAST_MOVE_HIDE_MS = 100;

export type HoverIntentOptions<T> = {
  /** Perform the hover — in the app, invoke the callback thunk. */
  onShow: (payload: T) => void;
  /** Tear the readout down. Called at most once per show. */
  onHide: () => void;
  /**
   * The map's own "am I moving" signal (`map.isMoving()` in MapLibre, which
   * covers drag, wheel/pinch zoom, inertia and programmatic ease/fly).
   *
   * THIS IS THE ONLY SOURCE OF ONGOING-MOVEMENT SUPPRESSION, and it is polled
   * on every query. `mapMoveStart()` deliberately does NOT set a latch: a latch
   * that only `mapMoveEnd()` could clear would wedge hover off forever on an
   * unpaired `movestart` (MapLibre can emit one whose `moveend` is swallowed by
   * a torn-down animation). `movestart` is a one-shot "take it down now"; the
   * poll is what keeps it down for as long as the map is actually moving.
   */
  isMoving?: () => boolean;
  dwellMs?: number;
  tolerancePx?: number;
  fastMoveHideMs?: number;
  /** Injected for tests; default to the ambient timer/clock. */
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => number;
  clearTimer?: (id: number) => void;
};

export class HoverIntent<T> {
  private readonly options: Required<
    Pick<HoverIntentOptions<T>, "onShow" | "onHide" | "dwellMs" | "tolerancePx" | "fastMoveHideMs" | "now" | "setTimer" | "clearTimer">
  > & { isMoving?: () => boolean };

  private pointerIsDown = false;
  private visible = false;
  /** True once `onShow` has fired without a matching `onHide`. */
  private shown = false;
  private timerId: number | null = null;
  private payload: T | null = null;
  private anchorX = 0;
  private anchorY = 0;
  private hasAnchor = false;
  private lastX = 0;
  private lastY = 0;
  /** When the current unbroken run of above-tolerance steps began. */
  private fastSince: number | null = null;

  constructor(options: HoverIntentOptions<T>) {
    this.options = {
      onShow: options.onShow,
      onHide: options.onHide,
      isMoving: options.isMoving,
      dwellMs: options.dwellMs ?? HOVER_DWELL_MS,
      tolerancePx: options.tolerancePx ?? HOVER_DWELL_TOLERANCE_PX,
      fastMoveHideMs: options.fastMoveHideMs ?? HOVER_FAST_MOVE_HIDE_MS,
      now: options.now ?? (() => Date.now()),
      setTimer:
        options.setTimer ??
        ((fn, ms) => setTimeout(fn, ms) as unknown as number),
      clearTimer: options.clearTimer ?? ((id) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>)),
    };
  }

  /**
   * True while no hover may be sampled or shown.
   *
   * Two inputs, and deliberately NO third: the pointer-down flag, which has a
   * guaranteed pair (`pointerup`/`pointercancel` on the window, so a release
   * anywhere clears it), and the polled `isMoving()`. `movestart` is NOT
   * latched here — see `HoverIntentOptions.isMoving`. An earlier revision did
   * latch it and short-circuited the poll, which meant one unpaired
   * `movestart` disabled hover for the life of the map.
   */
  isSuppressed(): boolean {
    if (this.pointerIsDown) {
      return true;
    }
    try {
      return this.options.isMoving?.() === true;
    } catch {
      return false;
    }
  }

  /** Exposed for tests and for callers that want to assert the visible state. */
  isVisible(): boolean {
    return this.visible;
  }

  pointerDown(): void {
    this.pointerIsDown = true;
    this.cancel();
  }

  pointerUp(): void {
    this.pointerIsDown = false;
    // Deliberately does NOT re-show anything. If the release ended a drag the
    // map is still coasting and `isMoving()` keeps it suppressed; either way
    // the next move has to earn a fresh dwell.
  }

  /**
   * A camera move began: take the readout down NOW rather than waiting for the
   * next pointer event to notice. Whether hover STAYS down is then entirely the
   * `isMoving()` poll's business, so an unpaired `movestart` costs one hide and
   * nothing more.
   */
  mapMoveStart(): void {
    this.cancel();
  }

  /**
   * Kept as an explicit no-op so the caller's `moveend` wiring stays symmetric
   * and self-documenting. There is no latch to clear: releasing suppression is
   * the poll's job, which is what makes a missing `moveend` harmless.
   */
  mapMoveEnd(): void {
    // intentionally empty -- see mapMoveStart()
  }

  /** Pointer left the map, or (globe) left the disc. */
  leave(): void {
    this.cancel();
  }

  /**
   * Hide anything visible, drop any armed dwell, and forget the anchor.
   * Idempotent: `onHide` fires only when there is actually a shown readout to
   * take down, so repeated calls (an out-of-disc mouse sweep, say) do not spam
   * the sampling hook with aborts.
   */
  cancel(): void {
    this.clearTimer();
    this.visible = false;
    this.hasAnchor = false;
    this.fastSince = null;
    this.payload = null;
    if (this.shown) {
      this.shown = false;
      this.options.onHide();
    }
  }

  /**
   * Feed one pointer position. `payload` is what `onShow` receives — in the
   * app, a thunk that performs the hover callback for exactly this position.
   */
  pointerMove(x: number, y: number, payload: T): void {
    if (this.isSuppressed()) {
      this.cancel();
      return;
    }
    this.payload = payload;

    if (this.visible) {
      const stepped = this.distance(x, y, this.lastX, this.lastY) > this.options.tolerancePx;
      const now = this.options.now();
      if (stepped) {
        if (this.fastSince === null) {
          this.fastSince = now;
        } else if (now - this.fastSince > this.options.fastMoveHideMs) {
          // Rule 3: sustained fast movement. Take it down and re-arm a dwell
          // from here, so the readout comes back where the cursor settles.
          this.cancel();
          this.payload = payload;
          this.arm(x, y);
          this.lastX = x;
          this.lastY = y;
          return;
        }
      } else {
        this.fastSince = null;
      }
      this.lastX = x;
      this.lastY = y;
      this.emit(payload);
      return;
    }

    // Pre-dwell: restart the clock whenever the cursor leaves the tolerance
    // circle, keep it running while it stays inside. A slow drift therefore
    // still lands (the circle is left, the clock restarts from the new spot)
    // rather than being starved forever.
    if (!this.hasAnchor || this.distance(x, y, this.anchorX, this.anchorY) > this.options.tolerancePx) {
      this.arm(x, y);
    }
    this.lastX = x;
    this.lastY = y;
  }

  dispose(): void {
    this.clearTimer();
    this.visible = false;
    this.shown = false;
    this.payload = null;
  }

  private arm(x: number, y: number): void {
    this.clearTimer();
    this.anchorX = x;
    this.anchorY = y;
    this.hasAnchor = true;
    this.timerId = this.options.setTimer(() => {
      this.timerId = null;
      if (this.isSuppressed()) {
        return;
      }
      this.visible = true;
      this.fastSince = null;
      this.lastX = this.anchorX;
      this.lastY = this.anchorY;
      if (this.payload !== null) {
        this.emit(this.payload);
      }
    }, this.options.dwellMs);
  }

  private emit(payload: T): void {
    this.shown = true;
    this.options.onShow(payload);
  }

  private clearTimer(): void {
    if (this.timerId !== null) {
      this.options.clearTimer(this.timerId);
      this.timerId = null;
    }
  }

  private distance(ax: number, ay: number, bx: number, by: number): number {
    return Math.hypot(ax - bx, ay - by);
  }
}
