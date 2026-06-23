import { modelColor, modelShortName } from "@/lib/chart-constants";

type ModelPillFilterProps = {
  // Models eligible to show (already filtered for coverage + entitlement by the
  // parent). A pill is rendered for each.
  models: string[];
  activeModels: Set<string>;
  onChange: (next: Set<string>) => void;
};

/**
 * Row of toggleable model pills. Each pill shows a color dot + short name.
 * Models the user cannot see (out of coverage / not entitled) are excluded by
 * the parent and never rendered here — no greyed-out pills.
 */
export function ModelPillFilter({ models, activeModels, onChange }: ModelPillFilterProps) {
  const toggle = (model: string) => {
    const next = new Set(activeModels);
    if (next.has(model)) {
      next.delete(model);
    } else {
      next.add(model);
    }
    onChange(next);
  };

  return (
    <div className="flex flex-wrap gap-2">
      {models.map((model) => {
        const active = activeModels.has(model);
        return (
          <button
            key={model}
            type="button"
            onClick={() => toggle(model)}
            aria-pressed={active}
            className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
              active
                ? "border-white/20 bg-white/[0.08] text-white/85"
                : "border-white/10 bg-transparent text-white/40 hover:text-white/60"
            }`}
          >
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: active ? modelColor(model) : "transparent", outline: `1px solid ${modelColor(model)}` }}
            />
            {modelShortName(model)}
          </button>
        );
      })}
    </div>
  );
}
