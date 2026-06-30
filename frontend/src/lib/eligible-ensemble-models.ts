import { ENSEMBLE_GUIDANCE_MODELS } from "@/lib/chart-constants";

/**
 * Eligible ensemble models (EPS, GEFS) for the current entitlements. Ensembles
 * are global products with no CONUS restriction, so only entitlement gating
 * applies. Pills are never shown for models the user cannot access.
 */
export function eligibleEnsembleModels(
  canAccessProduct: (productId: string) => boolean,
): string[] {
  return ENSEMBLE_GUIDANCE_MODELS.filter((model) => canAccessProduct(model));
}
