import { useCallback } from "react";
import { useUser } from "@clerk/react";

import { PROTECTED_PRODUCTS } from "@/config/proFeatures";

function readBooleanEnv(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "true";
}

export const billingEnabled = readBooleanEnv(import.meta.env.VITE_BILLING_ENABLED);
export const proGatingEnabled = readBooleanEnv(import.meta.env.VITE_PRO_GATING_ENABLED);
export const pricingPreviewEnabled = readBooleanEnv(import.meta.env.VITE_PRICING_PREVIEW_ENABLED);

if (import.meta.env.DEV) {
  console.info("[billing]", {
    billingEnabled,
    proGatingEnabled,
    pricingPreviewEnabled,
  });
}

export function getRequiredFeatureForProduct(productId: string): string | null {
  const product = PROTECTED_PRODUCTS[String(productId ?? "").trim().toLowerCase() as keyof typeof PROTECTED_PRODUCTS];
  return product?.requiredFeature ?? null;
}

function normalizePlan(plan: unknown): string {
  const normalized = String(plan ?? "").trim().toLowerCase();
  return normalized || "free";
}

const protectedFeatureSlugs = new Set(
  Object.values(PROTECTED_PRODUCTS)
    .map((product) => String(product.requiredFeature ?? "").trim().toLowerCase())
    .filter(Boolean)
);

export function planFromPublicMetadata(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object") {
    return "free";
  }
  const value = metadata as Record<string, unknown>;
  return normalizePlan(value.plan);
}

export function hasFeature(plan: unknown, featureSlug: string): boolean {
  const normalizedFeature = String(featureSlug ?? "").trim().toLowerCase();
  if (!normalizedFeature) {
    return false;
  }
  if (normalizePlan(plan) !== "pro") {
    return false;
  }
  return protectedFeatureSlugs.has(normalizedFeature);
}

export function canAccessFeature(plan: unknown, featureSlug: string): boolean {
  if (!proGatingEnabled) {
    return true;
  }
  const normalizedFeature = String(featureSlug ?? "").trim().toLowerCase();
  if (!normalizedFeature) {
    return true;
  }
  return hasFeature(plan, normalizedFeature);
}

export function canAccessProduct(productId: string, plan: unknown = "free"): boolean {
  const requiredFeature = getRequiredFeatureForProduct(productId);
  return requiredFeature ? canAccessFeature(plan, requiredFeature) : true;
}

export function shouldAuthorizeProductRequest(productId: string): boolean {
  if (!proGatingEnabled) {
    return false;
  }
  return getRequiredFeatureForProduct(productId) !== null;
}

export function getProtectedProductLabel(productId: string): string | null {
  const product = PROTECTED_PRODUCTS[String(productId ?? "").trim().toLowerCase() as keyof typeof PROTECTED_PRODUCTS];
  return product?.label ?? null;
}

export function getProtectedProductUpsellLabel(productId: string): string | null {
  const product = PROTECTED_PRODUCTS[String(productId ?? "").trim().toLowerCase() as keyof typeof PROTECTED_PRODUCTS];
  return product?.upsellLabel ?? null;
}

export function getLockedReason(productId: string, plan: unknown = "free"): string | null {
  if (canAccessProduct(productId, plan)) {
    return null;
  }
  return getProtectedProductUpsellLabel(productId) ?? "Requires CartoSky Pro";
}

export function useEntitlements() {
  const { isLoaded, user } = useUser();
  const plan = planFromPublicMetadata(user?.publicMetadata);

  const boundHasFeature = useCallback((featureSlug: string): boolean => hasFeature(plan, featureSlug), [plan]);

  const boundCanAccessFeature = useCallback((featureSlug: string): boolean => canAccessFeature(plan, featureSlug), [plan]);

  const boundCanAccessProduct = useCallback((productId: string): boolean => canAccessProduct(productId, plan), [plan]);

  const boundGetLockedReason = useCallback((productId: string): string | null => getLockedReason(productId, plan), [plan]);

  return {
    billingEnabled,
    proGatingEnabled,
    pricingPreviewEnabled,
    hasFeature: boundHasFeature,
    canAccessFeature: boundCanAccessFeature,
    canAccessProduct: boundCanAccessProduct,
    getRequiredFeatureForProduct,
    getLockedReason: boundGetLockedReason,
    isLoaded: isLoaded === true,
    plan,
  };
}
