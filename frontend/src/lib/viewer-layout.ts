import { useEffect, useState } from "react";

import { RAIL_MOBILE_MAX_WIDTH } from "@/lib/viewer-rail";

export type ViewerLayoutMode = "mobile" | "tablet-touch" | "desktop";

// §6.4 (Phase 6): 640-767 moved from the desktop header to the mobile sheet.
// Shared with the rail state model so the two cutoffs can never disagree.
const MOBILE_MAX_WIDTH = RAIL_MOBILE_MAX_WIDTH;
const TOUCH_TABLET_MAX_WIDTH = 1279;
const TOUCH_TABLET_MAX_HEIGHT = 950;

function supportsMatchMedia(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

function hasCoarsePointer(): boolean {
  if (!supportsMatchMedia()) {
    return false;
  }
  return window.matchMedia("(pointer: coarse)").matches;
}

function hasAnyCoarsePointer(): boolean {
  if (!supportsMatchMedia()) {
    return false;
  }
  return window.matchMedia("(any-pointer: coarse)").matches;
}

function hasTouchSupport(): boolean {
  if (typeof navigator === "undefined") {
    return false;
  }
  return (navigator.maxTouchPoints ?? 0) > 0;
}

function isAndroidDevice(): boolean {
  if (typeof navigator === "undefined") {
    return false;
  }
  return /android/i.test(navigator.userAgent || "");
}

function canHover(): boolean {
  if (!supportsMatchMedia()) {
    return true;
  }
  return window.matchMedia("(hover: hover)").matches;
}

export function detectViewerLayoutMode(): ViewerLayoutMode {
  if (typeof window === "undefined") {
    return "desktop";
  }

  const width = window.innerWidth;
  const height = window.innerHeight;

  if (width <= MOBILE_MAX_WIDTH) {
    return "mobile";
  }

  const coarsePointer = hasCoarsePointer();
  const anyCoarsePointer = hasAnyCoarsePointer();
  const touchSupport = hasTouchSupport();
  const androidDevice = isAndroidDevice();
  const hoverCapable = canHover();
  const touchFirstDevice = coarsePointer || (androidDevice && (touchSupport || anyCoarsePointer));

  if (touchFirstDevice && (!hoverCapable || androidDevice)) {
    const tabletTouchViewport = width <= TOUCH_TABLET_MAX_WIDTH || height <= TOUCH_TABLET_MAX_HEIGHT;
    if (tabletTouchViewport) {
      return "tablet-touch";
    }
  }

  return "desktop";
}

export function useViewerLayoutMode(): ViewerLayoutMode {
  const [mode, setMode] = useState<ViewerLayoutMode>(() => detectViewerLayoutMode());

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const update = () => {
      setMode(detectViewerLayoutMode());
    };

    update();

    const coarseQuery = window.matchMedia("(pointer: coarse)");
    const hoverQuery = window.matchMedia("(hover: hover)");

    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    coarseQuery.addEventListener("change", update);
    hoverQuery.addEventListener("change", update);

    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      coarseQuery.removeEventListener("change", update);
      hoverQuery.removeEventListener("change", update);
    };
  }, []);

  return mode;
}
