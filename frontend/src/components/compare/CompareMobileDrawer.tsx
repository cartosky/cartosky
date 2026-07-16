import { useEffect, useRef, useState, type ReactNode, type TouchEvent as ReactTouchEvent } from "react";
import { createPortal } from "react-dom";
import type { EnsembleProductOption } from "@/lib/api";
import { ArrowLeftRight, Layers, Moon, Sun, X } from "lucide-react";

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

type CompareMobileDrawerBaseProps = {
  open: boolean;
  onClose: () => void;
  activeTab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
  lModel: string;
  rModel: string;
  lRun: string;
  rRun: string;
  modelOptions: GroupedOption[];
  variableCatalog: VariableOption[];
  leftRunOptions: RunOption[];
  rightRunOptions: RunOption[];
  onLeftModelChange: (value: string) => void;
  onRightModelChange: (value: string) => void;
  onLeftRunChange: (value: string) => void;
  onRightRunChange: (value: string) => void;
  onSwap: () => void;
  swapDisabled: boolean;
  basemapMode: BasemapMode;
  onToggleBasemap: () => void;
  showLegends: boolean;
  onToggleLegends: () => void;
};

type CompareMobileDrawerDiffProps = CompareMobileDrawerBaseProps & {
  compareMode: "diff";
  sharedVariable: string;
  diffMutualVariables: string[];
  onSharedVariableChange: (value: string) => void;
  // Ensemble stats product, shared like the variable (stats design §7).
  sharedProduct: string;
  mutualProducts: EnsembleProductOption[];
  productAvailability: Record<string, boolean>;
  onSharedProductChange: (value: string) => void;
};

type CompareMobileDrawerSplitProps = CompareMobileDrawerBaseProps & {
  compareMode: "split";
  lVariable: string;
  rVariable: string;
  leftVariableIds: string[];
  rightVariableIds: string[];
  onLeftVariableChange: (value: string) => void;
  onRightVariableChange: (value: string) => void;
  // Ensemble stats products per panel (stats design §7).
  lProduct: string;
  rProduct: string;
  lProducts: EnsembleProductOption[];
  rProducts: EnsembleProductOption[];
  lProductAvailability: Record<string, boolean>;
  rProductAvailability: Record<string, boolean>;
  onLeftProductChange: (value: string) => void;
  onRightProductChange: (value: string) => void;
};

export type CompareMobileDrawerProps = CompareMobileDrawerDiffProps | CompareMobileDrawerSplitProps;

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

function DisplaySettings({
  basemapMode,
  onToggleBasemap,
  showLegends,
  onToggleLegends,
}: {
  basemapMode: BasemapMode;
  onToggleBasemap: () => void;
  showLegends: boolean;
  onToggleLegends: () => void;
}) {
  return (
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
  );
}

function DiffComparisonFields({
  lModel,
  rModel,
  lRun,
  rRun,
  sharedVariable,
  diffMutualVariables,
  sharedProduct,
  mutualProducts,
  productAvailability,
  modelSelectOptions,
  leftRunOptions,
  rightRunOptions,
  variableCatalog,
  onLeftModelChange,
  onRightModelChange,
  onSharedVariableChange,
  onSharedProductChange,
  onLeftRunChange,
  onRightRunChange,
  onSwap,
  swapDisabled,
}: {
  lModel: string;
  rModel: string;
  lRun: string;
  rRun: string;
  sharedVariable: string;
  diffMutualVariables: string[];
  sharedProduct: string;
  mutualProducts: EnsembleProductOption[];
  productAvailability: Record<string, boolean>;
  modelSelectOptions: RunOption[];
  leftRunOptions: RunOption[];
  rightRunOptions: RunOption[];
  variableCatalog: VariableOption[];
  onLeftModelChange: (value: string) => void;
  onRightModelChange: (value: string) => void;
  onSharedVariableChange: (value: string) => void;
  onSharedProductChange: (value: string) => void;
  onLeftRunChange: (value: string) => void;
  onRightRunChange: (value: string) => void;
  onSwap: () => void;
  swapDisabled: boolean;
}) {
  const variableSelectOptions = diffMutualVariables.map((key) => ({
    value: key,
    label: variableCatalog.find((entry) => entry.value === key)?.label ?? key,
  }));
  const variablesDisabled = variableSelectOptions.length === 0;
  const productSelectOptions = mutualProducts
    .filter((entry) => entry.key === "mean" || productAvailability[entry.key])
    .map((entry) => ({ value: entry.key, label: entry.long_label ?? entry.label ?? entry.key }));
  const showProductSelect = productSelectOptions.length > 1;

  return (
    <>
      <div className="flex items-end gap-2">
        <Field label="Left Model">
          <DrawerSelect value={lModel} onValueChange={onLeftModelChange} options={modelSelectOptions} placeholder="Model" />
        </Field>
        <button
          type="button"
          onClick={onSwap}
          disabled={swapDisabled}
          aria-label="Swap left and right models"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/[0.14] bg-[#07111f] text-white/55 transition-all hover:border-white/30 hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
        >
          <ArrowLeftRight className="h-4 w-4" />
        </button>
        <Field label="Right Model">
          <DrawerSelect value={rModel} onValueChange={onRightModelChange} options={modelSelectOptions} placeholder="Model" />
        </Field>
      </div>

      <Field label="Variable (Shared)" className="mt-3">
        <DrawerSelect
          value={sharedVariable}
          onValueChange={onSharedVariableChange}
          options={variableSelectOptions}
          placeholder={variablesDisabled ? "No shared variable" : "Variable"}
          disabled={variablesDisabled}
        />
      </Field>

      {showProductSelect ? (
        <Field label="Product (Shared)" className="mt-3">
          <DrawerSelect
            value={sharedProduct || "mean"}
            onValueChange={(value) => onSharedProductChange(value === "mean" ? "" : value)}
            options={productSelectOptions}
            placeholder="Product"
          />
        </Field>
      ) : null}

      <div className="my-3 border-t border-white/[0.08]" />

      <div className="flex items-end gap-2">
        <Field label="L Run">
          <DrawerSelect value={lRun} onValueChange={onLeftRunChange} options={leftRunOptions} placeholder="Run" />
        </Field>
        <Field label="R Run">
          <DrawerSelect value={rRun} onValueChange={onRightRunChange} options={rightRunOptions} placeholder="Run" />
        </Field>
      </div>
    </>
  );
}

function SplitComparisonFields({
  lModel,
  rModel,
  lVariable,
  rVariable,
  lRun,
  rRun,
  leftVariableIds,
  rightVariableIds,
  lProduct,
  rProduct,
  lProducts,
  rProducts,
  lProductAvailability,
  rProductAvailability,
  modelSelectOptions,
  leftRunOptions,
  rightRunOptions,
  variableCatalog,
  onLeftModelChange,
  onRightModelChange,
  onLeftVariableChange,
  onRightVariableChange,
  onLeftProductChange,
  onRightProductChange,
  onLeftRunChange,
  onRightRunChange,
  onSwap,
  swapDisabled,
}: {
  lModel: string;
  rModel: string;
  lVariable: string;
  rVariable: string;
  lRun: string;
  rRun: string;
  leftVariableIds: string[];
  rightVariableIds: string[];
  lProduct: string;
  rProduct: string;
  lProducts: EnsembleProductOption[];
  rProducts: EnsembleProductOption[];
  lProductAvailability: Record<string, boolean>;
  rProductAvailability: Record<string, boolean>;
  modelSelectOptions: RunOption[];
  leftRunOptions: RunOption[];
  rightRunOptions: RunOption[];
  variableCatalog: VariableOption[];
  onLeftModelChange: (value: string) => void;
  onRightModelChange: (value: string) => void;
  onLeftVariableChange: (value: string) => void;
  onRightVariableChange: (value: string) => void;
  onLeftProductChange: (value: string) => void;
  onRightProductChange: (value: string) => void;
  onLeftRunChange: (value: string) => void;
  onRightRunChange: (value: string) => void;
  onSwap: () => void;
  swapDisabled: boolean;
}) {
  const variableOptionsForIds = (ids: string[]): RunOption[] =>
    ids.map((key) => ({
      value: key,
      label: variableCatalog.find((entry) => entry.value === key)?.label ?? key,
    }));
  const leftVariableOptions = variableOptionsForIds(leftVariableIds);
  const rightVariableOptions = variableOptionsForIds(rightVariableIds);
  const productOptionsFor = (
    products: EnsembleProductOption[],
    availability: Record<string, boolean>,
  ): RunOption[] =>
    products
      .filter((entry) => entry.key === "mean" || availability[entry.key])
      .map((entry) => ({ value: entry.key, label: entry.label ?? entry.key }));
  const leftProductOptions = productOptionsFor(lProducts, lProductAvailability);
  const rightProductOptions = productOptionsFor(rProducts, rProductAvailability);
  const showProductRow = leftProductOptions.length > 1 || rightProductOptions.length > 1;

  return (
    <>
      <div className="flex items-end gap-2">
        <Field label="Upper Model">
          <DrawerSelect value={lModel} onValueChange={onLeftModelChange} options={modelSelectOptions} placeholder="Model" />
        </Field>
        <button
          type="button"
          onClick={onSwap}
          disabled={swapDisabled}
          aria-label="Swap upper and lower panels"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/[0.14] bg-[#07111f] text-white/55 transition-all hover:border-white/30 hover:text-white disabled:cursor-not-allowed disabled:opacity-35"
        >
          <ArrowLeftRight className="h-4 w-4" />
        </button>
        <Field label="Lower Model">
          <DrawerSelect value={rModel} onValueChange={onRightModelChange} options={modelSelectOptions} placeholder="Model" />
        </Field>
      </div>

      <div className="mt-3 flex items-end gap-2">
        <Field label="Upper Variable">
          <DrawerSelect
            value={lVariable}
            onValueChange={onLeftVariableChange}
            options={leftVariableOptions}
            placeholder="Variable"
            disabled={leftVariableOptions.length === 0}
          />
        </Field>
        <Field label="Lower Variable">
          <DrawerSelect
            value={rVariable}
            onValueChange={onRightVariableChange}
            options={rightVariableOptions}
            placeholder="Variable"
            disabled={rightVariableOptions.length === 0}
          />
        </Field>
      </div>

      {showProductRow ? (
        <div className="mt-3 flex items-end gap-2">
          <Field label="Upper Product">
            <DrawerSelect
              value={lProduct || "mean"}
              onValueChange={(value) => onLeftProductChange(value === "mean" ? "" : value)}
              options={leftProductOptions}
              placeholder="Product"
              disabled={leftProductOptions.length <= 1}
            />
          </Field>
          <Field label="Lower Product">
            <DrawerSelect
              value={rProduct || "mean"}
              onValueChange={(value) => onRightProductChange(value === "mean" ? "" : value)}
              options={rightProductOptions}
              placeholder="Product"
              disabled={rightProductOptions.length <= 1}
            />
          </Field>
        </div>
      ) : null}

      <div className="my-3 border-t border-white/[0.08]" />

      <div className="flex items-end gap-2">
        <Field label="Upper Run">
          <DrawerSelect value={lRun} onValueChange={onLeftRunChange} options={leftRunOptions} placeholder="Run" />
        </Field>
        <Field label="Lower Run">
          <DrawerSelect value={rRun} onValueChange={onRightRunChange} options={rightRunOptions} placeholder="Run" />
        </Field>
      </div>
    </>
  );
}

type DrawerShellProps = {
  onClose: () => void;
  activeTab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
  basemapMode: BasemapMode;
  onToggleBasemap: () => void;
  showLegends: boolean;
  onToggleLegends: () => void;
  comparisonContent: ReactNode;
};

function DrawerShell({
  onClose,
  activeTab,
  onTabChange,
  basemapMode,
  onToggleBasemap,
  showLegends,
  onToggleLegends,
  comparisonContent,
}: DrawerShellProps) {
  const [snap, setSnap] = useState<"peek" | "full">("peek");
  const dragStartY = useRef<number | null>(null);

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

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-[65] transition-[background-color,backdrop-filter] duration-300",
          snap === "full" ? "bg-black/42 backdrop-blur-[6px]" : "bg-black/20",
        )}
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        style={{
          maxHeight: snap === "full" ? "90dvh" : "60dvh",
          transition: "max-height 0.35s cubic-bezier(0.32, 0.72, 0, 1)",
        }}
        className="viewer-mobile-control-surface fixed bottom-0 left-0 right-0 z-[66] flex max-w-full flex-col overflow-x-hidden overflow-y-hidden rounded-t-[1.5rem] [border-bottom:none] [border-left:none] [border-right:none] pb-[env(safe-area-inset-bottom)]"
        role="dialog"
        aria-label="Comparison settings"
      >
        <div
          className="flex min-h-11 touch-none select-none items-center justify-center active:opacity-70"
          onTouchStart={handleDragStart}
          onTouchEnd={handleDragEnd}
          onClick={handleHandleClick}
          role="button"
          aria-label={snap === "peek" ? "Expand comparison settings" : "Collapse comparison settings"}
        >
          <div className="h-1 w-10 rounded-full bg-white/25" />
        </div>

        <div className="flex shrink-0 items-center justify-between border-b border-white/[0.08] px-4 pt-2">
          <div className="flex">
            {([
              { id: "comparison" as const, label: "Comparison" },
              { id: "display" as const, label: "Display" },
            ]).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => onTabChange(tab.id)}
                className={cn(
                  "relative flex min-h-11 items-center pr-5 text-sm font-semibold transition-colors duration-150",
                  activeTab === tab.id ? "text-white" : "text-white/40 hover:text-white/65",
                )}
              >
                {tab.label}
                {activeTab === tab.id ? (
                  <span className="absolute bottom-0 left-0 right-5 h-[2px] rounded-full bg-cyan-400" />
                ) : null}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/60 hover:text-white"
            aria-label="Close comparison settings"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div
          className="min-h-0 overflow-y-auto px-4 pb-6 pt-3"
          style={{ maxHeight: snap === "full" ? "calc(90dvh - 5.5rem)" : "calc(60dvh - 5.5rem)" }}
        >
          {activeTab === "comparison" ? comparisonContent : (
            <DisplaySettings
              basemapMode={basemapMode}
              onToggleBasemap={onToggleBasemap}
              showLegends={showLegends}
              onToggleLegends={onToggleLegends}
            />
          )}
        </div>
      </div>
    </>
  );
}

/**
 * Bottom drawer holding compare picker controls on mobile. Mounts via portal
 * only while `open`; unmounting models the "closed" snap state.
 */
export function CompareMobileDrawer(props: CompareMobileDrawerProps) {
  if (!props.open) {
    return null;
  }

  const shellProps = {
    onClose: props.onClose,
    activeTab: props.activeTab,
    onTabChange: props.onTabChange,
    basemapMode: props.basemapMode,
    onToggleBasemap: props.onToggleBasemap,
    showLegends: props.showLegends,
    onToggleLegends: props.onToggleLegends,
  };

  const modelSelectOptions: RunOption[] = props.modelOptions.map((option) => ({
    value: option.value,
    label: option.label,
  }));

  if (props.compareMode === "diff") {
    return createPortal(
      <DrawerShell
        {...shellProps}
        comparisonContent={(
          <DiffComparisonFields
            lModel={props.lModel}
            rModel={props.rModel}
            lRun={props.lRun}
            rRun={props.rRun}
            sharedVariable={props.sharedVariable}
            diffMutualVariables={props.diffMutualVariables}
            sharedProduct={props.sharedProduct}
            mutualProducts={props.mutualProducts}
            productAvailability={props.productAvailability}
            onSharedProductChange={props.onSharedProductChange}
            modelSelectOptions={modelSelectOptions}
            leftRunOptions={props.leftRunOptions}
            rightRunOptions={props.rightRunOptions}
            variableCatalog={props.variableCatalog}
            onLeftModelChange={props.onLeftModelChange}
            onRightModelChange={props.onRightModelChange}
            onSharedVariableChange={props.onSharedVariableChange}
            onLeftRunChange={props.onLeftRunChange}
            onRightRunChange={props.onRightRunChange}
            onSwap={props.onSwap}
            swapDisabled={props.swapDisabled}
          />
        )}
      />,
      document.body,
    );
  }

  return createPortal(
    <DrawerShell
      {...shellProps}
      comparisonContent={(
        <SplitComparisonFields
          lModel={props.lModel}
          rModel={props.rModel}
          lVariable={props.lVariable}
          rVariable={props.rVariable}
          lRun={props.lRun}
          rRun={props.rRun}
          leftVariableIds={props.leftVariableIds}
          rightVariableIds={props.rightVariableIds}
          lProduct={props.lProduct}
          rProduct={props.rProduct}
          lProducts={props.lProducts}
          rProducts={props.rProducts}
          lProductAvailability={props.lProductAvailability}
          rProductAvailability={props.rProductAvailability}
          modelSelectOptions={modelSelectOptions}
          leftRunOptions={props.leftRunOptions}
          rightRunOptions={props.rightRunOptions}
          variableCatalog={props.variableCatalog}
          onLeftModelChange={props.onLeftModelChange}
          onRightModelChange={props.onRightModelChange}
          onLeftVariableChange={props.onLeftVariableChange}
          onRightVariableChange={props.onRightVariableChange}
          onLeftProductChange={props.onLeftProductChange}
          onRightProductChange={props.onRightProductChange}
          onLeftRunChange={props.onLeftRunChange}
          onRightRunChange={props.onRightRunChange}
          onSwap={props.onSwap}
          swapDisabled={props.swapDisabled}
        />
      )}
    />,
    document.body,
  );
}

export default CompareMobileDrawer;
