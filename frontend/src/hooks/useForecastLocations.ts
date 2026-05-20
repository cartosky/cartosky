import { useCallback, useEffect, useMemo, useState } from "react";

export interface ForecastLocation {
  id: string;
  label: string;
  lat: number;
  lon: number;
}

const FAVORITES_STORAGE_KEY = "cartosky_forecast_favorites_v1";
const RECENTS_STORAGE_KEY = "cartosky_forecast_recents_v1";
const MAX_FAVORITES = 5;
const MAX_RECENTS = 4;

export const defaultCities: ForecastLocation[] = [
  { id: "denver-co", label: "Denver, CO", lat: 39.7392, lon: -104.9903 },
  { id: "chicago-il", label: "Chicago, IL", lat: 41.8781, lon: -87.6298 },
  { id: "miami-fl", label: "Miami, FL", lat: 25.7617, lon: -80.1918 },
  { id: "seattle-wa", label: "Seattle, WA", lat: 47.6062, lon: -122.3321 },
];

function favoritesStorageKey(userId?: string): string {
  return userId ? `cartosky_forecast_favorites_${userId}` : FAVORITES_STORAGE_KEY;
}

function isForecastLocation(value: unknown): value is ForecastLocation {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<ForecastLocation>;
  return (
    typeof item.id === "string" &&
    item.id.trim().length > 0 &&
    typeof item.label === "string" &&
    item.label.trim().length > 0 &&
    typeof item.lat === "number" &&
    Number.isFinite(item.lat) &&
    typeof item.lon === "number" &&
    Number.isFinite(item.lon)
  );
}

function sanitizeLocations(value: unknown, maxItems: number): ForecastLocation[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();
  const locations: ForecastLocation[] = [];

  for (const item of value) {
    if (!isForecastLocation(item) || seen.has(item.id)) continue;
    seen.add(item.id);
    locations.push({
      id: item.id,
      label: item.label,
      lat: item.lat,
      lon: item.lon,
    });
    if (locations.length >= maxItems) break;
  }

  return locations;
}

function readLocations(key: string, maxItems: number): ForecastLocation[] {
  if (typeof window === "undefined") return [];

  try {
    return sanitizeLocations(JSON.parse(window.localStorage.getItem(key) ?? "[]"), maxItems);
  } catch {
    return [];
  }
}

function writeLocations(key: string, locations: ForecastLocation[]): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(key, JSON.stringify(locations));
  } catch {
    // Storage can be unavailable in private browsing or locked-down webviews.
  }
}

function dedupeLocations(locations: ForecastLocation[], maxItems: number): ForecastLocation[] {
  return sanitizeLocations(locations, maxItems);
}

function withoutLocation(locations: ForecastLocation[], id: string): ForecastLocation[] {
  return locations.filter(location => location.id !== id);
}

export function makeForecastLocationId(label: string, lat: number, lon: number): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  if (slug) return slug;
  return `coords-${lat.toFixed(4).replace(/[^0-9-]/g, "")}-${lon.toFixed(4).replace(/[^0-9-]/g, "")}`;
}

type StoredLocations = {
  key: string;
  items: ForecastLocation[];
};

export function useForecastLocations(userId?: string) {
  const favoriteKey = useMemo(() => favoritesStorageKey(userId), [userId]);
  const [favoritesState, setFavoritesState] = useState<StoredLocations>(() => ({
    key: favoriteKey,
    items: readLocations(favoriteKey, MAX_FAVORITES),
  }));
  const [recents, setRecents] = useState<ForecastLocation[]>(() => readLocations(RECENTS_STORAGE_KEY, MAX_RECENTS));

  useEffect(() => {
    setFavoritesState({ key: favoriteKey, items: readLocations(favoriteKey, MAX_FAVORITES) });
  }, [favoriteKey]);

  useEffect(() => {
    if (favoritesState.key !== favoriteKey) return;
    writeLocations(favoriteKey, favoritesState.items);
  }, [favoriteKey, favoritesState]);

  useEffect(() => {
    writeLocations(RECENTS_STORAGE_KEY, recents);
  }, [recents]);

  const favorites = favoritesState.items;
  const favoriteIds = useMemo(() => new Set(favorites.map(location => location.id)), [favorites]);

  useEffect(() => {
    setRecents(current => current.filter(location => !favoriteIds.has(location.id)));
  }, [favoriteIds]);

  const visibleRecents = useMemo(
    () => recents.filter(location => !favoriteIds.has(location.id)).slice(0, MAX_RECENTS),
    [favoriteIds, recents],
  );
  const displayChips = useMemo(() => {
    if (favorites.length === 0 && visibleRecents.length === 0) return defaultCities;
    return [...favorites, ...visibleRecents];
  }, [favorites, visibleRecents]);

  const addFavorite = useCallback((location: ForecastLocation) => {
    setFavoritesState(current => {
      const withoutCurrent = withoutLocation(current.items, location.id);
      if (withoutCurrent.length >= MAX_FAVORITES) return current;

      return {
        key: current.key,
        items: dedupeLocations([location, ...withoutCurrent], MAX_FAVORITES),
      };
    });
    setRecents(current => withoutLocation(current, location.id));
  }, []);

  const removeFavorite = useCallback((id: string) => {
    setFavoritesState(current => ({
      key: current.key,
      items: withoutLocation(current.items, id),
    }));
  }, []);

  const isFavorite = useCallback((id: string) => favoriteIds.has(id), [favoriteIds]);

  const addRecent = useCallback((location: ForecastLocation) => {
    setRecents(current => {
      if (favoriteIds.has(location.id)) return withoutLocation(current, location.id);
      return dedupeLocations([location, ...withoutLocation(current, location.id)], MAX_RECENTS);
    });
  }, [favoriteIds]);

  return {
    favorites,
    recents: visibleRecents,
    displayChips,
    addFavorite,
    removeFavorite,
    isFavorite,
    addRecent,
  };
}