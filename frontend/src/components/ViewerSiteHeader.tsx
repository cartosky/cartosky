import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useUser } from "@clerk/react";
import {
  Check,
  MapPin,
  MapPinSearch,
  Search,
  Star,
  X,
} from "lucide-react";

import { HexSignalRing } from "@/components/HexSignalRing";
import { viewerFieldTriggerClassName } from "@/components/ui/viewer-field-trigger";
import { ViewerMobileBar } from "@/components/mobile/ViewerMobileBar";
import { ViewerTopBar } from "@/components/ViewerTopBar";
import { ViewerTopProgressBar } from "@/components/ViewerTopProgressBar";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
} from "@/components/ui/select";
import type { GroupedOption } from "@/lib/app-utils";
import { API_V4_BASE } from "@/lib/config";
import { cn } from "@/lib/utils";
import { MOBILE_BAR_PX } from "@/lib/viewer-mobile";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

// ─── Shared types ────────────────────────────────────────────────────────────
type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

type LocationSearchResult = {
  display_name: string;
  latitude: number;
  longitude: number;
  timezone?: string | null;
  country_code?: string | null;
  admin1?: string | null;
  country?: string | null;
};

type ViewerFavoriteLocation = LocationSearchResult & {
  id: string;
};

const VIEWER_LOCATION_FAVORITES_STORAGE_KEY = "cartosky_viewer_location_favorites_v1";
const VIEWER_LOCATION_FAVORITES_METADATA_KEY = "viewerLocationFavorites";
const MAX_VIEWER_LOCATION_FAVORITES = 5;

const DESKTOP_TOPBAR_POPOVER_OFFSET = 10;
const DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP = 74;
const DESKTOP_ICON_BUTTON_CLASSNAME = "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[5px] border border-transparent bg-transparent px-0 text-white/50 shadow-none transition-[background,color] duration-100 hover:bg-white/10 hover:text-white/90 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:h-11 pointer-coarse:w-11";
const DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME = "bg-cyan-300/[0.12] text-cyan-200 hover:bg-cyan-300/[0.12] hover:text-cyan-200";

function viewerLocationId(result: Pick<LocationSearchResult, "display_name" | "latitude" | "longitude">): string {
  const label = result.display_name
    .trim()
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (label) return label;
  return `coords-${result.latitude.toFixed(4).replace(/[^0-9-]/g, "")}-${result.longitude.toFixed(4).replace(/[^0-9-]/g, "")}`;
}

function toViewerFavoriteLocation(result: LocationSearchResult): ViewerFavoriteLocation {
  return {
    id: viewerLocationId(result),
    display_name: result.display_name,
    latitude: result.latitude,
    longitude: result.longitude,
    timezone: result.timezone ?? null,
    country_code: result.country_code ?? null,
    admin1: result.admin1 ?? null,
    country: result.country ?? null,
  };
}

function isViewerFavoriteLocation(value: unknown): value is ViewerFavoriteLocation {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<ViewerFavoriteLocation>;
  return (
    typeof item.id === "string" &&
    item.id.trim().length > 0 &&
    typeof item.display_name === "string" &&
    item.display_name.trim().length > 0 &&
    typeof item.latitude === "number" &&
    Number.isFinite(item.latitude) &&
    typeof item.longitude === "number" &&
    Number.isFinite(item.longitude)
  );
}

function sanitizeViewerFavorites(value: unknown): ViewerFavoriteLocation[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const favorites: ViewerFavoriteLocation[] = [];
  for (const item of value) {
    if (!isViewerFavoriteLocation(item) || seen.has(item.id)) continue;
    seen.add(item.id);
    favorites.push({
      id: item.id,
      display_name: item.display_name,
      latitude: item.latitude,
      longitude: item.longitude,
      timezone: item.timezone ?? null,
      country_code: item.country_code ?? null,
      admin1: item.admin1 ?? null,
      country: item.country ?? null,
    });
    if (favorites.length >= MAX_VIEWER_LOCATION_FAVORITES) break;
  }
  return favorites;
}

function readViewerFavoritesFromStorage(storageKey: string): ViewerFavoriteLocation[] {
  if (typeof window === "undefined") return [];
  try {
    return sanitizeViewerFavorites(JSON.parse(window.localStorage.getItem(storageKey) ?? "[]"));
  } catch {
    return [];
  }
}

function writeViewerFavoritesToStorage(storageKey: string, favorites: ViewerFavoriteLocation[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(sanitizeViewerFavorites(favorites)));
  } catch {
    // Storage can be unavailable in private browsing or locked-down webviews.
  }
}

function useViewerLocationFavorites() {
  const { user, isLoaded } = useUser();
  const userStorageKey = user?.id ? `${VIEWER_LOCATION_FAVORITES_STORAGE_KEY}_${user.id}` : VIEWER_LOCATION_FAVORITES_STORAGE_KEY;
  const [favorites, setFavorites] = useState<ViewerFavoriteLocation[]>(() => readViewerFavoritesFromStorage(VIEWER_LOCATION_FAVORITES_STORAGE_KEY));

  useEffect(() => {
    if (!isLoaded) return;
    if (user) {
      const hasClerkFavorites = Object.prototype.hasOwnProperty.call(
        user.unsafeMetadata ?? {},
        VIEWER_LOCATION_FAVORITES_METADATA_KEY
      );
      const clerkFavorites = sanitizeViewerFavorites(user.unsafeMetadata?.[VIEWER_LOCATION_FAVORITES_METADATA_KEY]);
      setFavorites(hasClerkFavorites ? clerkFavorites : readViewerFavoritesFromStorage(userStorageKey));
      return;
    }
    setFavorites(readViewerFavoritesFromStorage(VIEWER_LOCATION_FAVORITES_STORAGE_KEY));
  }, [isLoaded, user, userStorageKey]);

  const persistFavorites = useCallback(async (nextFavorites: ViewerFavoriteLocation[]) => {
    const sanitized = sanitizeViewerFavorites(nextFavorites);
    if (user) {
      try {
        await user.update({
          unsafeMetadata: {
            ...user.unsafeMetadata,
            [VIEWER_LOCATION_FAVORITES_METADATA_KEY]: sanitized,
          },
        });
        writeViewerFavoritesToStorage(userStorageKey, sanitized);
        return;
      } catch {
        writeViewerFavoritesToStorage(userStorageKey, sanitized);
        return;
      }
    }
    writeViewerFavoritesToStorage(VIEWER_LOCATION_FAVORITES_STORAGE_KEY, sanitized);
  }, [user, userStorageKey]);

  const favoriteIds = useMemo(() => new Set(favorites.map((location) => location.id)), [favorites]);
  const isFavorite = useCallback((location: LocationSearchResult) => favoriteIds.has(viewerLocationId(location)), [favoriteIds]);
  const toggleFavorite = useCallback((location: LocationSearchResult): boolean => {
    const favorite = toViewerFavoriteLocation(location);
    const exists = favoriteIds.has(favorite.id);
    if (!exists && favorites.length >= MAX_VIEWER_LOCATION_FAVORITES) {
      return false;
    }
    const nextFavorites = exists
      ? favorites.filter((item) => item.id !== favorite.id)
      : sanitizeViewerFavorites([favorite, ...favorites.filter((item) => item.id !== favorite.id)]);
    setFavorites(nextFavorites);
    void persistFavorites(nextFavorites);
    return true;
  }, [favoriteIds, favorites, persistFavorites]);

  return { favorites, isFavorite, toggleFavorite };
}


const GROUP_ORDER = ["MODELS", "ENSEMBLES", "FORECASTS", "OBSERVATIONS", "SURFACE", "PRECIPITATION", "PRECIP ANOMALIES", "SEVERE", "AIR QUALITY", "UPPER AIR", "OUTLOOKS", "ENSEMBLE"];

function spcVariableLabel(option: VariableOption): string {
  switch (option.value) {
    case "convective": return "Categorical";
    case "tornado_prob": return "Tornado";
    case "wind_prob": return "Wind";
    case "hail_prob": return "Hail";
    default: return option.label;
  }
}

export function NavbarSelect(props: {
  value: string;
  onValueChange: (value: string) => void;
  options: (Option | VariableOption | GroupedOption)[];
  disabled?: boolean;
  placeholder: string;
  grouped?: boolean;
  selectedLabelOverride?: string;
  highlightState?: boolean;
  menuActionLabel?: string | null;
  menuActionDescription?: string | null;
  onMenuAction?: () => void;
  minWidth?: string;
  contentOffset?: number;
  contentClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const {
    value,
    onValueChange,
    options,
    disabled,
    placeholder,
    grouped,
    selectedLabelOverride,
    highlightState = false,
    menuActionLabel,
    menuActionDescription,
    onMenuAction,
    minWidth = "min-w-[120px]",
    contentOffset,
    contentClassName,
  } = props;

  const selectedLabel = selectedLabelOverride ?? options.find((o) => o.value === value)?.label ?? placeholder;

  let content: React.ReactNode;
  if (grouped) {
    const groups = new Map<string, Option[]>();
    const ungrouped: Option[] = [];
    for (const opt of options) {
      const g = "group" in opt && typeof opt.group === "string" ? opt.group : null;
      if (g) {
        let list = groups.get(g);
        if (!list) { list = []; groups.set(g, list); }
        list.push(opt);
      } else {
        ungrouped.push(opt);
      }
    }
    const ordered = GROUP_ORDER.filter((g) => groups.has(g));
    for (const g of groups.keys()) {
      if (!ordered.includes(g)) ordered.push(g);
    }
    content = (
      <>
        {ordered.map((g) => (
          <SelectGroup key={g}>
            <SelectLabel className="px-2 pt-1.5 pb-0.5 text-[11px] font-semibold uppercase tracking-wider text-white/60">
              {g}
            </SelectLabel>
            {groups.get(g)!.map((opt) => (
              <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
                {opt.label}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
        {ungrouped.map((opt) => (
          <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
            {opt.label}
          </SelectItem>
        ))}
      </>
    );
  } else {
    content = options.map((opt) => (
      <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
        {opt.label}
      </SelectItem>
    ));
  }

  const resolvedContent =
    menuActionLabel && onMenuAction ? (
      <>
        <button
          type="button"
          onClick={() => { setOpen(false); onMenuAction(); }}
          className="flex w-full flex-col items-start rounded-md px-3 py-2 text-left transition-colors duration-150 hover:bg-white/10"
        >
          <span className="text-xs font-semibold text-cyan-100">{menuActionLabel}</span>
          {menuActionDescription ? (
            <span className="mt-0.5 text-[11px] text-cyan-100/60">{menuActionDescription}</span>
          ) : null}
        </button>
        <SelectSeparator className="my-1 bg-white/10" />
        {content}
      </>
    ) : content;

  return (
    <Select
      value={value}
      onValueChange={(v) => { setOpen(false); onValueChange(v); }}
      open={open}
      onOpenChange={setOpen}
      disabled={disabled || options.length === 0}
    >
      <SelectTrigger
        className={viewerFieldTriggerClassName({
          className: cn(
            "focus:ring-0 focus:ring-offset-0 [&>span]:line-clamp-none data-[state=open]:border-cyan-300/25 data-[state=open]:bg-cyan-300/[0.08] data-[state=open]:text-cyan-100",
            minWidth,
            highlightState
              ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100 hover:bg-cyan-300/[0.12]"
              : ""
          ),
        })}
      >
        <span className="whitespace-nowrap">{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent sideOffset={contentOffset} className={contentClassName}>{resolvedContent}</SelectContent>
    </Select>
  );
}

// ─── Display toggle row ───────────────────────────────────────────────────────
export function DisplayRow({
  label,
  icon: Icon,
  badge,
  checked,
  onToggle,
  variant = "card",
}: {
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  badge?: React.ReactNode;
  checked: boolean;
  onToggle: () => void;
  variant?: "card" | "flat";
}) {
  const flat = variant === "flat";
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "relative flex min-h-8 w-full items-center justify-between gap-3 overflow-visible text-left transition-colors duration-150 pointer-coarse:min-h-11",
        flat
          ? "rounded-md border-0 bg-transparent px-1 py-1 text-white/82 hover:bg-white/[0.045]"
          : checked
            ? "rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-white hover:bg-cyan-300/[0.11]"
            : "rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-white/82 hover:bg-white/[0.07]"
      )}
    >
      <div className={cn("flex items-center text-white", flat ? "text-[13px] font-medium" : "gap-2 text-sm font-semibold")}>
        {Icon ? <Icon className="h-4 w-4 text-white/72" /> : null}
        {label}
      </div>
      {flat ? (
        <span
          data-testid="rail-toggle-switch"
          aria-hidden="true"
          className={cn(
            "relative h-5 w-9 shrink-0 rounded-full border transition-[background-color,border-color] duration-150",
            checked
              ? "border-cyan-200/35 bg-cyan-400/75"
              : "border-white/28 bg-white/[0.07]",
          )}
        >
          <span
            className={cn(
              "absolute left-0.5 top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-white shadow-sm transition-transform",
              checked && "translate-x-4",
            )}
          />
        </span>
      ) : (
        <span className={cn("font-['IBM_Plex_Mono',monospace] text-[11px] font-medium", checked ? "text-cyan-300/90" : "text-white/38")}>
          {checked ? "On" : "Off"}
        </span>
      )}
      {badge}
    </button>
  );
}

export function RegionUtilitySelect({
  value,
  onValueChange,
  onLocationJump,
  options,
  disabled,
  currentRegionLabel,
  tourTarget,
  variant = "icon",
  inlinePanel = false,
  inlinePanelClassName,
  onOpenChange,
  onLocationSelected,
  valueTestId,
  fieldPrefix = false,
  panelPlacement = "align-end",
  openSignal = 0,
}: {
  value: string;
  onValueChange: (value: string) => void;
  onLocationJump?: (lat: number, lon: number, zoom?: number, source?: "search" | "geolocation") => void;
  options: Option[];
  disabled?: boolean;
  currentRegionLabel: string;
  tourTarget?: string;
  variant?: "icon" | "field";
  inlinePanel?: boolean;
  inlinePanelClassName?: string;
  onOpenChange?: (open: boolean) => void;
  onLocationSelected?: () => void;
  /** Test hook for the §6.2 "region value visible as text" contract. */
  valueTestId?: string;
  /** Rail variant: put the field name inside the trigger instead of above it. */
  fieldPrefix?: boolean;
  /** Desktop panel placement relative to the field trigger. */
  panelPlacement?: "align-end" | "right";
  /**
   * Phase 8 (§8 decision 2): a monotonically increasing token that opens the
   * panel programmatically, so the mobile bar's ⌕ can land the user in
   * location search. `0` (the default) never opens anything, so every existing
   * mount behaves exactly as before.
   */
  openSignal?: number;
}) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const searchGenerationRef = useRef(0);
  const errorTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const openRef = useRef(open);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LocationSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isLocating, setIsLocating] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [panelTop, setPanelTop] = useState<number>(DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP);
  const [panelLeft, setPanelLeft] = useState<number>(16);
  const [currentLocation, setCurrentLocation] = useState<ViewerFavoriteLocation | null>(null);
  const { favorites, isFavorite, toggleFavorite } = useViewerLocationFavorites();

  const activeSearch = query.trim().length > 0;
  const currentLocationIsFavorite = currentLocation ? isFavorite(currentLocation) : false;

  const setOpenState = useCallback((nextOpen: boolean) => {
    setOpen(nextOpen);
    onOpenChange?.(nextOpen);
  }, [onOpenChange]);

  const updatePanelPosition = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    const panelWidth = 296;
    const viewportGutter = 16;
    const preferredLeft = panelPlacement === "right"
      ? rect.right + DESKTOP_TOPBAR_POPOVER_OFFSET
      : rect.right - panelWidth;
    const maxLeft = Math.max(viewportGutter, window.innerWidth - panelWidth - viewportGutter);
    setPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
    setPanelLeft(Math.min(Math.max(preferredLeft, viewportGutter), maxLeft));
  }, [panelPlacement]);

  const clearInlineError = useCallback(() => {
    if (errorTimerRef.current) {
      window.clearTimeout(errorTimerRef.current);
      errorTimerRef.current = null;
    }
    setInlineError(null);
  }, []);

  const showInlineError = useCallback((message: string) => {
    if (errorTimerRef.current) {
      window.clearTimeout(errorTimerRef.current);
    }
    setInlineError(message);
    errorTimerRef.current = window.setTimeout(() => {
      errorTimerRef.current = null;
      setInlineError(null);
    }, 2800);
  }, []);

  const resetSearch = useCallback(() => {
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    searchGenerationRef.current += 1;
    setQuery("");
    setResults([]);
    setIsSearching(false);
    clearInlineError();
  }, [clearInlineError]);

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Programmatic open (§8 ⌕). Keyed on the token alone: adding the setter
  // callbacks would re-open the panel every time the parent re-renders.
  useEffect(() => {
    if (!openSignal) {
      return;
    }
    updatePanelPosition();
    setOpenState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSignal]);

  useEffect(() => {
    if (!open) {
      return;
    }
    updatePanelPosition();
    function onPointerDown(event: MouseEvent | TouchEvent) {
      if (!(event.target instanceof Node)) {
        return;
      }
      if (triggerRef.current?.contains(event.target)) {
        return;
      }
      if (panelRef.current?.contains(event.target)) {
        return;
      }
      setOpenState(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpenState(false);
      }
    }
    window.addEventListener("resize", updatePanelPosition);
    window.addEventListener("scroll", updatePanelPosition, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", updatePanelPosition);
      window.removeEventListener("scroll", updatePanelPosition, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpenState, updatePanelPosition]);

  useEffect(() => {
    if (!open || !inlinePanel) {
      return;
    }
    // Wait for the mobile sheet expand animation before scrolling/focusing so iOS shows the keyboard reliably.
    const timer = window.setTimeout(() => {
      triggerRef.current?.scrollIntoView({ block: "nearest" });
      searchInputRef.current?.focus();
    }, 380);
    return () => window.clearTimeout(timer);
  }, [inlinePanel, open]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!openRef.current) {
      return;
    }
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }

    if (!trimmed) {
      setResults([]);
      setIsSearching(false);
      return;
    }

    if (trimmed.length < 2) {
      setResults([]);
      setIsSearching(false);
      return;
    }

    const generation = searchGenerationRef.current + 1;
    searchGenerationRef.current = generation;
    setIsSearching(true);
    debounceRef.current = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_V4_BASE}/locations/search?q=${encodeURIComponent(trimmed)}`, {
          cache: "no-store",
        });
        if (searchGenerationRef.current !== generation) {
          return;
        }
        if (!response.ok) {
          throw new Error("Location search is temporarily unavailable.");
        }
        const payload = (await response.json()) as { results?: LocationSearchResult[] };
        setResults(Array.isArray(payload.results) ? payload.results.slice(0, 5) : []);
      } catch (error) {
        if (searchGenerationRef.current !== generation) {
          return;
        }
        setResults([]);
        showInlineError("Location search is temporarily unavailable.");
      } finally {
        if (searchGenerationRef.current === generation) {
          setIsSearching(false);
        }
      }
    }, 300);

    return () => {
      if (debounceRef.current) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [query, showInlineError]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        window.clearTimeout(debounceRef.current);
      }
      searchGenerationRef.current += 1;
      if (errorTimerRef.current) {
        window.clearTimeout(errorTimerRef.current);
      }
    };
  }, []);

  function closeAfterLocationJump() {
    setOpenState(false);
    resetSearch();
    setIsLocating(false);
  }

  function handleLocationResultSelect(result: LocationSearchResult) {
    setCurrentLocation(toViewerFavoriteLocation(result));
    onLocationJump?.(result.latitude, result.longitude, 10, "search");
    closeAfterLocationJump();
    onLocationSelected?.();
  }

  function handleFavoriteToggle(location: LocationSearchResult) {
    if (!toggleFavorite(location)) {
      showInlineError(`Save up to ${MAX_VIEWER_LOCATION_FAVORITES} favorite locations.`);
    }
  }

  function handleUseMyLocation() {
    if (!navigator.geolocation) {
      showInlineError("Geolocation is not available in this browser.");
      return;
    }
    clearInlineError();
    setIsLocating(true);
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const lat = position.coords.latitude;
        const lon = position.coords.longitude;
        try {
          const response = await fetch(`${API_V4_BASE}/locations/reverse?lat=${encodeURIComponent(String(lat))}&lon=${encodeURIComponent(String(lon))}`);
          if (response.ok) {
            const payload = (await response.json()) as { location?: LocationSearchResult | null };
            if (payload.location) {
              setCurrentLocation(toViewerFavoriteLocation(payload.location));
            }
          }
        } catch {
          // The map jump still works if nearest-city lookup is unavailable.
        }
        onLocationJump?.(lat, lon, 10, "geolocation");
        closeAfterLocationJump();
        onLocationSelected?.();
      },
      () => {
        setIsLocating(false);
        showInlineError("Unable to access your location.");
      },
      {
        enableHighAccuracy: false,
        timeout: 10000,
        maximumAge: 300000,
      }
    );
  }

  function secondaryLocationLabel(result: LocationSearchResult): string | null {
    const pieces: string[] = [];
    const admin1 = result.admin1?.trim();
    const country = result.country?.trim();
    if (admin1) {
      pieces.push(admin1);
    }
    if (country && (!admin1 || country.toLowerCase() !== admin1.toLowerCase())) {
      pieces.push(country);
    }
    if (pieces.length === 0 && result.country_code && result.country_code !== "US") {
      pieces.push(result.country_code);
    }
    return pieces.length > 0 ? pieces.join(" • ") : null;
  }

  const locationPanel = (
    <div
      ref={panelRef}
      data-testid="region-picker-panel"
      className={cn(
        inlinePanel
          ? "mt-2 flex min-h-0 w-full flex-1 flex-col overflow-hidden rounded-xl border bg-[#04101e]/[0.92] shadow-[inset_0_1px_0_rgba(100,180,255,0.08)]"
          : "fixed z-[90] w-[296px] overflow-hidden rounded-2xl border bg-[#04101e]/[0.92] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
        activeSearch ? "border-[rgba(55,138,221,0.35)]" : "border-[#1a3a5c]/60",
        inlinePanel ? inlinePanelClassName : null
      )}
      style={inlinePanel ? undefined : { top: panelTop, left: panelLeft }}
      role={inlinePanel ? "dialog" : undefined}
      aria-label={inlinePanel ? "Region picker" : undefined}
    >
      <div className="shrink-0 border-b border-white/8 px-3 py-3">
        <label className={cn("flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 transition-colors focus-within:border-cyan-300/30 focus-within:bg-white/[0.06]", inlinePanel && "min-h-11")}>
          <Search className="h-3.5 w-3.5 flex-none text-white/45" />
          <input
            ref={searchInputRef}
            value={query}
            onChange={(event) => {
              clearInlineError();
              setQuery(event.target.value);
            }}
            placeholder="Search city or zip…"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            inputMode="search"
            enterKeyHint="search"
            type="search"
            className={cn(
              "viewer-touch-input w-full min-w-0 bg-transparent text-white outline-none placeholder:text-white/35 min-h-8 pointer-coarse:min-h-11",
              inlinePanel ? "text-base" : "text-sm"
            )}
          />
          {query.trim().length > 0 ? (
            <button
              type="button"
              onClick={() => {
                resetSearch();
                searchInputRef.current?.focus();
              }}
              className={cn("flex flex-none items-center justify-center rounded-full text-white/34 transition hover:bg-white/8 hover:text-white/68", inlinePanel ? "h-11 w-11" : "h-5 w-5")}
              aria-label="Clear location search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </label>
      </div>

      <div className={cn(
        "px-2 py-2",
        inlinePanel ? "picker-scroll min-h-0 flex-1 overflow-y-auto" : "max-h-[320px] overflow-y-auto"
      )}>
        {!activeSearch ? (
          <>
            {favorites.length > 0 ? (
              <>
                <div className="px-2 pb-1 pt-0.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/52">
                  Favorites
                </div>
                <div className="mb-2 space-y-0.5">
                  {favorites.map((location) => (
                    <div key={location.id} className={cn("group flex items-center gap-1 rounded-md hover:bg-cyan-300/14", inlinePanel && "min-h-11")}>
                      <button
                        type="button"
                        onClick={() => handleLocationResultSelect(location)}
                        className={cn("min-w-0 flex-1 rounded-md py-1.5 pl-3 pr-1 text-left text-xs font-medium text-white/86 outline-none transition-colors group-hover:text-cyan-50 min-h-8 pointer-coarse:min-h-11", inlinePanel && "min-h-11")}
                      >
                        <span className="block truncate">{location.display_name}</span>
                        {secondaryLocationLabel(location) ? (
                          <span className="mt-0.5 block truncate text-[11px] font-normal text-white/45 group-hover:text-cyan-100/70">
                            {secondaryLocationLabel(location)}
                          </span>
                        ) : null}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleFavoriteToggle(location)}
                        className={cn("mr-1 flex shrink-0 items-center justify-center rounded-md text-amber-300 transition hover:bg-white/10", inlinePanel ? "h-11 w-11" : "h-7 w-7")}
                        title="Remove favorite"
                        aria-label={`Remove ${location.display_name} from favorites`}
                      >
                        <Star className="h-3.5 w-3.5 fill-current" />
                      </button>
                    </div>
                  ))}
                </div>
              </>
            ) : null}

            {currentLocation && !currentLocationIsFavorite ? (
              <div className={cn("mb-2 rounded-lg border border-cyan-300/12 bg-cyan-300/[0.06] px-2 py-1.5", inlinePanel && "min-h-11")}>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleLocationResultSelect(currentLocation)}
                    className={cn("min-w-0 flex-1 text-left", inlinePanel && "min-h-11")}
                  >
                    <span className="block truncate text-xs font-medium text-white/88">{currentLocation.display_name}</span>
                    <span className="mt-0.5 block text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/55">Selected location</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleFavoriteToggle(currentLocation)}
                    className={cn("flex shrink-0 items-center justify-center rounded-md text-white/50 transition hover:bg-white/10 hover:text-amber-300", inlinePanel ? "h-11 w-11" : "h-7 w-7")}
                    title="Save favorite"
                    aria-label={`Save ${currentLocation.display_name} as favorite`}
                  >
                    <Star className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ) : null}

            <div className="px-2 pb-1 pt-0.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/52">
              Region
            </div>
            <div className="space-y-0.5">
              {options.map((opt) => {
                const selected = opt.value === value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => {
                      onValueChange(opt.value);
                      setOpenState(false);
                      clearInlineError();
                    }}
                    className={cn(
                      "relative flex w-full items-center rounded-md py-1.5 pl-8 pr-2 text-left text-xs font-medium text-white/86 outline-none transition-colors hover:bg-cyan-300/15 hover:text-cyan-50 min-h-8 pointer-coarse:min-h-11",
                      inlinePanel && "min-h-11",
                      selected && "bg-cyan-300/14 text-cyan-50"
                    )}
                  >
                    <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center text-cyan-200">
                      {selected ? <Check className="h-4 w-4" /> : null}
                    </span>
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </>
        ) : (
            <div className="space-y-0.5">
            {isSearching && results.length === 0 ? (
              <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-white/58">
                <HexSignalRing size="xs" />
                Searching…
              </div>
            ) : query.trim().length < 2 ? (
              <div className="rounded-lg px-3 py-2 text-xs text-white/48">
                Type at least 2 characters.
              </div>
            ) : results.length === 0 ? (
              <div className="rounded-lg px-3 py-2 text-xs text-white/48">
                No locations found.
              </div>
            ) : (
              results.map((result) => {
                const favorited = isFavorite(result);
                return (
                  <div
                    key={`${result.display_name}-${result.latitude}-${result.longitude}`}
                    className={cn("group flex items-center gap-1 rounded-lg transition-colors hover:bg-cyan-300/14 hover:text-cyan-50", inlinePanel && "min-h-11")}
                  >
                    <button
                      type="button"
                      onClick={() => handleLocationResultSelect(result)}
                      className={cn("min-w-0 flex-1 rounded-lg px-3 py-2 text-left", inlinePanel && "min-h-11")}
                    >
                      <span className="block truncate text-sm font-medium text-white/92 transition-colors group-hover:text-cyan-50">
                        {result.display_name}
                      </span>
                      {secondaryLocationLabel(result) ? (
                        <span className="mt-0.5 block truncate text-[11px] text-white/48 transition-colors group-hover:text-cyan-100/72">
                          {secondaryLocationLabel(result)}
                        </span>
                      ) : null}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleFavoriteToggle(result)}
                      className={cn(
                        "mr-1 flex shrink-0 items-center justify-center rounded-md transition hover:bg-white/10",
                        inlinePanel ? "h-11 w-11" : "h-8 w-8",
                        favorited ? "text-amber-300" : "text-white/40 hover:text-amber-300"
                      )}
                      title={favorited ? "Remove favorite" : "Save favorite"}
                      aria-label={favorited ? `Remove ${result.display_name} from favorites` : `Save ${result.display_name} as favorite`}
                    >
                      <Star className={cn("h-3.5 w-3.5", favorited ? "fill-current" : "")} />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        )}

        {inlineError ? (
          <div className="mt-2 rounded-lg border border-rose-300/18 bg-rose-300/10 px-3 py-2 text-[11px] text-rose-100">
            {inlineError}
          </div>
        ) : null}
      </div>

      <div className="shrink-0 border-t border-white/8 px-2 py-2">
        <button
          type="button"
          onClick={handleUseMyLocation}
          className={cn("flex min-h-8 w-full items-center justify-between rounded-lg px-3 py-2 text-left transition-colors hover:bg-cyan-300/12 pointer-coarse:min-h-11", inlinePanel && "min-h-11")}
        >
          <span className="flex items-center gap-2 text-sm font-medium text-white/88">
            <MapPin className="h-3.5 w-3.5 text-cyan-200/85" />
            Use my location
          </span>
          {isLocating ? (
            <HexSignalRing size="xs" />
          ) : null}
        </button>
      </div>
    </div>
  );

  const panel = open ? (inlinePanel ? locationPanel : createPortal(locationPanel, document.body)) : null;

  return (
    <div
      className={cn(inlinePanel ? "flex min-h-0 flex-col" : "shrink-0")}
      {...(tourTarget ? { "data-tour-target": tourTarget } : {})}
    >
      <button
        ref={triggerRef}
        type="button"
        title={`Region: ${currentRegionLabel}`}
        aria-label={`Region: ${currentRegionLabel}`}
        aria-expanded={open}
        aria-haspopup={inlinePanel ? "dialog" : undefined}
        disabled={disabled || options.length === 0}
        onClick={() => {
          if (disabled || options.length === 0) {
            return;
          }
          updatePanelPosition();
          setOpenState(!open);
        }}
        className={cn(
          variant === "field"
            ? cn("flex w-full items-center justify-between rounded-lg border border-white/10 bg-white/[0.045] px-3 text-left text-sm font-medium text-white/88 transition hover:border-cyan-300/22 hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:opacity-50", inlinePanel ? "h-11" : "h-9 pointer-coarse:h-11")
            : DESKTOP_ICON_BUTTON_CLASSNAME,
          open && (variant === "field" ? "border-cyan-300/28 bg-cyan-300/[0.08] text-cyan-50" : DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME)
        )}
      >
        {variant === "field" ? (
          fieldPrefix ? (
            <span className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-white/48">Region</span>
              <span
                aria-hidden="true"
                data-testid="region-prefix-dot"
                className="h-1 w-1 shrink-0 rounded-full bg-cyan-300/70"
              />
              <span className="truncate text-white/88" {...(valueTestId ? { "data-testid": valueTestId } : {})}>
                {currentRegionLabel}
              </span>
            </span>
          ) : (
            <>
              <span className="truncate" {...(valueTestId ? { "data-testid": valueTestId } : {})}>{currentRegionLabel}</span>
              <MapPinSearch className="h-3.5 w-3.5 shrink-0 text-cyan-100/70" />
            </>
          )
        ) : (
          <span className="flex h-full w-full items-center justify-center">
            <MapPinSearch className="h-3.5 w-3.5" />
          </span>
        )}
      </button>

      {panel}
    </div>
  );
}

export default function ViewerSiteHeader() {
  const toolbar = useViewerToolbar();
  const isViewerDesktop = (
    toolbar?.layoutMode === "desktop"
    || toolbar?.layoutMode === "tablet-touch"
    || toolbar?.layoutMode === undefined
  );
  const isViewerMobile = !isViewerDesktop;
  const headerRef = useRef<HTMLElement>(null);

  // Measured header-height contract (Phase 4, baselines re-based in Phase 6,
  // mobile re-based in Phase 8): the rail-mode bar is 48px, the Phase 8 mobile
  // bar is 52px (§8 State A). Under a coarse
  // pointer a 44px target can still wrap the bar taller than its baseline, so
  // publish only the growth beyond it — map padding, the rail top, the scrim,
  // the zoom stack, and header panels consume the variable.
  useEffect(() => {
    const element = headerRef.current;
    if (!element) return undefined;
    const baseline = isViewerDesktop ? 48 : MOBILE_BAR_PX;
    const apply = () => {
      const extra = Math.max(0, Math.round(element.getBoundingClientRect().height - baseline));
      document.documentElement.style.setProperty("--viewer-header-extra", `${extra}px`);
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(element);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--viewer-header-extra");
    };
  }, [isViewerDesktop]);

  return (
    <header ref={headerRef} data-testid="viewer-top-bar" className="fixed inset-x-0 top-0 z-[80]">
      <div
        aria-hidden="true"
        className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
        style={{ willChange: "transform" }}
      />
      {/* Rail layouts get the Phase 6 bar; <768 gets the Phase 8 three states. */}
      {isViewerDesktop ? <ViewerTopBar /> : null}
      {isViewerMobile ? <ViewerMobileBar /> : null}
      <ViewerTopProgressBar visible={Boolean(toolbar?.isFrameSwitching)} />
    </header>
  );
}
