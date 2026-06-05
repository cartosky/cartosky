export type ProtectedProductType =
  | "model"
  | "ensemble"
  | "observation"
  | "forecast"
  | "tool"
  | "saved_view"
  | "comparison";

export type ProtectedProductConfig = {
  productType: ProtectedProductType;
  requiredFeature: string;
  label: string;
  upsellLabel: string;
};

export const PROTECTED_PRODUCTS = {
  ecmwf: {
    productType: "model",
    requiredFeature: "ecmwf",
    label: "ECMWF",
    upsellLabel: "Requires CartoSky Pro",
  },
} satisfies Record<string, ProtectedProductConfig>;

export type ProtectedProductId = keyof typeof PROTECTED_PRODUCTS;
