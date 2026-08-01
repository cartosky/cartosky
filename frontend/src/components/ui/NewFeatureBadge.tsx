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
        "rounded-full border-2 border-cyan-200/40 bg-[#073047]/95 px-1.5 py-px",
        "font-sans text-[9px] font-semibold uppercase leading-[12px] tracking-[0.08em] text-cyan-100",
        "shadow-[0_2px_8px_rgba(34,211,238,0.30)] backdrop-blur-sm",
        className,
      )}
    >
      New
    </span>
  );
}
