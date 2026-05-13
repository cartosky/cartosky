import { useEffect, useState } from "react";

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
      <div className="glass-overlay flex min-w-36 flex-col items-center gap-3 rounded-2xl px-5 py-4 shadow-[0_22px_64px_rgba(0,0,0,0.36)]">
        <div className="relative h-11 w-11">
          <div className="absolute inset-0 rounded-full border border-cyan-200/18" />
          <div className="absolute inset-1 rounded-full border-2 border-white/10 border-t-cyan-200 animate-spin" />
          <div className="absolute inset-[0.95rem] rounded-full bg-cyan-200/80 shadow-[0_0_22px_rgba(103,232,249,0.42)]" />
        </div>
        <div className="max-w-[13rem] text-center text-xs font-medium text-white/76">
          {label}
        </div>
      </div>
    </div>
  );
}
