import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Search, Star, X } from "lucide-react";

import type { LegendPayload } from "@/components/map-legend";
import { useVariableFavorites } from "@/lib/use-variable-favorites";
import { cn } from "@/lib/utils";

type VariableOption = {
  value: string;
  label: string;
  group: string | null;
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
};

type CategoryId = "FAVORITES" | "SURFACE" | "PRECIPITATION" | "PRECIP ANOMALIES" | "SEVERE" | "UPPER AIR" | "ENSEMBLE" | "RADAR";

const BASE_CATEGORY_ROWS: Array<{ id: Exclude<CategoryId, "FAVORITES">; label: string }> = [
  { id: "SURFACE", label: "Surface" },
  { id: "PRECIPITATION", label: "Precip" },
  { id: "PRECIP ANOMALIES", label: "Precip anomalies" },
  { id: "SEVERE", label: "Severe" },
  { id: "UPPER AIR", label: "Upper air" },
];

const RADAR_CATEGORY_ROW: { id: Exclude<CategoryId, "FAVORITES">; label: string } = { id: "RADAR", label: "Radar" };

const CATEGORY_ROWS: Array<{ id: Exclude<CategoryId, "FAVORITES">; label: string }> = [
  ...BASE_CATEGORY_ROWS,
  RADAR_CATEGORY_ROW,
];

const CATEGORY_LABELS = new Map<CategoryId, string>([
  ["FAVORITES", "Favorites"],
  ...CATEGORY_ROWS.map((row) => [row.id, row.label] as [CategoryId, string]),
]);

function normalizeGroup(group: string | null): CategoryId | null {
  const normalized = String(group ?? "").trim().toUpperCase();
  if (normalized === "SURFACE") return "SURFACE";
  if (normalized === "PRECIPITATION") return "PRECIPITATION";
  if (normalized === "PRECIP ANOMALIES") return "PRECIP ANOMALIES";
  if (normalized === "SEVERE") return "SEVERE";
  if (normalized === "UPPER AIR") return "UPPER AIR";
  if (normalized === "ENSEMBLE" || normalized === "ENSEMBLES") return "ENSEMBLE";
  if (normalized === "RADAR") return "RADAR";
  return null;
}

function selectedSwatchColor(legend: LegendPayload | null | undefined): string {
  const entries = legend?.entries ?? [];
  if (entries.length === 0) {
    return "#185FA5";
  }
  return entries[Math.floor(entries.length / 2)]?.color || entries[0]?.color || "#185FA5";
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

export function VariablePicker({
  modelId,
  value,
  onChange,
  variableCatalog,
  supportedVariableIds,
  disabled = false,
  placeholder = "Variable",
  selectedLabelOverride,
  legend,
  minWidth = "min-w-[180px] max-w-[320px]",
  onOpenChange,
}: VariablePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<CategoryId>("SURFACE");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [panelPosition, setPanelPosition] = useState<{ left: number; top: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const { favorites, favoriteSet, toggleFavorite } = useVariableFavorites(modelId);
  const isRadarModel = modelId === "mrms";
  const availableCategoryRows = useMemo(
    () => (isRadarModel ? CATEGORY_ROWS : BASE_CATEGORY_ROWS),
    [isRadarModel]
  );

  const supportedSet = useMemo(() => new Set(supportedVariableIds), [supportedVariableIds]);
  const options = useMemo(() => {
    const seen = new Set<string>();
    return variableCatalog.filter((option) => {
      if (!option.value || seen.has(option.value)) {
        return false;
      }
      if (!isRadarModel && normalizeGroup(option.group) === "RADAR") {
        return false;
      }
      seen.add(option.value);
      return true;
    });
  }, [isRadarModel, variableCatalog]);
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
      if (!category || category === "FAVORITES" || (!isRadarModel && category === "RADAR")) {
        continue;
      }
      byCategory.get(category)?.push(option);
    }
    return byCategory;
  }, [availableCategoryRows, isRadarModel, options]);

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

  const visibleOptions = useMemo(() => {
    if (hasSearch) {
      return options.filter(matchesQuery);
    }
    if (activeCategory === "FAVORITES") {
      return favoriteOptions;
    }
    return categorizedOptions.get(activeCategory) ?? [];
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
    setPanelPosition({ left, top: rect.bottom + 6 });
  }, []);

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
    if (!selectedCategory) {
      return;
    }
    setActiveCategory((current) => (current === "FAVORITES" && favoriteOptions.length > 0 ? current : selectedCategory));
  }, [favoriteOptions.length, selectedCategory]);

  useEffect(() => {
    if (activeCategory !== "FAVORITES" || favoriteOptions.length > 0) {
      return;
    }
    setActiveCategory(selectedCategory ?? "SURFACE");
  }, [activeCategory, favoriteOptions.length, selectedCategory]);

  useEffect(() => {
    if (activeCategory !== "RADAR" || isRadarModel) {
      return;
    }
    setActiveCategory(selectedCategory && selectedCategory !== "RADAR" ? selectedCategory : "SURFACE");
  }, [activeCategory, isRadarModel, selectedCategory]);

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

  const panel = open && panelPosition ? createPortal(
    <div
      ref={panelRef}
      className="fixed z-[90] w-[min(380px,calc(100vw-16px))] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] text-white shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
      style={{ left: panelPosition.left, top: panelPosition.top }}
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

      <div className="grid h-[292px] grid-cols-[118px_minmax(0,1fr)]">
        <div className="border-r border-[#1a3a5c]/55 bg-[#071422]/75 p-1.5">
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
                {category.id === "PRECIP ANOMALIES" ? (
                  <span className="min-w-0 leading-[1.05]">
                    <span className="block">Precip</span>
                    <span className="block">Anomalies</span>
                  </span>
                ) : (
                  <span className="min-w-0 truncate">{category.label}</span>
                )}
                <span className="rounded-md border border-white/8 bg-white/[0.055] px-1.5 py-0.5 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium text-white/44">
                  {category.count}
                </span>
              </button>
            );
          })}
        </div>

        <div ref={listRef} className="overflow-y-auto p-1.5">
          {visibleOptions.length === 0 ? (
            <div className="flex h-full items-center justify-center px-4 text-center text-[12px] font-medium text-white/42">
              No variables found
            </div>
          ) : visibleOptions.map((option, index) => {
            const supported = supportedSet.has(option.value);
            const selected = option.value === value;
            const highlighted = index === highlightedIndex;
            const favorited = favoriteSet.has(option.value);
            const categoryLabel = CATEGORY_LABELS.get(normalizeGroup(option.group) ?? "SURFACE") ?? "Other";
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
                    favorited ? "text-amber-300 opacity-100" : "text-white/34 opacity-0 group-hover:opacity-100"
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
                  {hasSearch ? (
                    <span className="shrink-0 rounded-md border border-white/8 bg-white/[0.05] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-white/38">
                      {categoryLabel}
                    </span>
                  ) : null}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-[#1a3a5c]/60 bg-[#071422]/75 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className="h-[10px] w-[10px] shrink-0 rounded-[2px]"
            style={{ backgroundColor: selectedSwatchColor(legend) }}
          />
          <span className="min-w-0 truncate text-[11px] font-semibold text-white/78">{selectedLabel}</span>
        </div>
        <span className="shrink-0 text-[10px] font-medium text-white/34">↑↓ navigate · ★ favorite</span>
      </div>
    </div>,
    document.body
  ) : null;

  return (
    <>
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
    </>
  );
}