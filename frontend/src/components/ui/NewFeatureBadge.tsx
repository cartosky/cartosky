import type { NewFeatureId } from "@/lib/new-features";
import { isNewFeature } from "@/lib/new-features";
import { cn } from "@/lib/utils";

export function NewFeatureBadge({
  feature,
  className,
}: {
  feature: NewFeatureId;
  className?: string;
}) {
  if (!isNewFeature(feature)) return null;

  return (
    <span
      data-new-feature={feature}
      aria-label="New feature"
      className={cn(
        "pointer-events-none absolute right-0 top-0 z-20 -translate-y-1/2 translate-x-1/3 whitespace-nowrap",
        "rounded-full border border-cyan-200/45 bg-[#073047]/95 px-1.5 py-px",
        "font-['IBM_Plex_Mono',monospace] text-[9px] font-bold uppercase leading-[12px] tracking-[0.08em] text-cyan-100",
        "shadow-[0_2px_8px_rgba(34,211,238,0.28)] backdrop-blur-sm",
        className,
      )}
    >
      New
    </span>
  );
}
