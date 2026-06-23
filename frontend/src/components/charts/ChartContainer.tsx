import type { ReactNode } from "react";

type ChartContainerProps = {
  title: string;
  subtitle?: string;
  filterSlot?: ReactNode;
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  children?: ReactNode;
};

/**
 * Card shell for Model Guidance charts. Matches Forecast page card styling
 * (p-4 md:p-5, bg-white/[0.03], border-white/10, rounded-xl). Renders loading
 * skeleton and inline error states while preserving the chart area.
 */
export function ChartContainer({
  title,
  subtitle,
  filterSlot,
  isLoading,
  error,
  onRetry,
  children,
}: ChartContainerProps) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 md:p-5">
      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-[14px] font-medium text-white/85">{title}</h3>
          {subtitle && <p className="mt-0.5 text-[12px] text-white/40">{subtitle}</p>}
        </div>
        {filterSlot && <div className="sm:flex-none">{filterSlot}</div>}
      </div>

      {isLoading ? (
        <div className="h-[320px] w-full animate-pulse rounded-lg bg-white/[0.04]" />
      ) : error ? (
        <div className="flex h-[320px] w-full flex-col items-center justify-center gap-3 rounded-lg bg-white/[0.02] text-center">
          <p className="px-6 text-[13px] text-white/55">{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md border border-white/15 px-3 py-1.5 text-[12px] text-white/70 transition-colors hover:bg-white/[0.06]"
            >
              Retry
            </button>
          )}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
