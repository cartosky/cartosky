import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { SiteLoadingOverlay } from "@/components/site-loading-overlay";

type SiteLoadingContextValue = {
  start: (label?: string) => () => void;
};

type LoadingEntry = {
  id: number;
  label: string;
};

const SiteLoadingContext = createContext<SiteLoadingContextValue | null>(null);

export function SiteLoadingProvider({ children }: { children: ReactNode }) {
  const nextIdRef = useRef(1);
  const [entries, setEntries] = useState<LoadingEntry[]>([]);

  const start = useCallback((label = "Loading") => {
    const id = nextIdRef.current;
    nextIdRef.current += 1;
    setEntries((current) => [...current, { id, label }]);

    let stopped = false;
    return () => {
      if (stopped) {
        return;
      }
      stopped = true;
      setEntries((current) => current.filter((entry) => entry.id !== id));
    };
  }, []);

  const value = useMemo(() => ({ start }), [start]);
  const activeEntry = entries.length > 0 ? entries[entries.length - 1] : null;

  return (
    <SiteLoadingContext.Provider value={value}>
      {children}
      <SiteLoadingOverlay
        visible={Boolean(activeEntry)}
        label={activeEntry?.label ?? "Loading"}
      />
    </SiteLoadingContext.Provider>
  );
}

export function useSiteLoading() {
  const context = useContext(SiteLoadingContext);
  if (!context) {
    throw new Error("useSiteLoading must be used within SiteLoadingProvider");
  }
  return context;
}
