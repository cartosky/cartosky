// Contour artifacts live at `.../{model}/{run}/{varKey}/{fh}/contours/{key}`
// (buildContourUrl in lib/api.ts), so two contour URLs belong to the same
// selection when only the segment right before `contours` differs. The map uses
// this to decide whether a frame switch may keep the drawn contours up while the
// next payload loads, instead of blanking them.

export function contourSelectionKey(rawUrl: string | null | undefined): string {
  const normalizedUrl = String(rawUrl ?? "").trim();
  if (!normalizedUrl) {
    return "";
  }
  const queryIndex = normalizedUrl.indexOf("?");
  const path = queryIndex >= 0 ? normalizedUrl.slice(0, queryIndex) : normalizedUrl;
  const query = queryIndex >= 0 ? normalizedUrl.slice(queryIndex) : "";
  const segments = path.split("/");
  const contoursIndex = segments.lastIndexOf("contours");
  if (contoursIndex < 1) {
    // Not a contour artifact URL shape — fall back to exact-match semantics.
    return normalizedUrl;
  }
  segments[contoursIndex - 1] = "*";
  return `${segments.join("/")}${query}`;
}

/**
 * True when the contours currently on screen are a different forecast hour of
 * the selection being requested — the only case where holding them is correct.
 * Empty/blank URLs on either side are never a match, so turning contours off (or
 * switching model/run/variable/domain) still clears immediately.
 */
export function isSameContourSelection(
  activeUrl: string | null | undefined,
  nextUrl: string | null | undefined,
): boolean {
  const activeKey = contourSelectionKey(activeUrl);
  const nextKey = contourSelectionKey(nextUrl);
  return Boolean(activeKey) && activeKey === nextKey;
}
