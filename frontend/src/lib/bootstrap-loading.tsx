import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

type BootstrapContextValue = {
  markBootstrapComplete: () => void;
};

const BootstrapContext = createContext<BootstrapContextValue | null>(null);

export function BootstrapProvider({ children }: { children: ReactNode }) {
  const [complete, setComplete] = useState(false);

  const markBootstrapComplete = useCallback(() => {
    setComplete(true);
  }, []);

  const value = useMemo(() => ({ markBootstrapComplete }), [markBootstrapComplete]);

  return (
    <BootstrapContext.Provider value={value}>
      {children}
      <SiteLoadingOverlay visible={!complete} label="Loading" delayMs={0} />
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
