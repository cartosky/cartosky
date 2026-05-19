import { useCallback, useEffect, useMemo, useState } from "react";

const MODEL_FAVORITES_STORAGE_KEY = "cartosky.modelFavorites";

function readFavorites(): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(MODEL_FAVORITES_STORAGE_KEY);
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

function writeFavorites(favorites: string[]): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(MODEL_FAVORITES_STORAGE_KEY, JSON.stringify(favorites));
  } catch {
    // Ignore storage errors in private mode or quota-limited environments.
  }
}

export function useModelFavorites() {
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites());

  useEffect(() => {
    setFavorites(readFavorites());
  }, []);

  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);

  const toggleFavorite = useCallback((modelId: string) => {
    const normalized = modelId.trim();
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
      writeFavorites(next);
      return next;
    });
  }, []);

  return {
    favorites,
    favoriteSet,
    toggleFavorite,
  };
}