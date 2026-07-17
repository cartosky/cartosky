/**
 * Non-blocking indeterminate progress bar shown along the bottom edge of the
 * viewer header while a model/variable/run/region switch has a manifest fetch
 * in flight. Never shown during cold boot — the hex signal ring owns that
 * phase. See docs/LOADER_REDESIGN_IMPLEMENTATION_PLAN.md §5.
 */
export function ViewerTopProgressBar({ visible }: { visible: boolean }) {
  if (!visible) {
    return null;
  }
  return (
    <div aria-hidden="true" className="viewer-top-progress">
      <div className="viewer-top-progress-fill" />
    </div>
  );
}
