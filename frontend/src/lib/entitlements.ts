import { useCallback } from "react";
import { useAuth } from "@clerk/react";

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

export function getProtectedProductLabel(productId: string): string | null {
  const product = PROTECTED_PRODUCTS[String(productId ?? "").trim().toLowerCase() as keyof typeof PROTECTED_PRODUCTS];
  return product?.label ?? null;
}

export function getProtectedProductUpsellLabel(productId: string): string | null {
  const product = PROTECTED_PRODUCTS[String(productId ?? "").trim().toLowerCase() as keyof typeof PROTECTED_PRODUCTS];
  return product?.upsellLabel ?? null;
}

export function useEntitlements() {
  const auth = useAuth();
  const isLoaded = auth.isLoaded === true;
  const has = auth.has;

  const hasFeature = useCallback((featureSlug: string): boolean => {
    const normalized = String(featureSlug ?? "").trim();
    if (!normalized || !isLoaded || typeof has !== "function") {
      return false;
    }
    try {
      return has({ feature: normalized });
    } catch {
      return false;
    }
  }, [has, isLoaded]);

  const canAccessFeature = useCallback((featureSlug: string): boolean => {
    if (!proGatingEnabled) {
      return true;
    }
    const normalized = String(featureSlug ?? "").trim();
    if (!normalized) {
      return true;
    }
    return hasFeature(normalized);
  }, [hasFeature]);

  const canAccessProduct = useCallback((productId: string): boolean => {
    const requiredFeature = getRequiredFeatureForProduct(productId);
    return requiredFeature ? canAccessFeature(requiredFeature) : true;
  }, [canAccessFeature]);

  const getLockedReason = useCallback((productId: string): string | null => {
    if (canAccessProduct(productId)) {
      return null;
    }
    return getProtectedProductUpsellLabel(productId) ?? "Requires CartoSky Pro";
  }, [canAccessProduct]);

  return {
    billingEnabled,
    proGatingEnabled,
    pricingPreviewEnabled,
    hasFeature,
    canAccessFeature,
    canAccessProduct,
    getRequiredFeatureForProduct,
    getLockedReason,
    isLoaded,
  };
}
