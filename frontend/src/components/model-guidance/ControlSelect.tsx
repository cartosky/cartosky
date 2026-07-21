import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type ControlOption = { value: string; label: string };

/**
 * Labeled themed dropdown for the control bar. Dropdowns (not segmented
 * toggles) by design: additional views/variables flow into them without the
 * bar growing. Uses the design-system Radix select so the open panel is the
 * site's glass/cyan styling everywhere — native <select> pickers fall back to
 * the generic browser control on mobile/tablet.
 */
export function ControlSelect({
  label,
  ariaLabel,
  value,
  options,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  options: ControlOption[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-white/40">
        {label}
      </span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger
          aria-label={ariaLabel}
          className="h-8 w-auto gap-1.5 rounded-lg border-white/[0.09] bg-white/[0.05] px-2.5 text-[12px] text-white/80 ring-offset-0 hover:bg-white/[0.08] focus:ring-1 focus:ring-cyan-300/40 focus:ring-offset-0"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
