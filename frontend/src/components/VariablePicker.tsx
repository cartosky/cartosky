import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Search, Star, X } from "lucide-react";

import type { LegendPayload } from "@/components/map-legend";
import { useVariableFavorites } from "@/lib/use-variable-favorites";
import { cn } from "@/lib/utils";

type VariableOption = {
  value: string;
  label: string;
  group: string | null;
  hasStats?: boolean;
};

type VariablePickerProps = {
  modelId: string;
  value: string;
  onChange: (value: string) => void;
  variableCatalog: VariableOption[];
  supportedVariableIds: string[];
  disabled?: boolean;
  placeholder?: string;
  selectedLabelOverride?: string;
  legend?: LegendPayload | null;
  minWidth?: string;
  onOpenChange?: (open: boolean) => void;
  inlinePanel?: boolean;
  inlinePanelClassName?: string;
  panelOffset?: number;
};

type CategoryId = "FAVORITES" | "SURFACE" | "PRECIPITATION" | "SEVERE" | "UPPER AIR" | "OUTLOOKS" | "FORECASTS" | "ENSEMBLE" | "RADAR" | "SATELLITE";

const BASE_CATEGORY_ROWS: Array<{ id: Exclude<CategoryId, "FAVORITES">; label: string }> = [
  { id: "SURFACE", label: "Surface" },
  { id: "PRECIPITATION", label: "Precip" },
  { id: "SEVERE", label: "Severe" },
  { id: "UPPER AIR", label: "Upper air" },
  { id: "OUTLOOKS", label: "Outlooks" },
  { id: "FORECASTS", label: "Forecasts" },
];

const RADAR_CATEGORY_ROW: { id: Exclude<CategoryId, "FAVORITES">; label: string } = { id: "RADAR", label: "Radar" };
const SATELLITE_CATEGORY_ROW: { id: Exclude<CategoryId, "FAVORITES">; label: string } = { id: "SATELLITE", label: "Satellite" };

const CATEGORY_ROWS: Array<{ id: Exclude<CategoryId, "FAVORITES">; label: string }> = [
  ...BASE_CATEGORY_ROWS,
  RADAR_CATEGORY_ROW,
  SATELLITE_CATEGORY_ROW,
];

const CATEGORY_LABELS = new Map<CategoryId, string>([
  ["FAVORITES", "Favorites"],
  ...CATEGORY_ROWS.map((row) => [row.id, row.label] as [CategoryId, string]),
]);

const ANOMALY_VARIABLE_ID_PATTERN = /_anom(?:__|$)/;

function isAnomalyOption(option: VariableOption): boolean {
  return ANOMALY_VARIABLE_ID_PATTERN.test(option.value) || option.label.toLowerCase().includes("anomaly");
}

function normalizeGroup(group: string | null): CategoryId | null {
  const normalized = String(group ?? "").trim().toUpperCase();
  if (normalized === "SURFACE") return "SURFACE";
  if (normalized === "PRECIPITATION" || normalized === "PRECIP ANOMALIES") return "PRECIPITATION";
  if (normalized === "SEVERE") return "SEVERE";
  if (normalized === "UPPER AIR") return "UPPER AIR";
  if (normalized === "OUTLOOKS") return "OUTLOOKS";
  if (normalized === "FORECASTS" || normalized === "FORECAST") return "FORECASTS";
  if (normalized === "ENSEMBLE" || normalized === "ENSEMBLES") return "ENSEMBLE";
  if (normalized === "RADAR") return "RADAR";
  if (normalized === "SATELLITE") return "SATELLITE";
  return null;
}

function focusWeatherMap(): void {
  if (typeof document === "undefined") {
    return;
  }
  const map = document.querySelector<HTMLElement>('[aria-label="Weather map"]');
  if (!map) {
    return;
  }
  if (!map.hasAttribute("tabindex")) {
    map.setAttribute("tabindex", "-1");
  }
  map.focus({ preventScroll: true });
}

type CpcPair = {
  periodKey: string;
  periodLabel: string;
  temp: VariableOption | null;
  precip: VariableOption | null;
};

const CPC_PERIOD_ORDER = ["610", "814", "w34", "1m", "3m"] as const;

const CPC_PERIOD_LABELS: Record<string, string> = {
  "610": "6-10 Day",
  "814": "8-14 Day",
  "w34": "Week 3-4",
  "1m":  "One Month",
  "3m":  "Three Month",
};

function buildCpcPairs(options: VariableOption[]): CpcPair[] {
  const byPeriod = new Map<string, { temp: VariableOption | null; precip: VariableOption | null }>();
  for (const period of CPC_PERIOD_ORDER) {
    byPeriod.set(period, { temp: null, precip: null });
  }
  for (const option of options) {
    for (const period of CPC_PERIOD_ORDER) {
      if (option.value === `cpc_${period}_temp`) {
        byPeriod.get(period)!.temp = option;
      } else if (option.value === `cpc_${period}_precip`) {
        byPeriod.get(period)!.precip = option;
      }
    }
  }
  return CPC_PERIOD_ORDER
    .map((period) => ({
      periodKey: period,
      periodLabel: CPC_PERIOD_LABELS[period]!,
      ...byPeriod.get(period)!,
    }))
    .filter((pair) => pair.temp !== null || pair.precip !== null);
}

export function VariablePicker({
  modelId,
  value,
  onChange,
  variableCatalog,
  supportedVariableIds,
  disabled = false,
  placeholder = "Variable",
  selectedLabelOverride,
  minWidth = "min-w-[180px] max-w-[320px]",
  onOpenChange,
  inlinePanel = false,
  inlinePanelClassName,
  panelOffset = 6,
}: VariablePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<CategoryId>("SURFACE");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [cpcActivePeriod, setCpcActivePeriod] = useState<string>("610");
  const [panelPosition, setPanelPosition] = useState<{ left: number; top: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const wasOpenRef = useRef(false);
  const { favorites, favoriteSet, toggleFavorite } = useVariableFavorites(modelId);

  const supportedSet = useMemo(() => new Set(supportedVariableIds), [supportedVariableIds]);
  const options = useMemo(() => {
    const seen = new Set<string>();
    return variableCatalog.filter((option) => {
      if (!option.value || seen.has(option.value) || !supportedSet.has(option.value)) {
        return false;
      }
      seen.add(option.value);
      return true;
    });
  }, [supportedSet, variableCatalog]);
  const availableCategoryRows = useMemo(() => {
    const categoryIds = new Set<CategoryId>();
    for (const option of options) {
      const category = normalizeGroup(option.group);
      if (category && category !== "FAVORITES") {
        categoryIds.add(category);
      }
    }
    return CATEGORY_ROWS.filter((row) => categoryIds.has(row.id));
  }, [options]);
  const optionById = useMemo(() => new Map(options.map((option) => [option.value, option])), [options]);
  const selectedLabel = selectedLabelOverride ?? optionById.get(value)?.label ?? (value || placeholder);
  const normalizedQuery = query.trim().toLowerCase();
  const hasSearch = normalizedQuery.length > 0;

  const matchesQuery = useCallback((option: VariableOption) => {
    if (!hasSearch) {
      return true;
    }
    return option.label.toLowerCase().includes(normalizedQuery);
  }, [hasSearch, normalizedQuery]);

  const categorizedOptions = useMemo(() => {
    const byCategory = new Map<CategoryId, VariableOption[]>();
    for (const row of availableCategoryRows) {
      byCategory.set(row.id, []);
    }
    for (const option of options) {
      const category = normalizeGroup(option.group);
      if (!category || category === "FAVORITES") {
        continue;
      }
      byCategory.get(category)?.push(option);
    }
    return byCategory;
  }, [availableCategoryRows, options]);

  const cpcPairs = useMemo(
    () => modelId === "cpc" ? buildCpcPairs(categorizedOptions.get("FORECASTS") ?? []) : [],
    [modelId, categorizedOptions]
  );
  const cpcActivePair = cpcPairs.find((pair) => pair.periodKey === cpcActivePeriod) ?? cpcPairs[0] ?? null;
  const cpcVisibleOptions = cpcActivePair
    ? [cpcActivePair.temp, cpcActivePair.precip].filter((o): o is VariableOption => o !== null)
    : [];

  const favoriteOptions = useMemo(
    () => favorites.map((favoriteId) => optionById.get(favoriteId)).filter((option): option is VariableOption => Boolean(option)),
    [favorites, optionById]
  );

  const categoryRows = useMemo(() => {
    const rows: Array<{ id: CategoryId; label: string; count: number }> = [];
    if (favoriteOptions.length > 0) {
      rows.push({
        id: "FAVORITES",
        label: "Favorites",
        count: favoriteOptions.filter(matchesQuery).length,
      });
    }
    for (const row of availableCategoryRows) {
      rows.push({
        id: row.id,
        label: row.label,
        count: (categorizedOptions.get(row.id) ?? []).filter(matchesQuery).length,
      });
    }
    return rows;
  }, [availableCategoryRows, categorizedOptions, favoriteOptions, matchesQuery]);
  const firstAvailableCategory = categoryRows[0]?.id ?? "SURFACE";

  const visibleOptions = useMemo(() => {
    if (hasSearch) {
      return options.filter(matchesQuery);
    }
    if (activeCategory === "FAVORITES") {
      return favoriteOptions;
    }
    const categoryOptions = categorizedOptions.get(activeCategory) ?? [];
    const regularOptions = categoryOptions.filter((option) => !isAnomalyOption(option));
    const anomalyOptions = categoryOptions.filter((option) => isAnomalyOption(option));
    return [...regularOptions, ...anomalyOptions];
  }, [activeCategory, categorizedOptions, favoriteOptions, hasSearch, matchesQuery, options]);

  const selectedCategory = useMemo(() => {
    const selected = optionById.get(value);
    return normalizeGroup(selected?.group ?? null);
  }, [optionById, value]);

  const setOpenState = useCallback((nextOpen: boolean) => {
    setOpen(nextOpen);
    onOpenChange?.(nextOpen);
  }, [onOpenChange]);

  const updatePanelPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || typeof window === "undefined") {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const panelWidth = Math.min(380, window.innerWidth - 16);
    const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - panelWidth - 8));
    setPanelPosition({ left, top: rect.bottom + panelOffset });
  }, [panelOffset]);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }
    updatePanelPosition();
    window.addEventListener("resize", updatePanelPosition);
    window.addEventListener("scroll", updatePanelPosition, true);
    return () => {
      window.removeEventListener("resize", updatePanelPosition);
      window.removeEventListener("scroll", updatePanelPosition, true);
    };
  }, [open, updatePanelPosition]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const selectedIndex = visibleOptions.findIndex((option) => option.value === value);
    setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
  }, [open, value, visibleOptions]);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      const nextCategory = selectedCategory && categoryRows.some((category) => category.id === selectedCategory)
        ? selectedCategory
        : firstAvailableCategory;
      setActiveCategory((current) => (current === "FAVORITES" && favoriteOptions.length > 0 ? current : nextCategory));
    }
    wasOpenRef.current = open;
  }, [categoryRows, favoriteOptions.length, firstAvailableCategory, open, selectedCategory]);

  useEffect(() => {
    if (open) {
      return;
    }
    if (activeCategory === "FAVORITES" && favoriteOptions.length > 0) {
      return;
    }
    if (categoryRows.some((category) => category.id === activeCategory)) {
      return;
    }
    const nextCategory = selectedCategory && categoryRows.some((category) => category.id === selectedCategory)
      ? selectedCategory
      : firstAvailableCategory;
    setActiveCategory(nextCategory);
  }, [activeCategory, categoryRows, favoriteOptions.length, firstAvailableCategory, open, selectedCategory]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: MouseEvent | TouchEvent) {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      setOpenState(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
    };
  }, [open, setOpenState]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpenState(false);
        focusWeatherMap();
        return;
      }

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedIndex((current) => {
          if (visibleOptions.length === 0) {
            return 0;
          }
          const delta = event.key === "ArrowDown" ? 1 : -1;
          return (current + delta + visibleOptions.length) % visibleOptions.length;
        });
        return;
      }

      if (event.key === "Enter") {
        const option = visibleOptions[highlightedIndex];
        if (option && supportedSet.has(option.value)) {
          event.preventDefault();
          onChange(option.value);
          setOpenState(false);
        }
        return;
      }

      const target = event.target;
      const isEditable = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
      if (!isEditable && event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        setQuery((current) => `${current}${event.key}`);
        searchInputRef.current?.focus({ preventScroll: true });
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [highlightedIndex, onChange, open, setOpenState, supportedSet, visibleOptions]);

  useEffect(() => {
    const item = listRef.current?.querySelector<HTMLElement>(`[data-variable-index="${highlightedIndex}"]`);
    item?.scrollIntoView({ block: "nearest" });
  }, [highlightedIndex]);

  const chooseVariable = (variableId: string) => {
    if (!supportedSet.has(variableId)) {
      return;
    }
    onChange(variableId);
    setOpenState(false);
  };

  const panelContent = open && (inlinePanel || panelPosition) ? (
    <div
      ref={panelRef}
      className={cn(
        "z-[90] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] text-white shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
        inlinePanel
          ? "mt-2 flex min-h-0 w-full flex-1 flex-col"
          : "fixed w-[min(380px,calc(100vw-16px))]",
        inlinePanel ? inlinePanelClassName : null
      )}
      style={inlinePanel ? undefined : { left: panelPosition?.left ?? 8, top: panelPosition?.top ?? 0 }}
      role="dialog"
      aria-label="Variable picker"
    >
      <div className="flex items-center gap-2 border-b border-[#1a3a5c]/60 px-3 py-2.5">
        <Search className="h-3.5 w-3.5 shrink-0 text-cyan-200/58" />
        <input
          ref={searchInputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search variables…"
          className="min-w-0 flex-1 bg-transparent text-[12px] font-medium text-white outline-none placeholder:text-white/34"
        />
        {query ? (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              searchInputRef.current?.focus({ preventScroll: true });
            }}
            className="inline-flex h-6 w-6 items-center justify-center rounded-md text-white/42 transition-colors hover:bg-white/[0.07] hover:text-white/78"
            aria-label="Clear variable search"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>

      <div className={cn("grid grid-cols-[118px_minmax(0,1fr)]", inlinePanel ? "min-h-0 flex-1" : "h-[292px]")}> 
        {modelId === "cpc" && !hasSearch ? (
          <>
            <div className="min-h-0 overflow-hidden border-r border-[#1a3a5c]/55 bg-[#071422]/75 p-1.5">
              {cpcPairs.map((pair) => {
                const active = pair.periodKey === cpcActivePeriod;
                return (
                  <button
                    key={pair.periodKey}
                    type="button"
                    onClick={() => setCpcActivePeriod(pair.periodKey)}
                    className={cn(
                      "flex h-8 w-full items-center gap-2 rounded-lg border-l-2 px-2 text-left text-[11px] font-semibold transition-colors",
                      active
                        ? "border-l-[#185FA5] bg-cyan-300/[0.10] text-cyan-50"
                        : "border-l-transparent text-white/62 hover:bg-white/[0.055] hover:text-white/86"
                    )}
                  >
                    <span className="min-w-0 truncate">{pair.periodLabel}</span>
                  </button>
                );
              })}
            </div>

            <div ref={listRef} className="picker-scroll min-h-0 overflow-y-scroll p-1.5">
              {cpcVisibleOptions.length === 0 ? (
                <div className="flex h-full items-center justify-center px-4 text-center text-[12px] font-medium text-white/42">
                  No variables found
                </div>
              ) : (
                cpcVisibleOptions.map((option, index) => {
                  const supported = supportedSet.has(option.value);
                  const selected = option.value === value;
                  const highlighted = index === highlightedIndex;
                  const favorited = favoriteSet.has(option.value);
                  return (
                    <div
                      key={option.value}
                      data-variable-index={index}
                      className={cn(
                        "group flex h-8 items-center gap-1.5 rounded-lg px-1.5 transition-colors",
                        selected
                          ? "bg-[#185FA5]/20 text-cyan-100"
                          : highlighted
                            ? "bg-white/[0.07] text-white"
                            : supported
                              ? "text-white/82 hover:bg-white/[0.055] hover:text-white"
                              : "text-white/32"
                      )}
                    >
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleFavorite(option.value);
                        }}
                        className={cn(
                          "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-all hover:bg-white/[0.08]",
                          favorited ? "text-amber-300 opacity-100" : "text-white/34 opacity-50 hover:text-white/55"
                        )}
                        aria-label={favorited ? `Remove ${option.label} from favorites` : `Favorite ${option.label}`}
                      >
                        <Star className={cn("h-3.5 w-3.5", favorited ? "fill-current" : "")} />
                      </button>
                      <button
                        type="button"
                        disabled={!supported}
                        onClick={() => chooseVariable(option.value)}
                        onMouseEnter={() => setHighlightedIndex(index)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                        title={supported ? option.label : `${option.label} is not available for this model`}
                      >
                        <span className={cn("min-w-0 flex-1 truncate text-[12px] font-medium", selected ? "text-cyan-100" : "")}>{option.label}</span>
                        {option.hasStats ? (
                          <span className="shrink-0 rounded-md border border-cyan-300/25 bg-cyan-300/[0.10] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-cyan-200/80">
                            stats
                          </span>
                        ) : null}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </>
        ) : (
          <>
            <div className="min-h-0 overflow-hidden border-r border-[#1a3a5c]/55 bg-[#071422]/75 p-1.5">
              {categoryRows.map((category) => {
                const active = !hasSearch && category.id === activeCategory;
                return (
                  <button
                    key={category.id}
                    type="button"
                    onClick={() => setActiveCategory(category.id)}
                    className={cn(
                      "flex h-8 w-full items-center justify-between gap-2 rounded-lg border-l-2 px-2 text-left text-[11px] font-semibold transition-colors",
                      active
                        ? "border-l-[#185FA5] bg-cyan-300/[0.10] text-cyan-50"
                        : "border-l-transparent text-white/62 hover:bg-white/[0.055] hover:text-white/86"
                    )}
                  >
                    <span className="min-w-0 truncate">{category.label}</span>
                    <span className="rounded-md border border-white/8 bg-white/[0.055] px-1.5 py-0.5 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium text-white/44">
                      {category.count}
                    </span>
                  </button>
                );
              })}
            </div>

            <div ref={listRef} className="picker-scroll min-h-0 overflow-y-scroll p-1.5">
              {visibleOptions.length === 0 ? (
                <div className="flex h-full items-center justify-center px-4 text-center text-[12px] font-medium text-white/42">
                  No variables found
                </div>
              ) : (
                visibleOptions.map((option, index) => {
                  const supported = supportedSet.has(option.value);
                  const selected = option.value === value;
                  const highlighted = index === highlightedIndex;
                  const favorited = favoriteSet.has(option.value);
                  const categoryLabel = CATEGORY_LABELS.get(normalizeGroup(option.group) ?? "SURFACE") ?? "Other";
                  const anomalyOption = isAnomalyOption(option);
                  const showAnomalyHeading = !hasSearch
                    && activeCategory !== "FAVORITES"
                    && anomalyOption
                    && (index === 0 || !isAnomalyOption(visibleOptions[index - 1]!));
                  return (
                    <Fragment key={option.value}>
                      {showAnomalyHeading ? (
                        <div className="px-1.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-100/62">
                          Anomalies
                        </div>
                      ) : null}
                      <div
                        data-variable-index={index}
                        className={cn(
                          "group flex h-8 items-center gap-1.5 rounded-lg px-1.5 transition-colors",
                          selected
                            ? "bg-[#185FA5]/20 text-cyan-100"
                            : highlighted
                              ? "bg-white/[0.07] text-white"
                              : supported
                                ? "text-white/82 hover:bg-white/[0.055] hover:text-white"
                                : "text-white/32"
                        )}
                      >
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            toggleFavorite(option.value);
                          }}
                          className={cn(
                            "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-all hover:bg-white/[0.08]",
                            favorited ? "text-amber-300 opacity-100" : "text-white/34 opacity-50 hover:text-white/55"
                          )}
                          aria-label={favorited ? `Remove ${option.label} from favorites` : `Favorite ${option.label}`}
                        >
                          <Star className={cn("h-3.5 w-3.5", favorited ? "fill-current" : "")} />
                        </button>
                        <button
                          type="button"
                          disabled={!supported}
                          onClick={() => chooseVariable(option.value)}
                          onMouseEnter={() => setHighlightedIndex(index)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                          title={supported ? option.label : `${option.label} is not available for this model`}
                        >
                          <span className={cn("min-w-0 flex-1 truncate text-[12px] font-medium", selected ? "text-cyan-100" : "")}>{option.label}</span>
                          {option.hasStats ? (
                            <span className="shrink-0 rounded-md border border-cyan-300/25 bg-cyan-300/[0.10] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-cyan-200/80">
                              stats
                            </span>
                          ) : null}
                          {hasSearch ? (
                            <span className="shrink-0 rounded-md border border-white/8 bg-white/[0.05] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-white/38">
                              {categoryLabel}
                            </span>
                          ) : null}
                        </button>
                      </div>
                    </Fragment>
                  );
                })
              )}
            </div>
          </>
        )}
      </div>

    </div>
  ) : null;

  const panel = panelContent && inlinePanel ? panelContent : panelContent ? createPortal(panelContent, document.body) : null;

  return (
    <div className={cn(inlinePanel ? "flex min-h-0 flex-col" : "contents")}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled || options.length === 0}
        onClick={() => setOpenState(!open)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "inline-flex h-8 w-auto items-center justify-between gap-2 rounded-xl border border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50",
          minWidth,
          open ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100" : ""
        )}
      >
        <span className="min-w-0 truncate whitespace-nowrap">{selectedLabel}</span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 opacity-50 transition-transform", open ? "rotate-180" : "")} />
      </button>
      {panel}
    </div>
  );
}
