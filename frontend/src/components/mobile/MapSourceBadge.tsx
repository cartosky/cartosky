import { cn } from "@/lib/utils";

/**
 * Map source badge — map viewer redesign Phase 8 (§8 State A, decision 5).
 *
 * §8 "No duplication": the badge owns SOURCE IDENTITY (`MODEL · Variable`) and
 * the sheet peek owns RUN STATE. Informational on purpose — tapping the peek
 * is the path to controls, so the badge needs no 44 px target and no tour step.
 *
 * It is an overlay over the map, never a layout row: the map element's box is
 * fixed by `--viewer-map-bottom-inset` and must stay that way (§9).
 */
export function MapSourceBadge({
  modelLabel,
  variableLabel,
}: {
  modelLabel?: string | null;
  variableLabel?: string | null;
}) {
  const model = modelLabel?.trim();
  const variable = variableLabel?.trim();
  if (!model && !variable) {
    return null;
  }

  return (
    <div
      data-testid="map-source-badge"
      data-export-exclude="source-badge"
      className={cn(
        "pointer-events-none fixed left-3 z-[45] flex max-w-[62vw] items-center gap-1.5",
        "rounded-lg border border-[#1a3a5c]/60 bg-[#04101e]/[0.82] px-2.5 py-1.5",
        "shadow-[0_8px_32px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
      )}
      style={{ top: "calc(var(--viewer-map-top-inset, 0px) + 0.75rem)" }}
    >
      {model ? (
        <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[12px] font-semibold uppercase tracking-[0.16em] text-cyan-300/85">
          {model}
        </span>
      ) : null}
      {model && variable ? (
        <span aria-hidden="true" className="shrink-0 text-[12px] text-white/28">·</span>
      ) : null}
      {variable ? (
        <span className="min-w-0 truncate text-[12px] font-medium text-white/88">{variable}</span>
      ) : null}
    </div>
  );
}

export default MapSourceBadge;
