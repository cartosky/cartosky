import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  fetchCapabilities,
  fetchRegionPresets,
  type CapabilitiesResponse,
  type RegionPreset,
} from "@/lib/api";

export type CapabilitiesContextValue = {
  capabilities: CapabilitiesResponse | null;
  regionPresets: Record<string, RegionPreset> | null;
  loading: boolean;
  error: Error | null;
};

const CapabilitiesContext = createContext<CapabilitiesContextValue | null>(null);

export function CapabilitiesProvider({ children }: { children: ReactNode }) {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [regionPresets, setRegionPresets] = useState<Record<string, RegionPreset> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    setLoading(true);
    setError(null);

    Promise.all([
      fetchCapabilities({ signal: controller.signal }),
      fetchRegionPresets({ signal: controller.signal }),
    ])
      .then(([capabilitiesResponse, regionPresetsResponse]) => {
        if (controller.signal.aborted) {
          return;
        }
        setCapabilities(capabilitiesResponse);
        setRegionPresets(regionPresetsResponse);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
          return;
        }
        setError(err instanceof Error ? err : new Error(String(err)));
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, []);

  const value: CapabilitiesContextValue = {
    capabilities,
    regionPresets,
    loading,
    error,
  };

  return <CapabilitiesContext.Provider value={value}>{children}</CapabilitiesContext.Provider>;
}

export function useCapabilities(): CapabilitiesContextValue {
  const value = useContext(CapabilitiesContext);
  if (!value) {
    throw new Error("useCapabilities must be used inside CapabilitiesProvider");
  }
  return value;
}
