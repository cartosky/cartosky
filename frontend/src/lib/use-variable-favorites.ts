import { useCallback, useEffect, useMemo, useState } from "react";

const FAVORITES_STORAGE_PREFIX = "cartosky.variableFavorites";

function storageKeyForModel(modelId: string): string {
  return `${FAVORITES_STORAGE_PREFIX}.${modelId || "unknown"}`;
}

function readFavorites(modelId: string): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(storageKeyForModel(modelId));
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.map((value) => String(value ?? "").trim()).filter(Boolean);
  } catch {
    return [];
  }
}

function writeFavorites(modelId: string, favorites: string[]): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(storageKeyForModel(modelId), JSON.stringify(favorites));
  } catch {
    // Ignore storage errors in private mode or quota-limited environments.
  }
}

export function useVariableFavorites(modelId: string) {
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites(modelId));

  useEffect(() => {
    setFavorites(readFavorites(modelId));
  }, [modelId]);

  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);

  const toggleFavorite = useCallback((variableId: string) => {
    const normalized = variableId.trim();
    if (!normalized) {
      return;
    }
    setFavorites((current) => {
      const currentSet = new Set(current);
      if (currentSet.has(normalized)) {
        currentSet.delete(normalized);
      } else {
        currentSet.add(normalized);
      }
      const next = Array.from(currentSet);
      writeFavorites(modelId, next);
      return next;
    });
  }, [modelId]);

  return {
    favorites,
    favoriteSet,
    toggleFavorite,
  };
}