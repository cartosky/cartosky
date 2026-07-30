/**
 * Longitude normalization for the sampling boundary.
 *
 * MapLibre reports UNWRAPPED longitudes for pointer events on world copies —
 * hovering one world east of the primary gives lng 181, not -179. The sampling
 * API contract is `lon ge=-180 le=180` (docs/GLOBAL_DOMAIN_4326_CONTRACT.md §3),
 * so an unwrapped value is a 422 and the tooltip silently disappears (the catch
 * in use-sample-tooltip.ts swallows it). Normalizing here keeps the contract
 * unchanged and makes hover work on every copy.
 */

/**
 * Longitude folded into [-180, 180], with +180 preserved rather than folded to
 * -180 — the sampler treats +180 as the seam column's own meridian (contract
 * §3, "lon = +180 special case") and both endpoints are legal input.
 *
 * Non-finite input is returned unchanged; the caller's own validity checks own
 * that case.
 */
export function normalizeLongitudeDeg(lon: number): number {
  if (!Number.isFinite(lon)) {
    return lon;
  }
  if (lon >= -180 && lon <= 180) {
    return lon;
  }
  const wrapped = ((((lon + 180) % 360) + 360) % 360) - 180;
  return wrapped;
}
