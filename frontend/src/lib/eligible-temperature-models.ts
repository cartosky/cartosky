import {
  CONUS_ONLY_GUIDANCE_MODELS,
  TEMPERATURE_GUIDANCE_MODELS,
  isInsideConus,
} from "@/lib/chart-constants";

/** Eligible long-range temperature models for the current location and entitlements. */
export function eligibleTemperatureModels(
  lat: number,
  lon: number,
  canAccessProduct: (productId: string) => boolean,
): string[] {
  const insideConus = isInsideConus(lat, lon);
  return TEMPERATURE_GUIDANCE_MODELS.filter((model) => {
    if (CONUS_ONLY_GUIDANCE_MODELS.has(model) && !insideConus) return false;
    if (!canAccessProduct(model)) return false;
    return true;
  });
}
