/**
 * Hover-intent state machine (Globe v1.1).
 *
 * The operator symptom was "click-dragging the globe pops the tooltip and it
 * sticks until the pan ends". These tests pin the two mechanisms that fix it —
 * drag suppression and stationary dwell — as a pure state machine, with the
 * clock and the timer injected so nothing here waits on real time.
 *
 * The load-bearing assertion throughout is on `onShow` calls, NOT on a
 * `isVisible()` flag: `onShow` is the thunk that reaches the sampling hook, so
 * "onShow was never called" is exactly "no request was issued".
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  HOVER_DWELL_MS,
  HOVER_DWELL_TOLERANCE_PX,
  HOVER_FAST_MOVE_HIDE_MS,
  HoverIntent,
} from "@/lib/hover-intent";

/** A hand-driven clock + timer queue, so every test states its own timing. */
function makeHarness(overrides: { isMoving?: () => boolean } = {}) {
  let now = 1_000;
  let nextId = 1;
  const timers = new Map<number, { fireAt: number; fn: () => void }>();
  const shown: string[] = [];
  let hides = 0;

  const intent = new HoverIntent<string>({
    onShow: (payload) => shown.push(payload),
    onHide: () => {
      hides += 1;
    },
    isMoving: overrides.isMoving,
    now: () => now,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.set(id, { fireAt: now + ms, fn });
      return id;
    },
    clearTimer: (id) => {
      timers.delete(id);
    },
  });

  /** Advance the clock, firing every timer that comes due. */
  const advance = (ms: number) => {
    const target = now + ms;
    for (;;) {
      let dueId: number | null = null;
      let dueAt = Number.POSITIVE_INFINITY;
      for (const [id, timer] of timers) {
        if (timer.fireAt <= target && timer.fireAt < dueAt) {
          dueId = id;
          dueAt = timer.fireAt;
        }
      }
      if (dueId === null) {
        break;
      }
      const timer = timers.get(dueId)!;
      timers.delete(dueId);
      now = timer.fireAt;
      timer.fn();
    }
    now = target;
  };

  return {
    intent,
    advance,
    shown,
    hides: () => hides,
    pendingTimers: () => timers.size,
    setNow: (value: number) => {
      now = value;
    },
    now: () => now,
  };
}

describe("hover intent — stationary dwell", () => {
  it("shows nothing until the cursor has held still for the dwell", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS - 1);
    expect(h.shown, "nothing before the dwell elapses").toEqual([]);
    h.advance(1);
    expect(h.shown).toEqual(["a"]);
    expect(h.intent.isVisible()).toBe(true);
  });

  it("keeps the dwell clock running through sub-tolerance jitter", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(100);
    // 3 px: inside the 4 px tolerance, so this is hand-jitter, not intent.
    h.intent.pointerMove(102, 102, "b");
    expect(Math.hypot(2, 2)).toBeLessThanOrEqual(HOVER_DWELL_TOLERANCE_PX);
    h.advance(50);
    // Fires 150 ms after the FIRST sample, not restarted by the jitter.
    expect(h.shown).toEqual(["b"]);
  });

  it("restarts the dwell whenever the cursor leaves the tolerance circle", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(140);
    h.intent.pointerMove(140, 100, "b");
    h.advance(140);
    expect(h.shown, "the second anchor has not aged out yet").toEqual([]);
    h.advance(10);
    expect(h.shown).toEqual(["b"]);
  });

  it("never shows while the cursor keeps sweeping", () => {
    const h = makeHarness();
    for (let step = 0; step < 40; step += 1) {
      h.intent.pointerMove(100 + step * 20, 100, `p${step}`);
      h.advance(16);
    }
    expect(h.shown, "a sweep is not a hover").toEqual([]);
  });

  it("tracks the cursor live once visible, with no second dwell", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown).toEqual(["a"]);
    // Slow exploration: 3 px per event, well under the tolerance.
    h.intent.pointerMove(103, 100, "b");
    h.advance(16);
    h.intent.pointerMove(106, 100, "c");
    expect(h.shown, "each move re-emits immediately").toEqual(["a", "b", "c"]);
    expect(h.hides(), "and never flickers off").toBe(0);
  });
});

describe("hover intent — the hide rule", () => {
  it("hides after sustained fast movement, then re-earns the dwell", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    expect(h.intent.isVisible()).toBe(true);

    // >4 px per event, continuously. The first fast event only STARTS the
    // streak; the readout survives until the streak passes 100 ms.
    let x = 100;
    for (let step = 0; step < 5; step += 1) {
      x += 30;
      h.intent.pointerMove(x, 100, `f${step}`);
      h.advance(30);
    }
    expect(h.intent.isVisible(), "sustained fast movement takes it down").toBe(false);
    expect(h.hides()).toBe(1);

    // And it comes back where the cursor settles, on a fresh dwell.
    const before = h.shown.length;
    h.intent.pointerMove(x, 100, "settled");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown.slice(before)).toContain("settled");
  });

  it("does not hide on a single fast step followed by calm", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    h.intent.pointerMove(160, 100, "jump");
    h.advance(16);
    h.intent.pointerMove(161, 100, "calm");
    h.advance(HOVER_FAST_MOVE_HIDE_MS * 3);
    h.intent.pointerMove(162, 100, "calm2");
    expect(h.intent.isVisible(), "one jump is not a sweep").toBe(true);
    expect(h.hides()).toBe(0);
  });

  it("hides on leave, and only once", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    h.intent.leave();
    expect(h.hides()).toBe(1);
    h.intent.leave();
    h.intent.leave();
    expect(h.hides(), "repeat leaves (an off-disc sweep) do not spam onHide").toBe(1);
  });

  it("issues no hide when nothing was ever shown", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS - 10);
    h.intent.leave();
    expect(h.hides()).toBe(0);
    expect(h.pendingTimers(), "the armed dwell is dropped").toBe(0);
  });
});

describe("hover intent — drag suppression", () => {
  it("reports suppressed while the pointer is down", () => {
    const h = makeHarness();
    expect(h.intent.isSuppressed()).toBe(false);
    h.intent.pointerDown();
    expect(h.intent.isSuppressed()).toBe(true);
    h.intent.pointerUp();
    expect(h.intent.isSuppressed()).toBe(false);
  });

  it("kills a visible readout the moment a drag starts", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    expect(h.intent.isVisible()).toBe(true);
    h.intent.pointerDown();
    expect(h.intent.isVisible(), "THE stuck-tooltip symptom").toBe(false);
    expect(h.hides()).toBe(1);
  });

  it("shows nothing for the whole of a drag", () => {
    const h = makeHarness();
    h.intent.pointerDown();
    for (let step = 0; step < 30; step += 1) {
      h.intent.pointerMove(100 + step * 12, 100, `d${step}`);
      h.advance(16);
    }
    // The pan is over but the pointer is still down.
    h.advance(1_000);
    expect(h.shown, "not one sample through the pan").toEqual([]);
  });

  it("stays suppressed through post-release inertia", () => {
    let moving = true;
    const h = makeHarness({ isMoving: () => moving });
    h.intent.pointerDown();
    h.intent.pointerMove(100, 100, "drag");
    h.intent.pointerUp();
    // Pointer released, map still coasting.
    h.intent.pointerMove(140, 100, "inertia");
    h.advance(1_000);
    expect(h.shown, "inertia is not intent either").toEqual([]);
    moving = false;
    h.intent.pointerMove(140, 100, "settled");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown).toEqual(["settled"]);
  });

  it("suppresses on a camera move with no pointer involved", () => {
    let moving = false;
    const h = makeHarness({ isMoving: () => moving });
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);

    // An ease/fly: `movestart` takes it down immediately, and the map's own
    // signal is what holds it down for the duration.
    moving = true;
    h.intent.mapMoveStart();
    expect(h.intent.isVisible()).toBe(false);
    h.intent.pointerMove(100, 100, "b");
    h.advance(1_000);
    expect(h.shown, "an ease/fly suppresses just like a drag").toEqual(["a"]);

    moving = false;
    h.intent.mapMoveEnd();
    h.intent.pointerMove(100, 100, "c");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown).toEqual(["a", "c"]);
  });

  /**
   * REGRESSION (verifier R1). `movestart` used to set a latch that only
   * `moveend` cleared, and that latch short-circuited the `isMoving()` poll —
   * so a single `movestart` whose `moveend` never arrived (a torn-down
   * animation) disabled hover for the life of the map. `movestart` is now a
   * one-shot hide; ongoing suppression is the poll's job alone.
   */
  it("an unpaired movestart cannot wedge hover off", () => {
    const h = makeHarness({ isMoving: () => false });
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown).toEqual(["a"]);

    h.intent.mapMoveStart();
    // ...and no mapMoveEnd, ever.
    expect(h.intent.isVisible(), "the immediate hide still happens").toBe(false);

    h.intent.pointerMove(200, 200, "after");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown, "hover recovers on the next dwell").toEqual(["a", "after"]);
    expect(h.intent.isSuppressed(), "and nothing is left latched").toBe(false);

    // Ten seconds of ordinary exploration keeps working.
    for (let step = 0; step < 20; step += 1) {
      h.intent.pointerMove(200 + step, 200, `p${step}`);
      h.advance(500);
    }
    expect(h.shown.length, "still emitting after the unpaired movestart").toBeGreaterThan(20);
  });

  it("does not fire a dwell that came due while suppressed", () => {
    const h = makeHarness();
    h.intent.pointerMove(100, 100, "a");
    // Arm the dwell, then start a drag one tick before it would fire.
    h.advance(HOVER_DWELL_MS - 5);
    h.intent.pointerDown();
    h.advance(1_000);
    expect(h.shown).toEqual([]);
  });

  it("survives an isMoving() that throws", () => {
    const h = makeHarness({
      isMoving: () => {
        throw new Error("transform gone");
      },
    });
    expect(h.intent.isSuppressed(), "degrade to not-suppressed, never to broken").toBe(false);
    h.intent.pointerMove(100, 100, "a");
    h.advance(HOVER_DWELL_MS);
    expect(h.shown).toEqual(["a"]);
  });
});

describe("hover intent — defaults and disposal", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it("uses the ambient timer when none is injected", () => {
    vi.useFakeTimers();
    const shown: string[] = [];
    const intent = new HoverIntent<string>({
      onShow: (payload) => shown.push(payload),
      onHide: () => {},
    });
    intent.pointerMove(10, 10, "a");
    vi.advanceTimersByTime(HOVER_DWELL_MS - 1);
    expect(shown).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(shown).toEqual(["a"]);
    intent.dispose();
    vi.useRealTimers();
  });

  it("dispose drops a pending dwell without emitting", () => {
    const h = makeHarness();
    h.intent.pointerMove(10, 10, "a");
    h.intent.dispose();
    h.advance(1_000);
    expect(h.shown).toEqual([]);
    expect(h.hides()).toBe(0);
  });

  it("pins the tunable constants", () => {
    // Operator-tunable; a change here is a product decision, not a refactor.
    expect(HOVER_DWELL_MS).toBe(150);
    expect(HOVER_DWELL_TOLERANCE_PX).toBe(4);
    expect(HOVER_FAST_MOVE_HIDE_MS).toBe(100);
  });
});
