import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useUser } from "@clerk/react";
import { Link, NavLink } from "react-router-dom";
import {
  Boxes,
  CalendarClock,
  Check,
  GitCompareArrows,
  Layers,
  MapPin,
  MapPinSearch,
  MessageSquareText,
  Moon,
  Palette,
  Percent,
  Search,
  Settings,
  Share2,
  Star,
  Sun,
  TriangleAlert,
  X,
  ZoomIn,
} from "lucide-react";

import { MapLegend } from "@/components/map-legend";
import { ModelPicker } from "@/components/ModelPicker";
import { StatisticPicker } from "@/components/StatisticPicker";
import { VariablePicker } from "@/components/VariablePicker";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { supportsNwsWarningsOverlay, type GroupedOption } from "@/lib/app-utils";
import { BRAND_LOGO_SRC } from "@/lib/branding";
import { API_V4_BASE } from "@/lib/config";
import { useFeedbackContext } from "@/lib/feedback-context";
import { cn } from "@/lib/utils";
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
const DESKTOP_TOPBAR_SELECT_CONTENT_CLASSNAME = "data-[side=bottom]:translate-y-0";
const DESKTOP_ICON_CLUSTER_CLASSNAME = "flex items-center gap-px rounded-[7px] border-[0.5px] border-white/[0.11] bg-white/[0.06] p-0.5";
const DESKTOP_ICON_BUTTON_CLASSNAME = "inline-flex h-7 w-8 shrink-0 items-center justify-center rounded-[5px] border border-transparent bg-transparent px-0 text-white/50 shadow-none transition-[background,color] duration-100 hover:bg-white/10 hover:text-white/90 focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50";
const DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME = "bg-cyan-300/[0.12] text-cyan-200 hover:bg-cyan-300/[0.12] hover:text-cyan-200";
const DESKTOP_ICON_CLUSTER_SEPARATOR_CLASSNAME = "mx-px h-4 w-[2px] shrink-0 rounded-full bg-cyan-300/35";

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


const GROUP_ORDER = ["MODELS", "ENSEMBLES", "FORECASTS", "OBSERVATIONS", "SURFACE", "PRECIPITATION", "PRECIP ANOMALIES", "SEVERE", "UPPER AIR", "OUTLOOKS", "ENSEMBLE"];

function spcVariableLabel(option: VariableOption): string {
  switch (option.value) {
    case "convective": return "Categorical";
    case "tornado_prob": return "Tornado";
    case "wind_prob": return "Wind";
    case "hail_prob": return "Hail";
    default: return option.label;
  }
}

function NavbarSelect(props: {
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
            <SelectLabel className="px-2 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-white/60">
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
        className={cn(
          "h-8 w-auto gap-2 rounded-xl border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:ring-0 [&>span]:line-clamp-none",
          minWidth,
          highlightState
            ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100 hover:bg-cyan-300/[0.12]"
            : ""
        )}
      >
        <span className="whitespace-nowrap">{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent sideOffset={contentOffset} className={contentClassName}>{resolvedContent}</SelectContent>
    </Select>
  );
}

function HeaderSelectField({
  label,
  icon: Icon,
  children,
  tourTarget,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  tourTarget?: string;
}) {
  return (
    <div className="flex flex-col gap-1" {...(tourTarget ? { "data-tour-target": tourTarget } : {})}>
      <span className="flex items-center gap-1.5 pl-1 text-[9px] font-medium uppercase tracking-[0.18em] text-white/44">
        <Icon className="h-3 w-3" />
        {label}
      </span>
      {children}
    </div>
  );
}

// ─── Display toggle row ───────────────────────────────────────────────────────
function DisplayRow({
  label,
  icon: Icon,
  checked,
  onToggle,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-all duration-150",
        checked
          ? "border-cyan-300/20 bg-cyan-300/[0.07] text-white hover:bg-cyan-300/[0.11]"
          : "border-white/10 bg-white/[0.04] text-white/82 hover:bg-white/[0.07]"
      )}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-white">
        <Icon className="h-4 w-4 text-white/72" />
        {label}
      </div>
      <span className={cn("font-['IBM_Plex_Mono',monospace] text-[10px] font-medium", checked ? "text-cyan-300/90" : "text-white/38")}>
        {checked ? "On" : "Off"}
      </span>
    </button>
  );
}

function RegionUtilitySelect({
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
  const [panelRight, setPanelRight] = useState<number>(16);
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
    setPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
    setPanelRight(Math.max(window.innerWidth - rect.right, 16));
  }, []);

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
      className={cn(
        inlinePanel
          ? "mt-2 flex min-h-0 w-full flex-1 flex-col overflow-hidden rounded-xl border bg-[#04101e]/[0.92] shadow-[inset_0_1px_0_rgba(100,180,255,0.08)]"
          : "fixed z-[90] w-[296px] overflow-hidden rounded-2xl border bg-[#04101e]/[0.92] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
        activeSearch ? "border-[rgba(55,138,221,0.35)]" : "border-[#1a3a5c]/60",
        inlinePanel ? inlinePanelClassName : null
      )}
      style={inlinePanel ? undefined : { top: panelTop, right: panelRight }}
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
              "viewer-touch-input w-full min-w-0 bg-transparent text-white outline-none placeholder:text-white/35",
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
                <div className="px-2 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/52">
                  Favorites
                </div>
                <div className="mb-2 space-y-0.5">
                  {favorites.map((location) => (
                    <div key={location.id} className={cn("group flex items-center gap-1 rounded-md hover:bg-cyan-300/14", inlinePanel && "min-h-11")}>
                      <button
                        type="button"
                        onClick={() => handleLocationResultSelect(location)}
                        className={cn("min-w-0 flex-1 rounded-md py-1.5 pl-3 pr-1 text-left text-xs font-medium text-white/86 outline-none transition-colors group-hover:text-cyan-50", inlinePanel && "min-h-11")}
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
                    <span className="mt-0.5 block text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-100/55">Selected location</span>
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

            <div className="px-2 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/52">
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
                      "relative flex w-full items-center rounded-md py-1.5 pl-8 pr-2 text-left text-xs font-medium text-white/86 outline-none transition-colors hover:bg-cyan-300/15 hover:text-cyan-50",
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
                <div className="h-3 w-3 animate-spin rounded-full border border-cyan-300/25 border-t-cyan-300" />
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
          className={cn("flex w-full items-center justify-between rounded-lg px-3 py-2 text-left transition-colors hover:bg-cyan-300/12", inlinePanel && "min-h-11")}
        >
          <span className="flex items-center gap-2 text-sm font-medium text-white/88">
            <MapPin className="h-3.5 w-3.5 text-cyan-200/85" />
            Use my location
          </span>
          {isLocating ? (
            <div className="h-3 w-3 animate-spin rounded-full border border-cyan-300/25 border-t-cyan-300" />
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
            ? cn("flex w-full items-center justify-between rounded-lg border border-white/10 bg-white/[0.045] px-3 text-left text-sm font-medium text-white/88 transition hover:border-cyan-300/22 hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:opacity-50", inlinePanel ? "h-11" : "h-9")
            : DESKTOP_ICON_BUTTON_CLASSNAME,
          open && (variant === "field" ? "border-cyan-300/28 bg-cyan-300/[0.08] text-cyan-50" : DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME)
        )}
      >
        {variant === "field" ? (
          <>
            <span className="truncate">{currentRegionLabel}</span>
            <MapPinSearch className="h-3.5 w-3.5 shrink-0 text-cyan-100/70" />
          </>
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

// ─── Viewer toolbar inline (desktop) ─────────────────────────────────────────
function ViewerNavDesktop({ onFeedback }: { onFeedback?: () => void }) {
  const toolbar = useViewerToolbar();
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsPanelRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const legendPanelRef = useRef<HTMLDivElement>(null);
  const [legendPanelOpen, setLegendPanelOpen] = useState(false);
  const [settingsPanelTop, setSettingsPanelTop] = useState<number>(DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP);
  const [legendPanelTop, setLegendPanelTop] = useState<number>(DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP);

  const updateSettingsPanelTop = useCallback(() => {
    const rect = settingsRef.current?.getBoundingClientRect();
    if (!rect) return;
    setSettingsPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
  }, []);

  const updateLegendPanelTop = useCallback(() => {
    const rect = legendRef.current?.getBoundingClientRect();
    if (!rect) return;
    setLegendPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
  }, []);

  useEffect(() => {
    if (!toolbar?.displayPanelOpen) return;
    updateSettingsPanelTop();
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (settingsRef.current?.contains(e.target)) return;
      if (settingsPanelRef.current?.contains(e.target)) return;
      toolbar?.onDisplayPanelOpenChange(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") toolbar?.onDisplayPanelOpenChange(false);
    }
    window.addEventListener("resize", updateSettingsPanelTop);
    window.addEventListener("scroll", updateSettingsPanelTop, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", updateSettingsPanelTop);
      window.removeEventListener("scroll", updateSettingsPanelTop, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [toolbar?.displayPanelOpen, updateSettingsPanelTop]);

  useEffect(() => {
    if (!legendPanelOpen) return;
    updateLegendPanelTop();
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (legendRef.current?.contains(e.target)) return;
      if (legendPanelRef.current?.contains(e.target)) return;
      setLegendPanelOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setLegendPanelOpen(false);
    }
    window.addEventListener("resize", updateLegendPanelTop);
    window.addEventListener("scroll", updateLegendPanelTop, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", updateLegendPanelTop);
      window.removeEventListener("scroll", updateLegendPanelTop, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [legendPanelOpen, updateLegendPanelTop]);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, variableCatalog, supportedVariableIds, model, onModelChange, models,
    ensembleProducts, product, onProductChange, productAvailability,
    run, onRunChange, runs, region, onRegionChange, onLocationJump, regions,
    disabled, runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel,
    onViewLatestRun, runSelectionLocked,
    compareHref, onShare, displayPanelOpen, onDisplayPanelOpenChange,
    pointLabelsEnabled, onPointLabelsEnabledChange,
    nwsWarningsEnabled, onNwsWarningsEnabledChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legend,
  } = toolbar;
  // Ensemble stats product options (stats design §7): only products the
  // current run actually serves; the selector hides entirely when the run
  // offers nothing beyond the mean (e.g. stats still publishing).
  const availableProductOptions = (ensembleProducts ?? [])
    .filter((entry) => entry.key === "mean" || productAvailability?.[entry.key])
    .map((entry) => ({ value: entry.key, label: entry.label ?? entry.key, longLabel: entry.long_label ?? entry.label ?? entry.key }));
  const showProductSelect = availableProductOptions.length > 1;


  const runMenuOptions = useMemo(() => {
    if (!hasNewerRunAvailable) return runs;
    return runs.filter((o) => o.value !== "latest");
  }, [hasNewerRunAvailable, runs]);
  const displayVariableCatalog = model === "spc"
    ? variables.map((option) => ({ ...option, label: spcVariableLabel(option) }))
    : variables;
  const selectedRegionLabel = regions.find((option) => option.value === region)?.label ?? "Region";

  return (
    /* flex-1 so this fills all space after the logo; justify-end right-aligns
       controls while still allowing the row to wrap onto a second line
       instead of overflowing at narrow (tablet) widths. */
    <div className="flex h-full flex-1 flex-wrap items-end justify-end gap-1.5">
      {/* Controls group: selectors + divider + icons — all right-aligned */}
      <div className="flex flex-wrap items-end gap-1.5">
        {/* Primary selectors */}
        <div data-tour-target="product-variable-run" className="flex flex-wrap items-end gap-1.5 gap-y-2">
          <HeaderSelectField label="Product" icon={Boxes}>
            <ModelPicker
              value={model}
              onChange={onModelChange}
              options={models}
              disabled={disabled}
              placeholder="Model"
              minWidth="min-w-[180px] max-w-[220px]"
              panelOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
            />
          </HeaderSelectField>
          <HeaderSelectField label="Variable" icon={Layers}>
            <VariablePicker
              modelId={model}
              value={variable}
              onChange={onVariableChange}
              variableCatalog={displayVariableCatalog}
              supportedVariableIds={supportedVariableIds}
              disabled={disabled}
              placeholder="Variable"
              legend={legend}
              minWidth="min-w-[180px] max-w-[320px]"
              panelOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
            />
          </HeaderSelectField>
          {showProductSelect ? (
            <HeaderSelectField label="Statistic" icon={Percent}>
              <StatisticPicker
                value={product ?? "mean"}
                onValueChange={(value) => onProductChange?.(value)}
                options={availableProductOptions}
                disabled={disabled}
                minWidth="min-w-[120px] max-w-[200px]"
              />
            </HeaderSelectField>
          ) : null}
          <HeaderSelectField label="Run Time" icon={CalendarClock}>
            <NavbarSelect
              value={run}
              onValueChange={onRunChange}
              options={runMenuOptions}
              disabled={disabled || runSelectionLocked}
              placeholder="Run"
              selectedLabelOverride={runDisplayLabel}
              highlightState={!runSelectionLocked && hasNewerRunAvailable}
              menuActionLabel={!runSelectionLocked && hasNewerRunAvailable ? "View latest run" : null}
              menuActionDescription={
                !runSelectionLocked && hasNewerRunAvailable && latestAvailableRunLabel
                  ? `${latestAvailableRunLabel} available`
                  : null
              }
              onMenuAction={!runSelectionLocked && hasNewerRunAvailable ? onViewLatestRun : undefined}
              minWidth="min-w-[148px] max-w-[220px]"
              contentOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
              contentClassName={DESKTOP_TOPBAR_SELECT_CONTENT_CLASSNAME}
            />
          </HeaderSelectField>
        </div>

        <div className={DESKTOP_ICON_CLUSTER_CLASSNAME}>
          <RegionUtilitySelect
            value={region}
            onValueChange={onRegionChange}
            onLocationJump={onLocationJump}
            options={regions}
            disabled={disabled}
            currentRegionLabel={selectedRegionLabel}
            tourTarget="region-selector"
          />

          <div aria-hidden="true" className={DESKTOP_ICON_CLUSTER_SEPARATOR_CLASSNAME} />

          {/* Legend button */}
          <div className="relative shrink-0" ref={legendRef} data-tour-target="legend-button">
            <button
              type="button"
              onClick={() => {
                updateLegendPanelTop();
                setLegendPanelOpen((v) => !v);
              }}
              aria-expanded={legendPanelOpen}
              title="Legend"
              aria-label="Legend"
              className={cn(
                DESKTOP_ICON_BUTTON_CLASSNAME,
                legendPanelOpen ? DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME : ""
              )}
            >
              <Palette className="h-3.5 w-3.5" />
            </button>

            {legendPanelOpen ? createPortal(
              <div
                ref={legendPanelRef}
                className="fixed right-[3.25rem] z-[70] w-[220px] max-h-[calc(100vh-5rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
                style={{ top: legendPanelTop }}
              >
                <MapLegend
                  legend={legend}
                  defaultExpanded={true}
                  inline={true}
                />
              </div>
            , document.body) : null}
          </div>

          {onShare ? (
            <button
              type="button"
              onClick={onShare}
              title="Share"
              aria-label="Share"
              data-tour-target="share-button"
              className={DESKTOP_ICON_BUTTON_CLASSNAME}
            >
              <Share2 className="h-3.5 w-3.5" />
            </button>
          ) : null}

          {compareHref ? (
            <Link
              to={compareHref}
              title="Compare"
              aria-label="Compare"
              className={DESKTOP_ICON_BUTTON_CLASSNAME}
            >
              <GitCompareArrows className="h-3.5 w-3.5" />
            </Link>
          ) : null}

          {onFeedback ? (
            <button
              type="button"
              onClick={onFeedback}
              title="Send feedback"
              aria-label="Send feedback"
              data-tour-target="feedback-button"
              className={DESKTOP_ICON_BUTTON_CLASSNAME}
            >
              <MessageSquareText className="h-3.5 w-3.5" />
            </button>
          ) : null}

          <div aria-hidden="true" className={DESKTOP_ICON_CLUSTER_SEPARATOR_CLASSNAME} />

          {/* Settings / Display panel */}
          <div className="relative shrink-0" ref={settingsRef} data-tour-target="display-settings-button">
            <button
              type="button"
              onClick={() => {
                updateSettingsPanelTop();
                onDisplayPanelOpenChange(!displayPanelOpen);
              }}
              aria-expanded={displayPanelOpen}
              title="Display settings"
              aria-label="Display settings"
              className={cn(
                DESKTOP_ICON_BUTTON_CLASSNAME,
                displayPanelOpen ? DESKTOP_ICON_BUTTON_ACTIVE_CLASSNAME : ""
              )}
            >
              <Settings className="h-3.5 w-3.5" />
            </button>

          {displayPanelOpen ? createPortal(
            <div
              ref={settingsPanelRef}
              className="fixed right-4 z-[70] w-[232px] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
              style={{ top: settingsPanelTop }}
            >
              {/* Panel header */}
              <div className="flex items-center justify-between border-b border-[#1a3a5c]/50 px-4 py-3">
                <div>
                  <div className="font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.22em] text-cyan-300/60">
                    Display
                  </div>
                  <div className="mt-0.5 text-[11px] text-white/52">Map overlays &amp; reference aids</div>
                </div>
                <button
                  type="button"
                  onClick={() => onDisplayPanelOpenChange(false)}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-md text-white/32 transition-colors hover:text-white/72"
                  aria-label="Close display panel"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>

              <div className="space-y-1.5 px-3 py-3">
                <DisplayRow
                  label="City Labels"
                  icon={MapPin}
                  checked={pointLabelsEnabled}
                  onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
                />
                {supportsNwsWarningsOverlay(model, variable) ? (
                  <DisplayRow
                    label="NWS Warnings"
                    icon={TriangleAlert}
                    checked={nwsWarningsEnabled}
                    onToggle={() => onNwsWarningsEnabledChange(!nwsWarningsEnabled)}
                  />
                ) : null}
                <DisplayRow
                  label="Zoom Controls"
                  icon={ZoomIn}
                  checked={zoomControlsVisible}
                  onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
                />
                <button
                  type="button"
                  onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left transition-all duration-150 hover:bg-white/[0.07]"
                >
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    {basemapMode === "dark"
                      ? <Moon className="h-4 w-4 text-white/60" />
                      : <Sun className="h-4 w-4 text-white/60" />}
                    Basemap
                  </div>
                  <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/80">
                    {basemapMode === "dark" ? "Dark" : "Light"}
                  </span>
                </button>

                <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">Opacity</span>
                    <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/80">
                      {Math.round(opacity * 100)}%
                    </span>
                  </div>
                  <Slider
                    value={[Math.round(opacity * 100)]}
                    onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
                    min={0}
                    max={100}
                    step={1}
                    className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
                  />
                </div>

                {toolbar.onReplayTour ? (
                  <button
                    type="button"
                    onClick={() => {
                      toolbar.onReplayTour?.();
                      onDisplayPanelOpenChange(false);
                    }}
                    className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left transition-all duration-150 hover:bg-white/[0.07]"
                  >
                    <span className="text-sm font-semibold text-white">Replay Tour</span>
                    <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/70">?</span>
                  </button>
                ) : null}

                <div className="border-t border-white/8 pt-2 text-[10px] leading-relaxed text-white/32">
                  Maps:{" "}
                  <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">MapLibre</a>
                  {" "}·{" "}
                  <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">OSM</a>
                  {" "}·{" "}
                  <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">CARTO</a>
                </div>
              </div>
            </div>
          , document.body) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Viewer toolbar mobile/tablet (slide-up sheet) ───────────────────────────
function ViewerNavMobile({ onFeedback }: { onFeedback?: () => void }) {
  const toolbar = useViewerToolbar();
  const [sheetSnap, setSheetSnap] = useState<"closed" | "peek" | "full">("closed");
  const [activeTab, setActiveTab] = useState<"selection" | "display">("selection");
  const [mobileModelPickerOpen, setMobileModelPickerOpen] = useState(false);
  const [mobileVariablePickerOpen, setMobileVariablePickerOpen] = useState(false);
  const [mobileRegionPickerOpen, setMobileRegionPickerOpen] = useState(false);
  const dragStartY = useRef<number | null>(null);
  const pickerReturnSnap = useRef<"peek" | "full" | null>(null);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, variableCatalog, supportedVariableIds, model, onModelChange, models,
    ensembleProducts, product, onProductChange, productAvailability,
    run, onRunChange, runs, region, onRegionChange, onLocationJump, regions, disabled,
    runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    runSelectionLocked, onShare, pointLabelsEnabled, onPointLabelsEnabledChange,
    nwsWarningsEnabled, onNwsWarningsEnabledChange, legendVisible,
    onLegendVisibleChange, basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legendPopoverOpen, onLegendPopoverOpenChange,
    layoutMode, legend, mobileControlsOpen, onMobileControlsOpenChange,
  } = toolbar;
  // Ensemble stats product options (stats design §7): only products the
  // current run actually serves; the selector hides entirely when the run
  // offers nothing beyond the mean (e.g. stats still publishing).
  const availableProductOptions = (ensembleProducts ?? [])
    .filter((entry) => entry.key === "mean" || productAvailability?.[entry.key])
    .map((entry) => ({ value: entry.key, label: entry.label ?? entry.key, longLabel: entry.long_label ?? entry.label ?? entry.key }));
  const showProductSelect = availableProductOptions.length > 1;


  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const isPhoneLayout = !isTabletTouchLayout;
  const sheetOpen = sheetSnap !== "closed";
  const mobilePickerOpen = mobileModelPickerOpen || mobileVariablePickerOpen || mobileRegionPickerOpen;

  // Sync external open requests (e.g. from the bottom bar) into local sheetSnap
  useEffect(() => {
    if (mobileControlsOpen && sheetSnap === "closed") {
      setSheetSnap("peek");
    } else if (!mobileControlsOpen && sheetSnap !== "closed") {
      setSheetSnap("closed");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mobileControlsOpen]);

  const displayVariables = model === "spc"
    ? variables.map((o) => ({ ...o, label: spcVariableLabel(o) }))
    : variables;

  const runMenuOptions = hasNewerRunAvailable
    ? runs.filter((o) => o.value !== "latest")
    : runs;

  const selectedVariableLabel = displayVariables.find((o) => o.value === variable)?.label ?? "Variable";
  const selectedModelLabel = models.find((o) => o.value === model)?.label ?? "Model";
  const selectedRegionLabel = regions.find((option) => option.value === region)?.label ?? "Region";

  useEffect(() => {
    if (!sheetOpen) {
      document.body.style.removeProperty("overflow");
      document.body.style.removeProperty("overflow-x");
      return;
    }
    const previousOverflow = document.body.style.overflow;
    const previousOverflowX = document.body.style.overflowX;
    document.body.style.overflow = "hidden";
    document.body.style.overflowX = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      document.body.style.overflowX = previousOverflowX;
    };
  }, [sheetOpen]);

  const closeSheet = () => {
    setSheetSnap("closed");
    pickerReturnSnap.current = null;
    setMobileModelPickerOpen(false);
    setMobileVariablePickerOpen(false);
    setMobileRegionPickerOpen(false);
    onMobileControlsOpenChange?.(false);
  };

  const restorePickerReturnSnap = () => {
    const returnSnap = pickerReturnSnap.current;
    pickerReturnSnap.current = null;
    if (returnSnap) {
      setSheetSnap(returnSnap);
    }
  };

  const rememberPickerReturnSnap = () => {
    if (!mobilePickerOpen && pickerReturnSnap.current == null) {
      pickerReturnSnap.current = sheetSnap === "closed" ? "peek" : sheetSnap;
    }
  };

  // Drag-to-snap gesture handlers (phone only)
  const handleDragStart = (e: React.TouchEvent) => {
    dragStartY.current = e.touches[0]?.clientY ?? null;
  };
  const handleDragEnd = (e: React.TouchEvent) => {
    if (dragStartY.current == null) return;
    const deltaY = (e.changedTouches[0]?.clientY ?? 0) - dragStartY.current;
    dragStartY.current = null;
    if (sheetSnap === "peek") {
      if (deltaY < -40) setSheetSnap("full");
      else if (deltaY > 40) closeSheet();
    } else if (sheetSnap === "full") {
      if (deltaY > 60) setSheetSnap("peek");
    }
  };
  const handleHandleClick = () => {
    if (sheetSnap === "peek") setSheetSnap("full");
    else if (sheetSnap === "full") setSheetSnap("peek");
  };

  const selectionContent = (
    <>
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div data-tour-target="mobile-product-variable-run" className={cn("flex flex-col gap-3", mobileRegionPickerOpen ? "hidden" : "")}>
          <div className={cn("space-y-1.5", mobileModelPickerOpen ? "min-h-0 flex-1" : "") }>
            <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
              <Boxes className="h-3 w-3" /> Product
            </span>
            <ModelPicker
              value={model}
              onChange={(nextModel) => { onModelChange(nextModel); closeSheet(); }}
              options={models}
              disabled={disabled}
              placeholder="Product"
              minWidth="w-full"
              inlinePanel={isPhoneLayout}
              inlinePanelClassName="max-h-[calc(90dvh-12rem)]"
              onOpenChange={(nextOpen) => {
                if (!isPhoneLayout) {
                  setMobileModelPickerOpen(false);
                  return;
                }
                setMobileModelPickerOpen(nextOpen);
                if (nextOpen) {
                  rememberPickerReturnSnap();
                  setMobileVariablePickerOpen(false);
                  setMobileRegionPickerOpen(false);
                  setSheetSnap("full");
                } else if (!mobileVariablePickerOpen && !mobileRegionPickerOpen) {
                  restorePickerReturnSnap();
                }
              }}
            />
          </div>

          <div className={cn("space-y-1.5", mobileModelPickerOpen ? "hidden" : mobileVariablePickerOpen ? "min-h-0 flex-1" : "") }>
            <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
              <Layers className="h-3 w-3" /> Variable
            </span>
            <VariablePicker
              modelId={model}
              value={variable}
              onChange={(v) => { onVariableChange(v); closeSheet(); }}
              variableCatalog={displayVariables}
              supportedVariableIds={supportedVariableIds}
              disabled={disabled}
              placeholder="Variable"
              selectedLabelOverride={selectedVariableLabel}
              legend={legend}
              minWidth="w-full"
              inlinePanel={isPhoneLayout}
              inlinePanelClassName="max-h-[calc(90dvh-17rem)]"
              onOpenChange={(open) => {
                if (!isPhoneLayout) {
                  setMobileVariablePickerOpen(false);
                  return;
                }
                setMobileVariablePickerOpen(open);
                if (open) {
                  rememberPickerReturnSnap();
                  setMobileModelPickerOpen(false);
                  setMobileRegionPickerOpen(false);
                  setSheetSnap("full");
                } else if (!mobileModelPickerOpen && !mobileRegionPickerOpen) {
                  restorePickerReturnSnap();
                }
              }}
            />
          </div>

          {showProductSelect ? (
            <div className={cn("space-y-1.5", mobilePickerOpen ? "hidden" : "") }>
              <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
                <Percent className="h-3 w-3" /> Statistic
              </span>
              <StatisticPicker
                value={product ?? "mean"}
                onValueChange={(value) => { onProductChange?.(value); closeSheet(); }}
                options={availableProductOptions}
                disabled={disabled}
                minWidth="w-full"
              />
            </div>
          ) : null}
          <div className={cn("space-y-1.5", mobilePickerOpen ? "hidden" : "") }>
            <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
              <CalendarClock className="h-3 w-3" /> Run Time
            </span>
            <NavbarSelect
              value={run}
              onValueChange={(v) => { onRunChange(v); closeSheet(); }}
              options={runMenuOptions}
              disabled={disabled || runSelectionLocked}
              placeholder="Run Time"
              selectedLabelOverride={runDisplayLabel}
              highlightState={!runSelectionLocked && hasNewerRunAvailable}
              menuActionLabel={!runSelectionLocked && hasNewerRunAvailable ? "View latest run" : null}
              menuActionDescription={
                !runSelectionLocked && hasNewerRunAvailable && latestAvailableRunLabel
                  ? `${latestAvailableRunLabel} available`
                  : null
              }
              onMenuAction={
                !runSelectionLocked && hasNewerRunAvailable
                  ? () => { onViewLatestRun?.(); closeSheet(); }
                  : undefined
              }
              minWidth="w-full"
            />
          </div>
        </div>

        <div
          data-tour-target="mobile-region-row"
          className={cn(
            "space-y-1.5",
            mobileRegionPickerOpen ? "flex min-h-0 flex-1 flex-col" : "",
            mobilePickerOpen && !mobileRegionPickerOpen ? "hidden" : ""
          )}
        >
          <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <MapPinSearch className="h-3 w-3" /> Region
          </span>
          <RegionUtilitySelect
            value={region}
            onValueChange={(v) => { onRegionChange(v); closeSheet(); }}
            onLocationJump={onLocationJump}
            options={regions}
            disabled={disabled}
            currentRegionLabel={selectedRegionLabel}
            variant="field"
            inlinePanel={isPhoneLayout}
            inlinePanelClassName="max-h-[calc(90dvh-12rem)]"
            onOpenChange={(nextOpen) => {
              if (!isPhoneLayout) {
                setMobileRegionPickerOpen(false);
                return;
              }
              setMobileRegionPickerOpen(nextOpen);
              if (nextOpen) {
                rememberPickerReturnSnap();
                setMobileModelPickerOpen(false);
                setMobileVariablePickerOpen(false);
                setSheetSnap("full");
              } else if (!mobileModelPickerOpen && !mobileVariablePickerOpen) {
                restorePickerReturnSnap();
              }
            }}
            onLocationSelected={closeSheet}
          />
        </div>
      </div>
    </>
  );

  const displayContent = (
    <>
      <div className="grid grid-cols-1 gap-2">
        <DisplayRow
          label="City Labels"
          icon={MapPin}
          checked={pointLabelsEnabled}
          onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
        />
        {supportsNwsWarningsOverlay(model, variable) ? (
          <DisplayRow
            label="NWS Warnings"
            icon={TriangleAlert}
            checked={nwsWarningsEnabled}
            onToggle={() => onNwsWarningsEnabledChange(!nwsWarningsEnabled)}
          />
        ) : null}
        <DisplayRow
          label="Legend"
          icon={Palette}
          checked={legendVisible}
          onToggle={() => onLegendVisibleChange(!legendVisible)}
        />
        <DisplayRow
          label="Zoom Controls"
          icon={ZoomIn}
          checked={zoomControlsVisible}
          onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
        />
        <button
          type="button"
          onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-left transition-colors hover:bg-white/[0.07]"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            {basemapMode === "dark" ? <Moon className="h-4 w-4 text-white/72" /> : <Sun className="h-4 w-4 text-white/72" />}
            Basemap
          </div>
          <span className="text-xs font-semibold text-[#98c9b2]">
            {basemapMode === "dark" ? "Dark" : "Light"}
          </span>
        </button>
      </div>

      <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04] px-3.5 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-white">Opacity</span>
          <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
        </div>
        <Slider
          value={[Math.round(opacity * 100)]}
          onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
          min={0}
          max={100}
          step={1}
          className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
        />
      </div>

      {toolbar.onReplayTour ? (
        <button
          type="button"
          onClick={() => {
            toolbar.onReplayTour?.();
            closeSheet();
          }}
          className="mt-2 flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-left transition-colors hover:bg-white/[0.07]"
        >
          <span className="text-sm font-semibold text-white">Replay Tour</span>
          <span className="text-xs font-medium text-cyan-300/70">?</span>
        </button>
      ) : null}
    </>
  );

  return (
    <>
      {/* Spacer so logo stays left-aligned with nothing on the right */}
      <div className="flex-1" />

      {/* Slide-up sheet */}
      {sheetOpen ? createPortal(
        <>
          {/* Backdrop — subtler in peek, full blur when expanded */}
          <div
            className={cn(
              "fixed inset-0 z-[65] transition-[background-color,backdrop-filter] duration-300",
              sheetSnap === "full"
                ? "bg-black/42 backdrop-blur-[6px]"
                : "bg-black/20"
            )}
            onClick={closeSheet}
            aria-hidden="true"
          />

          {/* Sheet panel */}
          <div
            data-tour-target="mobile-bottom-sheet"
            style={isPhoneLayout ? {
              maxHeight: sheetSnap === "full" ? "90dvh" : "60dvh",
              transition: "max-height 0.35s cubic-bezier(0.32, 0.72, 0, 1)",
            } : undefined}
            className={cn(
              "viewer-mobile-surface fixed z-[66] flex max-w-full flex-col overflow-x-hidden overflow-y-hidden",
              isTabletTouchLayout
                ? "right-3 top-[4.5rem] max-h-[calc(100svh-5.5rem)] w-[min(19rem,56vw)] rounded-[1.4rem]"
                : "bottom-0 left-0 right-0 rounded-t-[1.5rem] [border-left:none] [border-right:none] [border-bottom:none] pb-[env(safe-area-inset-bottom)]"
            )}
          >
            {/* Drag handle — phone only, tap to toggle peek/full */}
            {isPhoneLayout ? (
              <div
                className="flex min-h-11 touch-none select-none items-center justify-center active:opacity-70"
                onTouchStart={handleDragStart}
                onTouchEnd={handleDragEnd}
                onClick={handleHandleClick}
                aria-label={sheetSnap === "peek" ? "Expand controls" : "Collapse controls"}
                role="button"
              >
                <div className="h-1 w-10 rounded-full bg-white/25" />
              </div>
            ) : null}

            {/* Header: underline tabs + close button */}
            <div className={cn(
              "flex shrink-0 items-center justify-between border-b border-white/[0.08]",
              isTabletTouchLayout ? "px-5 pt-4" : "px-4 pt-2"
            )}>
              <div className="flex">
                <button
                  type="button"
                  onClick={() => setActiveTab("selection")}
                  className={cn(
                    "relative flex min-h-11 items-center pr-5 text-sm font-semibold transition-colors duration-150",
                    activeTab === "selection" ? "text-white" : "text-white/40 hover:text-white/65"
                  )}
                >
                  Selection
                  {activeTab === "selection" && (
                    <span className="absolute bottom-0 left-0 right-5 h-[2px] rounded-full bg-cyan-400" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("display")}
                  data-tour-target="mobile-display-tab"
                  className={cn(
                    "relative flex min-h-11 items-center pr-5 text-sm font-semibold transition-colors duration-150",
                    activeTab === "display" ? "text-white" : "text-white/40 hover:text-white/65"
                  )}
                >
                  Display
                  {activeTab === "display" && (
                    <span className="absolute bottom-0 left-0 right-5 h-[2px] rounded-full bg-cyan-400" />
                  )}
                </button>
              </div>

              <button
                type="button"
                onClick={closeSheet}
                className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/60 hover:text-white"
                aria-label="Close controls"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Scrollable content — explicit max-height keeps container content-sized (no dead space) */}
            <div
              style={isPhoneLayout ? {
                maxHeight: sheetSnap === "full" ? "calc(90dvh - 5.5rem)" : "calc(60dvh - 5.5rem)",
              } : undefined}
              className={cn(
                "min-h-0",
                mobilePickerOpen ? "flex flex-1 flex-col overflow-hidden" : "overflow-y-auto",
                isTabletTouchLayout ? "max-h-[calc(100svh-10rem)] px-5 pb-5 pt-3" : "px-4 pb-6 pt-3"
              )}
            >
              {activeTab === "selection" ? selectionContent : displayContent}
            </div>
          </div>
        </>
      , document.body) : null}

      {/* Legend popover — opens to the right of the zoom control cluster */}
      {legendVisible && legendPopoverOpen && legend ? createPortal(
        <>
          <div
            className="fixed inset-0 z-[54]"
            onClick={() => onLegendPopoverOpenChange(false)}
            aria-hidden="true"
          />
          <div className="fixed left-[58px] top-[calc(3.5rem+1rem)] z-[55] w-[220px] max-h-[calc(100svh-6rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md">
            <MapLegend
              legend={legend}
              defaultExpanded={true}
              inline={true}
            />
          </div>
        </>
      , document.body) : null}
    </>
  );
}

export default function ViewerSiteHeader() {
  const toolbar = useViewerToolbar();
  const { openFeedback } = useFeedbackContext();
  const isViewerDesktop = (
    toolbar?.layoutMode === "desktop"
    || toolbar?.layoutMode === "tablet-touch"
    || toolbar?.layoutMode === undefined
  );
  const isViewerMobile = !isViewerDesktop;

  return (
    <header className="fixed inset-x-0 top-0 z-[80]">
      <div
        aria-hidden="true"
        className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
        style={{ willChange: "transform" }}
      />
      <div
        className={cn(
          "relative z-10",
          isViewerDesktop
            ? "flex min-h-[4.5rem] items-end gap-3 px-4 pb-2 md:px-5"
            : "flex h-14 items-center gap-3 px-4 md:px-5",
        )}
      >
        <NavLink
          to="/"
          className={cn(
            "flex shrink-0 items-center font-semibold tracking-tight text-white",
            isViewerDesktop ? "self-center translate-y-1" : "",
          )}
        >
          <img
            src={BRAND_LOGO_SRC}
            alt="CartoSky"
            className="block h-12 w-auto max-w-none"
          />
        </NavLink>

        {isViewerDesktop ? <ViewerNavDesktop onFeedback={openFeedback} /> : null}
        {isViewerMobile ? <ViewerNavMobile onFeedback={openFeedback} /> : null}
      </div>
    </header>
  );
}
