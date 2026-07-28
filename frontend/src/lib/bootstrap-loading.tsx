import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

type BootstrapContextValue = {
  complete: boolean;
  markBootstrapComplete: () => void;
};

const BootstrapContext = createContext<BootstrapContextValue | null>(null);

export function useBootstrapComplete(): boolean {
  return useContext(BootstrapContext)?.complete ?? false;
}

export function BootstrapProvider({ children }: { children: ReactNode }) {
  const [complete, setComplete] = useState(false);
  // /viewer paints its own viewer-shaped shell (static document fallback +
  // AppLayout suspense skeleton) and gates only the map canvas; adding the
  // full-screen overlay there would reintroduce a viewport-covering blocker.
  // Every other route keeps the overlay.
  const isViewerRoute = useLocation().pathname === "/viewer";

  const markBootstrapComplete = useCallback(() => {
    setComplete(true);
  }, []);

  const value = useMemo(
    () => ({ complete, markBootstrapComplete }),
    [complete, markBootstrapComplete],
  );

  return (
    <BootstrapContext.Provider value={value}>
      {children}
      <SiteLoadingOverlay visible={!complete && !isViewerRoute} label="Loading" delayMs={0} />
    </BootstrapContext.Provider>
  );
}

export function BootstrapCompleteMarker() {
  const context = useContext(BootstrapContext);

  useEffect(() => {
    context?.markBootstrapComplete();
  }, [context]);

  return null;
}
