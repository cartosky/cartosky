import { useBootstrapComplete } from "@/lib/bootstrap-loading";
import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

export function MarketingRouteSuspenseFallback() {
  const bootstrapComplete = useBootstrapComplete();
  if (bootstrapComplete) {
    return null;
  }
  return <SiteLoadingOverlay visible label="Loading page" />;
}

export function AdminRouteSuspenseFallback() {
  return <SiteLoadingOverlay visible label="Loading page" />;
}
