/**
 * Keyboard shortcut sheet (map viewer redesign Phase 5, Task 5). Opened with
 * `?`, closed with Escape — both owned by useViewerKeyboard so the sheet has
 * no key handling of its own and cannot swallow the viewer's shortcuts.
 */
type ShortcutRow = { keys: string; description: string };

const SHORTCUT_GROUPS: Array<{ title: string; rows: ShortcutRow[] }> = [
  {
    title: "Timeline",
    rows: [
      { keys: "← / →", description: "Previous / next frame" },
      { keys: "Shift + ← / →", description: "Jump 6 hours" },
      { keys: "Alt + ← / →", description: "Jump 24 hours" },
      { keys: "Home / End", description: "First frame / ready boundary" },
      { keys: "Space", description: "Play or pause" },
    ],
  },
  {
    title: "Runs & map",
    rows: [
      { keys: "[ / ]", description: "Older / newer run" },
      { keys: "+ / −", description: "Zoom in / out (Shift for two levels)" },
      { keys: "W A S D", description: "Pan the map" },
      { keys: "?", description: "Show this sheet" },
    ],
  },
];

export function ShortcutSheet({ onClose }: { onClose: () => void }) {
  return (
    <div className="pointer-events-auto fixed inset-0 z-[120] flex items-center justify-center p-4">
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        data-testid="shortcut-sheet"
        role="dialog"
        aria-modal="false"
        aria-label="Keyboard shortcuts"
        className="relative w-[min(92vw,32rem)] rounded-2xl border border-white/10 bg-[#07111f]/95 p-5 shadow-2xl"
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-white">Keyboard shortcuts</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close keyboard shortcuts"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-[13px] text-white/70 transition-colors hover:bg-white/[0.09] hover:text-white pointer-coarse:h-11 pointer-coarse:w-11"
          >
            Esc
          </button>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-300/70">
                {group.title}
              </div>
              <dl className="space-y-1.5">
                {group.rows.map((row) => (
                  <div key={row.keys} className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[12px] text-white/85">
                      {row.keys}
                    </dt>
                    <dd className="min-w-0 text-right text-[12px] text-white/55">{row.description}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
