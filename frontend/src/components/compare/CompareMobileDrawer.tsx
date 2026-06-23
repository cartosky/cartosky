import { useEffect, useRef, useState, type ReactNode, type TouchEvent as ReactTouchEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowLeftRight, Layers, Moon, Sun } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { BasemapMode } from "@/components/map-canvas";
import type { GroupedOption, VariableOption } from "@/lib/app-utils";
import { cn } from "@/lib/utils";

type RunOption = { value: string; label: string };
type DrawerTab = "comparison" | "display";

export type CompareMobileDrawerProps = {
  open: boolean;
  onClose: () => void;
  activeTab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
  // Picker state (same state/setters as the desktop control bar — no second store).
  lModel: string;
  rModel: string;
  sharedVariable: string;
  lRun: string;
  rRun: string;
  modelOptions: GroupedOption[];
  variableCatalog: VariableOption[];
  diffMutualVariables: string[];
  leftRunOptions: RunOption[];
  rightRunOptions: RunOption[];
  onLeftModelChange: (value: string) => void;
  onRightModelChange: (value: string) => void;
  onSharedVariableChange: (value: string) => void;
  onLeftRunChange: (value: string) => void;
  onRightRunChange: (value: string) => void;
  onSwap: () => void;
  // Display settings — same state the desktop display panel reads/writes.
  basemapMode: BasemapMode;
  onToggleBasemap: () => void;
  showLegends: boolean;
  onToggleLegends: () => void;
};

/** Labeled field wrapper — label above a 44px control. */
function Field({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <label className={cn("flex min-w-0 flex-1 flex-col gap-1", className)}>
      <span className="px-0.5 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/42">{label}</span>
      {children}
    </label>
  );
}

/** 44px-tall select matching the drawer's touch-target requirement. */
function DrawerSelect({
  value,
  onValueChange,
  options,
  placeholder,
  disabled,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: RunOption[];
  placeholder: string;
  disabled?: boolean;
}) {
  return (
    <Select value={value} onValueChange={onValueChange} disabled={disabled || options.length === 0}>
      <SelectTrigger className="h-11 gap-2 rounded-xl border-white/[0.09] bg-white/[0.05] px-3 text-[13px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:ring-0">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className="max-h-72">
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} className="text-[13px]">
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function DrawerInner({
  onClose,
  activeTab,
  onTabChange,
  lModel,
  rModel,
  sharedVariable,
  lRun,
  rRun,
  modelOptions,
  variableCatalog,
  diffMutualVariables,
  leftRunOptions,
  rightRunOptions,
  onLeftModelChange,
  onRightModelChange,
  onSharedVariableChange,
  onLeftRunChange,
  onRightRunChange,
  onSwap,
  basemapMode,
  onToggleBasemap,
  showLegends,
  onToggleLegends,
}: Omit<CompareMobileDrawerProps, "open">) {
  // Three-state sheet, mirroring the viewer's mobile sheet. "closed" is modeled
  // by unmounting (the `open` prop), so internally we only track peek/full.
  const [snap, setSnap] = useState<"peek" | "full">("peek");
  const dragStartY = useRef<number | null>(null);

  // Lock body scroll while the drawer is mounted.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  const handleDragStart = (e: ReactTouchEvent) => {
    dragStartY.current = e.touches[0]?.clientY ?? null;
  };
  const handleDragEnd = (e: ReactTouchEvent) => {
    if (dragStartY.current == null) return;
    const deltaY = (e.changedTouches[0]?.clientY ?? 0) - dragStartY.current;
    dragStartY.current = null;
    if (snap === "peek") {
      if (deltaY < -40) setSnap("full");
      else if (deltaY > 40) onClose();
    } else if (deltaY > 60) {
      setSnap("peek");
    }
  };
  const handleHandleClick = () => {
    setSnap((current) => (current === "peek" ? "full" : "peek"));
  };

  const modelSelectOptions: RunOption[] = modelOptions.map((option) => ({
    value: option.value,
    label: option.label,
  }));
  const variableSelectOptions: RunOption[] = diffMutualVariables.map((key) => ({
    value: key,
    label: variableCatalog.find((entry) => entry.value === key)?.label ?? key,
  }));
  const variablesDisabled = variableSelectOptions.length === 0;

  return (
    <>
      {/* Backdrop — subtler at peek, darker + blurred at full. */}
      <div
        className={cn(
          "fixed inset-0 z-[65] transition-[background-color,backdrop-filter] duration-300",
          snap === "full" ? "bg-black/42 backdrop-blur-[6px]" : "bg-black/20",
        )}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Sheet */}
      <div
        style={{
          maxHeight: snap === "full" ? "90dvh" : "60dvh",
          transition: "max-height 0.35s cubic-bezier(0.32, 0.72, 0, 1)",
        }}
        className="viewer-mobile-surface fixed bottom-0 left-0 right-0 z-[66] flex max-w-full flex-col overflow-x-hidden overflow-y-hidden rounded-t-[1.5rem] [border-bottom:none] [border-left:none] [border-right:none] pb-[env(safe-area-inset-bottom)]"
        role="dialog"
        aria-label="Comparison settings"
      >
        {/* Drag handle — tap toggles peek/full, drag snaps/closes. */}
        <div
          className="flex touch-none select-none justify-center pt-3 pb-1 active:opacity-70"
          onTouchStart={handleDragStart}
          onTouchEnd={handleDragEnd}
          onClick={handleHandleClick}
          role="button"
          aria-label={snap === "peek" ? "Expand comparison settings" : "Collapse comparison settings"}
        >
          <div className="h-1 w-10 rounded-full bg-white/25" />
        </div>

        <div
          className="min-h-0 overflow-y-auto px-4 pb-6 pt-1"
          style={{ maxHeight: snap === "full" ? "calc(90dvh - 3rem)" : "calc(60dvh - 3rem)" }}
        >
          {/* Tab bar — two equal pills, same treatment as the mode toggle. */}
          <div className="mb-3 grid grid-cols-2 gap-0.5 rounded-xl border border-white/[0.09] bg-white/[0.05] p-0.5">
            {([
              { id: "comparison" as const, label: "Comparison" },
              { id: "display" as const, label: "Display" },
            ]).map((tab) => {
              const active = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange(tab.id)}
                  className={cn(
                    "h-10 w-full rounded-lg text-[12px] font-medium transition-all duration-150",
                    active
                      ? "border border-cyan-300/25 bg-cyan-300/[0.10] text-cyan-100 shadow-[inset_0_1px_0_rgba(103,232,249,0.08)]"
                      : "border border-transparent text-white/55 hover:bg-white/[0.06] hover:text-white",
                  )}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {activeTab === "comparison" ? (
            <>
              {/* Left model · swap · right model */}
              <div className="flex items-end gap-2">
                <Field label="Left Model">
                  <DrawerSelect value={lModel} onValueChange={onLeftModelChange} options={modelSelectOptions} placeholder="Model" />
                </Field>
                <button
                  type="button"
                  onClick={onSwap}
                  aria-label="Swap left and right models"
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/[0.14] bg-[#07111f] text-white/55 transition-all hover:border-white/30 hover:text-white"
                >
                  <ArrowLeftRight className="h-4 w-4" />
                </button>
                <Field label="Right Model">
                  <DrawerSelect value={rModel} onValueChange={onRightModelChange} options={modelSelectOptions} placeholder="Model" />
                </Field>
              </div>

              {/* Shared variable */}
              <Field label="Variable (Shared)" className="mt-3">
                <DrawerSelect
                  value={sharedVariable}
                  onValueChange={onSharedVariableChange}
                  options={variableSelectOptions}
                  placeholder={variablesDisabled ? "No shared variable" : "Variable"}
                  disabled={variablesDisabled}
                />
              </Field>

              <div className="my-3 border-t border-white/[0.08]" />

              {/* Independent runs */}
              <div className="flex items-end gap-2">
                <Field label="L Run">
                  <DrawerSelect value={lRun} onValueChange={onLeftRunChange} options={leftRunOptions} placeholder="Run" />
                </Field>
                <Field label="R Run">
                  <DrawerSelect value={rRun} onValueChange={onRightRunChange} options={rightRunOptions} placeholder="Run" />
                </Field>
              </div>
            </>
          ) : (
            <div className="space-y-1.5">
              <button
                type="button"
                onClick={onToggleBasemap}
                className="flex h-12 w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-left transition-all duration-150 hover:bg-white/[0.07]"
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-white">
                  {basemapMode === "dark" ? <Moon className="h-4 w-4 text-white/60" /> : <Sun className="h-4 w-4 text-white/60" />}
                  Basemap
                </div>
                <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/80">
                  {basemapMode === "dark" ? "Dark" : "Light"}
                </span>
              </button>

              <button
                type="button"
                onClick={onToggleLegends}
                aria-pressed={showLegends}
                className={cn(
                  "flex h-12 w-full items-center justify-between gap-3 rounded-lg border px-3 text-left transition-all duration-150",
                  showLegends
                    ? "border-cyan-300/20 bg-cyan-300/[0.07] hover:bg-cyan-300/[0.11]"
                    : "border-white/10 bg-white/[0.04] hover:bg-white/[0.07]",
                )}
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-white">
                  <Layers className="h-4 w-4 text-white/72" />
                  Legends
                </div>
                <span className={cn("font-['IBM_Plex_Mono',monospace] text-[10px] font-medium", showLegends ? "text-cyan-300/90" : "text-white/38")}>
                  {showLegends ? "On" : "Off"}
                </span>
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * Bottom drawer holding the diff picker controls on mobile. Mounts via portal
 * only while `open`; unmounting models the "closed" snap state. All selects read
 * and write the same compare state passed in as props (no second state machine).
 */
export function CompareMobileDrawer({ open, ...rest }: CompareMobileDrawerProps) {
  if (!open) {
    return null;
  }
  return createPortal(<DrawerInner {...rest} />, document.body);
}

export default CompareMobileDrawer;
