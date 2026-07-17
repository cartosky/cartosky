import { useEffect, useState } from "react";

import { HexSignalRing } from "@/components/HexSignalRing";

type SiteLoadingOverlayProps = {
  visible: boolean;
  label?: string;
  delayMs?: number;
};

export function SiteLoadingOverlay({
  visible,
  label = "Loading",
  delayMs = 140,
}: SiteLoadingOverlayProps) {
  const [shouldRender, setShouldRender] = useState(visible && delayMs <= 0);

  useEffect(() => {
    if (!visible) {
      setShouldRender(false);
      return;
    }
    if (delayMs <= 0) {
      setShouldRender(true);
      return;
    }

    const timer = window.setTimeout(() => setShouldRender(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, visible]);

  if (!shouldRender) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={label}
      className="fixed inset-0 z-[90] grid place-items-center bg-[#040d18]/58 text-white backdrop-blur-[2px]"
    >
      <div className="flex flex-col items-center gap-3">
        <HexSignalRing />
        <div className="max-w-[13rem] text-center text-xs font-medium text-white/76">
          {label}
        </div>
      </div>
    </div>
  );
}
