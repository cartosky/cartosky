/**
 * Viewer keyboard map (map viewer redesign Phase 5, Task 5).
 *
 * One document-level handler owns every viewer shortcut so they work from a
 * cold load with no prior click. MapLibre's own keyboard handler is disabled
 * at map construction (arrows must step frames, not pan), so +/- zoom and
 * WASD pan are reimplemented here at MapLibre's own step sizes.
 *
 * Suppression rules are deliberately conservative: anything already handled,
 * an IME composition, a Ctrl/Meta chord, or focus inside an interactive
 * element yields to the platform. `preventDefault` is only called when a
 * shortcut actually fires.
 */
import { useEffect, useRef } from "react";

/** Elements that own their own key semantics — shortcuts stay out of them. */
const INTERACTIVE_SELECTOR = [
  "input",
  "textarea",
  "select",
  "button",
  "a[href]",
  '[contenteditable=""]',
  '[contenteditable="true"]',
  '[role="button"]',
  '[role="combobox"]',
  '[role="listbox"]',
  '[role="menu"]',
  '[role="menubar"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]',
  '[role="textbox"]',
  '[role="dialog"][data-state="open"]',
  "[data-radix-popper-content-wrapper]",
  "dialog[open]",
].join(", ");

/** Minimal MapLibre surface this hook drives; keeps the map type out of here. */
export type ViewerKeyboardMap = {
  getZoom: () => number;
  easeTo: (options: { zoom?: number; duration?: number }) => unknown;
  panBy: (offset: [number, number], options?: { duration?: number }) => unknown;
};

export type ViewerKeyboardOptions = {
  enabled: boolean;
  /** Committable frame hours, ascending (already eligibility-filtered). */
  frameHours: number[];
  forecastHour: number;
  onForecastHour: (fh: number) => void;
  onTogglePlay: () => void;
  /** Run ids in the picker's order — newest first. */
  runValues: string[];
  currentRun: string;
  onRunChange: (runId: string) => void;
  shortcutSheetOpen: boolean;
  onShortcutSheetOpenChange: (open: boolean) => void;
  getMap: () => ViewerKeyboardMap | null;
};

function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false;
  }
  return Boolean(target.closest(INTERACTIVE_SELECTOR));
}

function stepIndex(frameHours: number[], current: number, direction: 1 | -1): number | null {
  if (frameHours.length === 0) {
    return null;
  }
  let index = frameHours.indexOf(current);
  if (index < 0) {
    // Cold loads and out-of-list hours snap to the nearest frame first.
    let nearest = 0;
    for (let i = 1; i < frameHours.length; i += 1) {
      if (Math.abs(frameHours[i] - current) < Math.abs(frameHours[nearest] - current)) {
        nearest = i;
      }
    }
    index = nearest;
  }
  const next = Math.max(0, Math.min(frameHours.length - 1, index + direction));
  return frameHours[next];
}

/**
 * Directional nearest-frame snapping: land on the first frame at or beyond the
 * requested offset in the pressed direction, clamped to the ends.
 */
function jumpHours(frameHours: number[], current: number, offset: number): number | null {
  if (frameHours.length === 0) {
    return null;
  }
  const target = current + offset;
  if (offset > 0) {
    return frameHours.find((fh) => fh >= target) ?? frameHours[frameHours.length - 1];
  }
  for (let i = frameHours.length - 1; i >= 0; i -= 1) {
    if (frameHours[i] <= target) {
      return frameHours[i];
    }
  }
  return frameHours[0];
}

export function useViewerKeyboard(options: ViewerKeyboardOptions): void {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const current = optionsRef.current;
      if (!current.enabled || event.defaultPrevented) {
        return;
      }
      if (event.isComposing || event.keyCode === 229) {
        return;
      }

      // Escape closes the shortcut sheet even from inside it.
      if (event.key === "Escape" && current.shortcutSheetOpen) {
        current.onShortcutSheetOpenChange(false);
        event.preventDefault();
        return;
      }

      if (event.ctrlKey || event.metaKey) {
        return;
      }
      if (isInteractiveTarget(event.target)) {
        return;
      }

      const { frameHours, forecastHour } = current;
      const commit = (next: number | null) => {
        if (next === null || next === forecastHour) {
          return true;
        }
        current.onForecastHour(next);
        return true;
      };

      let handled = false;
      switch (event.key) {
        case "ArrowRight":
        case "ArrowLeft": {
          const direction = event.key === "ArrowRight" ? 1 : -1;
          if (event.altKey) {
            handled = commit(jumpHours(frameHours, forecastHour, 24 * direction));
          } else if (event.shiftKey) {
            handled = commit(jumpHours(frameHours, forecastHour, 6 * direction));
          } else {
            handled = commit(stepIndex(frameHours, forecastHour, direction));
          }
          break;
        }
        case "Home":
          handled = commit(frameHours[0] ?? null);
          break;
        case "End":
          handled = commit(frameHours[frameHours.length - 1] ?? null);
          break;
        case " ":
        case "Spacebar":
          if (event.repeat) {
            return;
          }
          current.onTogglePlay();
          handled = true;
          break;
        case "[":
        case "]": {
          if (event.repeat) {
            return;
          }
          const runs = current.runValues;
          if (runs.length === 0) {
            break;
          }
          const index = Math.max(0, runs.indexOf(current.currentRun));
          // Runs are newest-first: `[` walks older (up the array), `]` newer.
          const nextIndex = event.key === "["
            ? Math.min(runs.length - 1, index + 1)
            : Math.max(0, index - 1);
          if (runs[nextIndex] !== current.currentRun) {
            current.onRunChange(runs[nextIndex]);
          }
          handled = true;
          break;
        }
        case "?":
          if (event.repeat) {
            return;
          }
          current.onShortcutSheetOpenChange(!current.shortcutSheetOpen);
          handled = true;
          break;
        case "+":
        case "=":
        case "-":
        case "_": {
          const map = current.getMap();
          if (!map) {
            break;
          }
          const direction = event.key === "+" || event.key === "=" ? 1 : -1;
          const levels = event.shiftKey ? 2 : 1;
          map.easeTo({ zoom: map.getZoom() + direction * levels, duration: 300 });
          handled = true;
          break;
        }
        case "w":
        case "a":
        case "s":
        case "d": {
          const map = current.getMap();
          if (!map) {
            break;
          }
          const offsets: Record<string, [number, number]> = {
            w: [0, -100],
            s: [0, 100],
            a: [-100, 0],
            d: [100, 0],
          };
          map.panBy(offsets[event.key], { duration: 300 });
          handled = true;
          break;
        }
        default:
          break;
      }

      if (handled) {
        event.preventDefault();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);
}
