import type { ComponentType, ReactNode } from "react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, AlertCircle, Gauge, Globe, Layers, PauseCircle, RefreshCcw, TimerReset, X, Zap } from "lucide-react";

import {
  fetchAdminPerfBreakdown,
  fetchAdminPerfSummary,
  fetchAdminPerfTimeseries,
  fetchTwfStatus,
  type PerfBreakdownItem,
  type PerfMetricSummary,
  type PerfTimeseriesPoint,
  type TwfStatus,
} from "@/lib/admin-api";
import { isLegacyPerfTelemetryEnabled } from "@/lib/config";

type WindowValue = "24h" | "7d" | "30d";
type DeviceValue = "all" | "desktop" | "mobile";
type LatestRunsValue = "all" | "1" | "2" | "4" | "8";
type TrendPercentile = "p95" | "p50";
type MetricKey =
  | "frame_change"
  | "loop_start"
  | "scrub_latency"
  | "animation_stall"
  | "viewer_first_frame"
  | "variable_switch"
  | "tile_fetch"
  | "loop_manifest_resolve"
  | "loop_decode_ready"
  | "loop_decode_to_commit"
  | "loop_commit_to_visible"
  | "long_task_blocking"
  | "loop_frame_drop_gap";

function formatMs(value: number | null | undefined): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return `${Math.round(Number(value))} ms`;
}

function formatCount(value: number | null | undefined): string {
  if (!Number.isFinite(value)) {
    return "0";
  }
  return new Intl.NumberFormat("en-US").format(Number(value));
}

function trendValue(point: PerfTimeseriesPoint, percentile: TrendPercentile): number | null {
  return percentile === "p50" ? point.p50_ms : point.p95_ms;
}

type MetricStatusTone = "good" | "warning" | "bad" | "unknown";

function getMetricStatus(metric?: PerfMetricSummary): {
  tone: MetricStatusTone;
  label: string;
  accentClassName: string;
  iconClassName: string;
  badgeClassName: string;
} {
  const target = metric?.target_ms ?? null;
  const p95 = metric?.p95_ms ?? null;

  if (target === null || p95 === null || target <= 0) {
    return {
      tone: "unknown",
      label: "Target unavailable",
      accentClassName: "text-[#9dd5bf]",
      iconClassName: "border-white/10 bg-white/[0.05] text-white/76",
      badgeClassName: "border-white/10 bg-white/[0.04] text-white/54",
    };
  }

  const ratio = p95 / target;

  if (ratio <= 0.8) {
    return {
      tone: "good",
      label: `Well under target ${formatMs(target)}`,
      accentClassName: "text-emerald-300",
      iconClassName: "border-emerald-400/25 bg-emerald-500/12 text-emerald-100",
      badgeClassName: "border-emerald-400/25 bg-emerald-500/12 text-emerald-100",
    };
  }

  if (ratio <= 1) {
    return {
      tone: "warning",
      label: `Near target ${formatMs(target)}`,
      accentClassName: "text-amber-300",
      iconClassName: "border-amber-400/25 bg-amber-500/12 text-amber-100",
      badgeClassName: "border-amber-400/25 bg-amber-500/12 text-amber-100",
    };
  }

  return {
    tone: "bad",
    label: `Over target ${formatMs(target)}`,
    accentClassName: "text-rose-300",
    iconClassName: "border-rose-400/25 bg-rose-500/12 text-rose-100",
    badgeClassName: "border-rose-400/25 bg-rose-500/12 text-rose-100",
  };
}

function MetricCard(props: {
  title: string;
  icon: ComponentType<{ className?: string }>;
  metric?: PerfMetricSummary;
  onClick?: () => void;
}) {
  const { title, icon: Icon, metric, onClick } = props;
  const status = getMetricStatus(metric);
  const p95 = metric?.p95_ms ?? null;
  const cardClassName =
    status.tone === "good"
      ? "border-emerald-400/20 bg-[linear-gradient(180deg,rgba(16,185,129,0.09),rgba(0,0,0,0.28))]"
      : status.tone === "warning"
        ? "border-amber-400/20 bg-[linear-gradient(180deg,rgba(245,158,11,0.09),rgba(0,0,0,0.28))]"
        : status.tone === "bad"
          ? "border-rose-400/20 bg-[linear-gradient(180deg,rgba(244,63,94,0.1),rgba(0,0,0,0.28))]"
          : "border-white/12 bg-black/28";
  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-white">{title}</div>
          <div className="mt-1 text-xs uppercase tracking-[0.22em] text-white/42">p95</div>
        </div>
        <div className={`rounded-xl border p-2 ${status.iconClassName}`}>
          <Icon className="h-4 w-4" />
        </div>
      </div>

      <div className={`mt-5 text-[2.2rem] font-semibold tracking-tight ${status.accentClassName}`}>{formatMs(p95)}</div>
      <div className="mt-2 flex items-center gap-2 text-sm text-white/62">
        <span className={status.accentClassName}>p50 {formatMs(metric?.p50_ms)}</span>
        <span className="text-white/24">•</span>
        <span>{formatCount(metric?.count)} samples</span>
      </div>
      <div className={`mt-4 inline-flex rounded-full border px-3 py-1 text-[11px] font-medium ${status.badgeClassName}`}>
        {status.label}
      </div>
      {onClick ? (
        <div className="mt-4 text-xs font-medium uppercase tracking-[0.18em] text-white/48">
          Open metric details
        </div>
      ) : null}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`w-full rounded-[24px] border p-5 text-left shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl transition hover:-translate-y-0.5 hover:border-white/20 hover:bg-white/[0.05] ${cardClassName}`}
        aria-label={`Open ${title} details`}
      >
        {content}
      </button>
    );
  }

  return <section className={`rounded-[24px] border p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl ${cardClassName}`}>{content}</section>;
}

function TrendChart(props: {
  title: string;
  subtitle: string;
  points: PerfTimeseriesPoint[];
  lineColor: string;
  percentile: TrendPercentile;
}) {
  const { title, subtitle, points, lineColor, percentile } = props;
  const values = points
    .map((point) => trendValue(point, percentile))
    .filter((value): value is number => Number.isFinite(value));

  if (values.length === 0) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="text-lg font-semibold text-white">{title}</div>
        <p className="mt-1 text-sm text-white/58">{subtitle}</p>
        <div className="mt-8 rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-10 text-sm text-white/48">
          No data yet for this window.
        </div>
      </section>
    );
  }

  const width = 680;
  const height = 220;
  const paddingX = 24;
  const paddingY = 20;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const path = points
    .map((point, index) => {
      const x = paddingX + (index * (width - paddingX * 2)) / Math.max(1, points.length - 1);
      const rawValue = Number(trendValue(point, percentile) ?? min);
      const y = height - paddingY - ((rawValue - min) / span) * (height - paddingY * 2);
      return `${index === 0 ? "M" : "L"} ${x} ${y}`;
    })
    .join(" ");
  const areaPath = `${path} L ${width - paddingX} ${height - paddingY} L ${paddingX} ${height - paddingY} Z`;

  return (
    <section className="rounded-[28px] border border-white/12 bg-black/28 p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-lg font-semibold text-white">{title}</div>
          <p className="mt-1 text-sm text-white/58">{subtitle}</p>
        </div>
        <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-white/60">
          {percentile} trend
        </div>
      </div>

      <div className="mt-5 overflow-hidden rounded-[22px] border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-4">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-[220px] w-full">
          <defs>
            <linearGradient id={`area-${title.replace(/\s+/g, "-").toLowerCase()}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.34" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0.04" />
            </linearGradient>
          </defs>

          {[0, 1, 2, 3].map((row) => {
            const y = paddingY + (row * (height - paddingY * 2)) / 3;
            return <line key={row} x1={paddingX} y1={y} x2={width - paddingX} y2={y} stroke="rgba(255,255,255,0.08)" />;
          })}

          <path d={areaPath} fill={`url(#area-${title.replace(/\s+/g, "-").toLowerCase()})`} />
          <path d={path} fill="none" stroke={lineColor} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />

          {points.map((point, index) => {
            const x = paddingX + (index * (width - paddingX * 2)) / Math.max(1, points.length - 1);
            const rawValue = Number(trendValue(point, percentile) ?? min);
            const y = height - paddingY - ((rawValue - min) / span) * (height - paddingY * 2);
            return <circle key={point.bucket_start} cx={x} cy={y} r="3.5" fill={lineColor} />;
          })}
        </svg>

        <div className="mt-4 flex items-center justify-between gap-2 text-[11px] uppercase tracking-[0.22em] text-white/42">
          <span>{points[0]?.bucket_start?.slice(0, 10) ?? ""}</span>
          <span>{points[points.length - 1]?.bucket_start?.slice(0, 10) ?? ""}</span>
        </div>
      </div>
    </section>
  );
}

function BreakdownList(props: { title: string; subtitle: string; items: PerfBreakdownItem[] }) {
  const { title, subtitle, items } = props;

  return (
    <section className="rounded-[28px] border border-white/12 bg-black/28 p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
      <div className="text-lg font-semibold text-white">{title}</div>
      <p className="mt-1 text-sm text-white/58">{subtitle}</p>

      <div className="mt-5 space-y-3">
        {items.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-sm text-white/48">
            No data yet for this breakdown.
          </div>
        ) : (
          items.map((item) => {
            const status = getMetricStatus(item);
            return (
              <div key={item.key} className="rounded-2xl border border-white/10 bg-white/[0.035] px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-white">{item.key}</div>
                    <div className="mt-1 text-xs text-white/48">{formatCount(item.count)} samples</div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-semibold ${status.accentClassName}`}>{formatMs(item.p95_ms)}</div>
                    <div className={`mt-1 text-xs ${status.accentClassName}`}>p50 {formatMs(item.p50_ms)}</div>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function MetricDetailDialog(props: {
  open: boolean;
  title: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  metric?: PerfMetricSummary;
  onClose: () => void;
  children?: ReactNode;
}) {
  const { open, title, description, icon, metric, onClose, children } = props;

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="Close metric details"
        className="absolute inset-0 bg-black/82 backdrop-blur-md"
        onClick={onClose}
      />
      <div className="relative z-10 flex h-full items-center justify-center p-4 md:p-6">
        <section
          role="dialog"
          aria-modal="true"
          aria-label={`${title} details`}
          className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-[32px] border border-white/12 bg-black/72 shadow-[0_24px_80px_rgba(0,0,0,0.55)] backdrop-blur-2xl"
        >
          <div className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4 md:px-6">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#95b1a2]">Metric details</div>
              <h3 className="mt-2 text-2xl font-semibold tracking-tight text-white">{title}</h3>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-white/62">{description}</p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-white/12 bg-white/[0.04] text-white/74 transition hover:bg-white/[0.08]"
              aria-label="Close metric details"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="overflow-y-auto px-5 py-5 md:px-6 md:py-6">
            <div className="space-y-6">
              <MetricCard title={title} icon={icon} metric={metric} />
              {children ? (
                children
              ) : (
                <section className="rounded-[28px] border border-dashed border-white/10 bg-white/[0.03] px-5 py-8 text-sm text-white/52">
                  No detailed panels are available for this metric yet.
                </section>
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function SectionLabel(props: { label: string; description: string; children: ReactNode }) {
  return (
    <div className="space-y-4 pt-4">
      <div className="flex items-center gap-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#95b1a2]">{props.label}</div>
          <p className="mt-0.5 text-sm text-white/48">{props.description}</p>
        </div>
        <div className="flex-1 border-t border-white/8" />
      </div>
      {props.children}
    </div>
  );
}

export default function AdminPerformancePage() {
  const legacyPerfEnabled = isLegacyPerfTelemetryEnabled();
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [windowValue, setWindowValue] = useState<WindowValue>("7d");
  const [deviceValue, setDeviceValue] = useState<DeviceValue>("all");
  const [latestRunsValue, setLatestRunsValue] = useState<LatestRunsValue>("all");
  const [trendPercentile, setTrendPercentile] = useState<TrendPercentile>("p95");
  const [loading, setLoading] = useState(true);
  const [refreshTick, setRefreshTick] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, PerfMetricSummary>>({});
  const [frameTrend, setFrameTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopTrend, setLoopTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [firstFrameTrend, setFirstFrameTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [varSwitchTrend, setVarSwitchTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [tileFetchTrend, setTileFetchTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopManifestTrend, setLoopManifestTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopDecodeTrend, setLoopDecodeTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopDecodeToCommitTrend, setLoopDecodeToCommitTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopCommitToVisibleTrend, setLoopCommitToVisibleTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [longTaskTrend, setLongTaskTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [loopDropTrend, setLoopDropTrend] = useState<PerfTimeseriesPoint[]>([]);
  const [modelBreakdown, setModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [deviceBreakdown, setDeviceBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [frameDeviceBreakdown, setFrameDeviceBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopModelBreakdown, setLoopModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopVariableBreakdown, setLoopVariableBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [firstFrameModelBreakdown, setFirstFrameModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [firstFrameDeviceBreakdown, setFirstFrameDeviceBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [scrubModelBreakdown, setScrubModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [scrubVariableBreakdown, setScrubVariableBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [animationStallModelBreakdown, setAnimationStallModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [animationStallVariableBreakdown, setAnimationStallVariableBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [frameVariableBreakdown, setFrameVariableBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [varSwitchModelBreakdown, setVarSwitchModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [tileFetchModelBreakdown, setTileFetchModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopManifestModelBreakdown, setLoopManifestModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopDecodeModelBreakdown, setLoopDecodeModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopDecodeToCommitModelBreakdown, setLoopDecodeToCommitModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopCommitToVisibleModelBreakdown, setLoopCommitToVisibleModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [longTaskDeviceBreakdown, setLongTaskDeviceBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [loopDropModelBreakdown, setLoopDropModelBreakdown] = useState<PerfBreakdownItem[]>([]);
  const [activeMetric, setActiveMetric] = useState<MetricKey | null>(null);
  const latestRuns = latestRunsValue === "all" ? undefined : Number(latestRunsValue);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const authStatus = await fetchTwfStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) {
          setLoading(false);
          return;
        }
        if (!legacyPerfEnabled) {
          setLoading(false);
          return;
        }

        const [
          summaryData,
          frameSeries, loopSeries, firstFrameSeries, varSwitchSeries, tileFetchSeries,
          loopManifestSeries, loopDecodeSeries, loopDecodeToCommitSeries, loopCommitToVisibleSeries, longTaskSeries, loopDropSeries,
          modelData, frameDeviceData, deviceData, loopModelData, loopVariableData,
          firstFrameModelData, firstFrameDeviceData,
          scrubModelData, scrubVariableData, animationStallModelData, animationStallVariableData, frameVariableData,
          varSwitchModelData, tileFetchModelData,
          loopManifestModelData, loopDecodeModelData, loopDecodeToCommitModelData, loopCommitToVisibleModelData,
          longTaskDeviceData, loopDropModelData,
        ] = await Promise.all([
          fetchAdminPerfSummary({ window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "frame_change", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_start", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "viewer_first_frame", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "variable_switch", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "tile_fetch", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_manifest_resolve", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_decode_ready", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_decode_to_commit", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_commit_to_visible", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "long_task_blocking", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfTimeseries({ metric: "loop_frame_drop_gap", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "frame_change", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "frame_change", by: "device", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_start", by: "device", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_start", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_start", by: "variable", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "viewer_first_frame", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "viewer_first_frame", by: "device", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "scrub_latency", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "scrub_latency", by: "variable", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "animation_stall", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "animation_stall", by: "variable", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "frame_change", by: "variable", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "variable_switch", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "tile_fetch", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_manifest_resolve", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_decode_ready", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_decode_to_commit", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_commit_to_visible", by: "model", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "long_task_blocking", by: "device", window: windowValue, device: deviceValue, latestRuns }),
          fetchAdminPerfBreakdown({ metric: "loop_frame_drop_gap", by: "model", window: windowValue, device: deviceValue, latestRuns }),
        ]);
        if (cancelled) return;

        setSummary(summaryData.metrics);
        setFrameTrend(frameSeries.points);
        setLoopTrend(loopSeries.points);
        setFirstFrameTrend(firstFrameSeries.points);
        setVarSwitchTrend(varSwitchSeries.points);
        setTileFetchTrend(tileFetchSeries.points);
        setLoopManifestTrend(loopManifestSeries.points);
        setLoopDecodeTrend(loopDecodeSeries.points);
        setLoopDecodeToCommitTrend(loopDecodeToCommitSeries.points);
        setLoopCommitToVisibleTrend(loopCommitToVisibleSeries.points);
        setLongTaskTrend(longTaskSeries.points);
        setLoopDropTrend(loopDropSeries.points);
        setModelBreakdown(modelData.items);
        setFrameDeviceBreakdown(frameDeviceData.items);
        setDeviceBreakdown(deviceData.items);
        setLoopModelBreakdown(loopModelData.items);
        setLoopVariableBreakdown(loopVariableData.items);
        setFirstFrameModelBreakdown(firstFrameModelData.items);
        setFirstFrameDeviceBreakdown(firstFrameDeviceData.items);
        setScrubModelBreakdown(scrubModelData.items);
        setScrubVariableBreakdown(scrubVariableData.items);
        setAnimationStallModelBreakdown(animationStallModelData.items);
        setAnimationStallVariableBreakdown(animationStallVariableData.items);
        setFrameVariableBreakdown(frameVariableData.items);
        setVarSwitchModelBreakdown(varSwitchModelData.items);
        setTileFetchModelBreakdown(tileFetchModelData.items);
        setLoopManifestModelBreakdown(loopManifestModelData.items);
        setLoopDecodeModelBreakdown(loopDecodeModelData.items);
        setLoopDecodeToCommitModelBreakdown(loopDecodeToCommitModelData.items);
        setLoopCommitToVisibleModelBreakdown(loopCommitToVisibleModelData.items);
        setLongTaskDeviceBreakdown(longTaskDeviceData.items);
        setLoopDropModelBreakdown(loopDropModelData.items);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load admin dashboard");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [windowValue, deviceValue, latestRuns, refreshTick]);

  useEffect(() => {
    if (!activeMetric) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveMetric(null);
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [activeMetric]);

  if (loading && status === null) {
    return (
      <div className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Loading admin dashboard...
      </div>
    );
  }

  if (!status?.linked) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-center gap-2 text-lg font-semibold">
          <AlertCircle className="h-5 w-5 text-amber-200" />
          Login required
        </div>
        <p className="mt-3 max-w-xl text-sm leading-6 text-white/66">
          The admin dashboard is private. Sign in with your linked The Weather Forums account before opening this page.
        </p>
        <Link
          to="/login"
          className="mt-5 inline-flex rounded-xl border border-white/15 bg-white/[0.06] px-4 py-2 text-sm font-medium text-white hover:bg-white/[0.1]"
        >
          Open login
        </Link>
      </section>
    );
  }

  if (!status.admin) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-center gap-2 text-lg font-semibold">
          <AlertCircle className="h-5 w-5 text-amber-200" />
          Admin access required
        </div>
        <p className="mt-3 max-w-xl text-sm leading-6 text-white/66">
          Your account is linked, but it is not in the configured admin allowlist yet.
        </p>
      </section>
    );
  }

  if (!legacyPerfEnabled) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="text-2xl font-semibold tracking-tight text-white">Legacy Performance Retired</div>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/62">
          The legacy custom frontend performance dashboard has been retired for the Phase 6 cutoff. Use{" "}
          <Link to="/admin/overview" className="text-[#9dd5bf] underline underline-offset-4">Overview</Link>,{" "}
          <Link to="/admin/analytics" className="text-[#9dd5bf] underline underline-offset-4">Analytics</Link>,{" "}
          <Link to="/admin/observability" className="text-[#9dd5bf] underline underline-offset-4">Observability</Link>, and{" "}
          <Link to="/admin/traces" className="text-[#9dd5bf] underline underline-offset-4">Traces</Link> for the current telemetry sources of truth.
        </p>
      </section>
    );
  }

  const metricSections: Array<{
    label: string;
    description: string;
    metrics: Array<{
      key: MetricKey;
      title: string;
      description: string;
      icon: ComponentType<{ className?: string }>;
      metric?: PerfMetricSummary;
      detailContent?: ReactNode;
    }>;
  }> = [
    {
      label: "Playback & Animation",
      description: "Real-time frame rendering, loop playback, and scrub responsiveness.",
      metrics: [
        {
          key: "frame_change",
          title: "Frame Change",
          description: "How quickly the map responds to manual frame changes.",
          icon: Gauge,
          metric: summary.frame_change,
          detailContent: (
            <>
              <TrendChart
                title="Frame Change Trend"
                subtitle="How quickly the map responds to manual frame changes."
                points={frameTrend}
                lineColor="#7ec8ff"
                percentile={trendPercentile}
              />
              <div className="grid gap-6 xl:grid-cols-3">
                <BreakdownList
                  title="Frame Change by Model"
                  subtitle="Most active models ordered by sample count."
                  items={modelBreakdown}
                />
                <BreakdownList
                  title="Frame Change by Device"
                  subtitle="Frame change latency split by device class."
                  items={frameDeviceBreakdown}
                />
                <BreakdownList
                  title="Frame Change by Variable"
                  subtitle="Frame change latency split by variable — identifies render-heavy variables."
                  items={frameVariableBreakdown}
                />
              </div>
            </>
          ),
        },
        {
          key: "loop_start",
          title: "Loop Start",
          description: "Time from play action to the first advancing visible loop frame.",
          icon: Activity,
          metric: summary.loop_start,
          detailContent: (
            <>
              <TrendChart
                title="Loop Start Trend"
                subtitle="Time from play action to actual loop playback start."
                points={loopTrend}
                lineColor="#b7e38f"
                percentile={trendPercentile}
              />
              <div className="grid gap-6 xl:grid-cols-3">
                <BreakdownList
                  title="Loop Start by Model"
                  subtitle="Playback startup latency split by model."
                  items={loopModelBreakdown}
                />
                <BreakdownList
                  title="Loop Start by Device"
                  subtitle="Quick split of playback startup behavior."
                  items={deviceBreakdown}
                />
                <BreakdownList
                  title="Loop Start by Variable"
                  subtitle="Playback startup latency split by variable."
                  items={loopVariableBreakdown}
                />
              </div>
            </>
          ),
        },
        {
          key: "scrub_latency",
          title: "Scrub Latency",
          description: "Time from a committed scrub target to the exact requested frame becoming visible.",
          icon: TimerReset,
          metric: summary.scrub_latency,
          detailContent: (
            <div className="grid gap-6 xl:grid-cols-2">
              <BreakdownList
                title="Scrub Latency by Model"
                subtitle="Committed scrub latency per model — reveals which datasets take longest to land on the requested frame."
                items={scrubModelBreakdown}
              />
              <BreakdownList
                title="Scrub Latency by Variable"
                subtitle="Committed scrub latency split by variable."
                items={scrubVariableBreakdown}
              />
            </div>
          ),
        },
        {
          key: "animation_stall",
          title: "Animation Stall",
          description: "Counts slow playback frames that exceed the stall threshold.",
          icon: PauseCircle,
          metric: summary.animation_stall,
          detailContent: (
            <div className="grid gap-6 xl:grid-cols-2">
              <BreakdownList
                title="Animation Stall by Model"
                subtitle="Playback stalls split by model to isolate loop decode or readiness bottlenecks."
                items={animationStallModelBreakdown}
              />
              <BreakdownList
                title="Animation Stall by Variable"
                subtitle="Playback stalls split by variable to surface render-heavy products."
                items={animationStallVariableBreakdown}
              />
            </div>
          ),
        },
      ],
    },
    {
      label: "Cold Start & Navigation",
      description: "Initial viewer load time and variable switching latency.",
      metrics: [
        {
          key: "viewer_first_frame",
          title: "First Viewer Frame",
          description: "Time from viewer open to the first visible weather frame, whether loop or tiles.",
          icon: Zap,
          metric: summary.viewer_first_frame,
          detailContent: (
            <>
              <TrendChart
                title="First Viewer Frame Trend"
                subtitle="Time from viewer open to the first visible weather frame."
                points={firstFrameTrend}
                lineColor="#f0a575"
                percentile={trendPercentile}
              />
              <div className="grid gap-6 xl:grid-cols-2">
                <BreakdownList
                  title="First Viewer Frame by Model"
                  subtitle="Cold-start render latency per model — identifies slow-loading datasets."
                  items={firstFrameModelBreakdown}
                />
                <BreakdownList
                  title="First Viewer Frame by Device"
                  subtitle="Cold-start render latency split by device type."
                  items={firstFrameDeviceBreakdown}
                />
              </div>
            </>
          ),
        },
        {
          key: "variable_switch",
          title: "Variable Switch",
          description: "Time from variable selector click to first frame of the new variable.",
          icon: Layers,
          metric: summary.variable_switch,
          detailContent: (
            <>
              <TrendChart
                title="Variable Switch Trend"
                subtitle="Time from variable selector click to first frame of new variable."
                points={varSwitchTrend}
                lineColor="#c4a8f5"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Variable Switch by Model"
                subtitle="Time to first frame after a variable selection, per model."
                items={varSwitchModelBreakdown}
              />
            </>
          ),
        },
      ],
    },
    {
      label: "Network / Tile Fetch",
      description: "Individual weather tile network fetch latency from the CDN.",
      metrics: [
        {
          key: "tile_fetch",
          title: "Tile Fetch",
          description: "Individual weather tile network fetch duration from the CDN.",
          icon: Globe,
          metric: summary.tile_fetch,
          detailContent: (
            <>
              <TrendChart
                title="Tile Fetch Trend"
                subtitle="Individual weather tile network fetch duration (sampled 1-in-8)."
                points={tileFetchTrend}
                lineColor="#f5c842"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Tile Fetch by Model"
                subtitle="Sampled network fetch time per weather tile, split by model."
                items={tileFetchModelBreakdown}
              />
            </>
          ),
        },
      ],
    },
    {
      label: "Phase 0b Signals",
      description: "Loop pipeline and main-thread telemetry added for deeper render-path diagnosis.",
      metrics: [
        {
          key: "loop_manifest_resolve",
          title: "Loop Manifest Resolve",
          description: "Time to resolve loop manifest metadata for the selected model/run/variable.",
          icon: Activity,
          metric: summary.loop_manifest_resolve,
          detailContent: (
            <>
              <TrendChart
                title="Loop Manifest Resolve Trend"
                subtitle="Loop manifest resolve latency over time."
                points={loopManifestTrend}
                lineColor="#8fd0ff"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Loop Manifest Resolve by Model"
                subtitle="Model-level manifest resolve latency."
                items={loopManifestModelBreakdown}
              />
            </>
          ),
        },
        {
          key: "loop_decode_ready",
          title: "Loop Decode Ready",
          description: "Time from loop frame decode request to decoded frame readiness.",
          icon: Gauge,
          metric: summary.loop_decode_ready,
          detailContent: (
            <>
              <TrendChart
                title="Loop Decode Ready Trend"
                subtitle="Decoded-frame readiness timing for loop images."
                points={loopDecodeTrend}
                lineColor="#9ce3b4"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Loop Decode Ready by Model"
                subtitle="Decode readiness split by model."
                items={loopDecodeModelBreakdown}
              />
            </>
          ),
        },
        {
          key: "loop_decode_to_commit",
          title: "Loop Decode to Commit",
          description: "Age of a decoded loop frame when it is committed for presentation.",
          icon: TimerReset,
          metric: summary.loop_decode_to_commit,
          detailContent: (
            <>
              <TrendChart
                title="Loop Decode to Commit Trend"
                subtitle="How long decoded loop frames wait before presentation commit."
                points={loopDecodeToCommitTrend}
                lineColor="#f2c27a"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Loop Decode to Commit by Model"
                subtitle="Decoded-frame age at commit split by model."
                items={loopDecodeToCommitModelBreakdown}
              />
            </>
          ),
        },
        {
          key: "loop_commit_to_visible",
          title: "Loop Commit to Visible",
          description: "Time from loop frame commit to first visible paint confirmation.",
          icon: Zap,
          metric: summary.loop_commit_to_visible,
          detailContent: (
            <>
              <TrendChart
                title="Loop Commit to Visible Trend"
                subtitle="Commit-to-visible latency for loop frames."
                points={loopCommitToVisibleTrend}
                lineColor="#d5b2ff"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Loop Commit to Visible by Model"
                subtitle="Commit-to-visible timing split by model."
                items={loopCommitToVisibleModelBreakdown}
              />
            </>
          ),
        },
        {
          key: "long_task_blocking",
          title: "Long Task Blocking",
          description: "Main-thread long task durations sampled from browser PerformanceObserver.",
          icon: AlertCircle,
          metric: summary.long_task_blocking,
          detailContent: (
            <>
              <TrendChart
                title="Long Task Blocking Trend"
                subtitle="Sampled long-task durations (>=50ms)."
                points={longTaskTrend}
                lineColor="#ff9da8"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Long Task Blocking by Device"
                subtitle="Main-thread blocking split by device class."
                items={longTaskDeviceBreakdown}
              />
            </>
          ),
        },
        {
          key: "loop_frame_drop_gap",
          title: "Loop Frame Drop Gap",
          description: "Playback gap when next loop frames are not decoded in time.",
          icon: PauseCircle,
          metric: summary.loop_frame_drop_gap,
          detailContent: (
            <>
              <TrendChart
                title="Loop Frame Drop Gap Trend"
                subtitle="Gap duration while playback waits for decoded loop frames."
                points={loopDropTrend}
                lineColor="#ffcf8b"
                percentile={trendPercentile}
              />
              <BreakdownList
                title="Loop Frame Drop Gap by Model"
                subtitle="Frame-drop gap split by model."
                items={loopDropModelBreakdown}
              />
            </>
          ),
        },
      ],
    },
  ];
  const activeMetricConfig = metricSections
    .flatMap((section) => section.metrics)
    .find((metric) => metric.key === activeMetric);

  return (
    <>
      <div className="space-y-6">
        <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl md:p-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#95b1a2]">Legacy Performance</div>
            <h2 className="mt-2 text-4xl font-semibold tracking-tight text-white">Viewer telemetry comparison</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-white/62">
              Legacy custom viewer timings from the current frontend build. Use this page for migration comparison and engineering diagnostics, not as the primary source of truth for user-perceived performance.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] text-white/58">
              <span className="rounded-full border border-emerald-400/25 bg-emerald-500/12 px-3 py-1 text-emerald-100">
                Green = well under target
              </span>
              <span className="rounded-full border border-amber-400/25 bg-amber-500/12 px-3 py-1 text-amber-100">
                Yellow = near target
              </span>
              <span className="rounded-full border border-rose-400/25 bg-rose-500/12 px-3 py-1 text-rose-100">
                Red = over target
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="inline-flex items-center rounded-xl border border-white/12 bg-white/[0.04] p-1">
              <button
                type="button"
                onClick={() => setTrendPercentile("p95")}
                className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                  trendPercentile === "p95"
                    ? "bg-white/14 text-white"
                    : "text-white/70 hover:bg-white/[0.06]"
                }`}
                aria-pressed={trendPercentile === "p95"}
              >
                p95 trend
              </button>
              <button
                type="button"
                onClick={() => setTrendPercentile("p50")}
                className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                  trendPercentile === "p50"
                    ? "bg-white/14 text-white"
                    : "text-white/70 hover:bg-white/[0.06]"
                }`}
                aria-pressed={trendPercentile === "p50"}
              >
                p50 trend
              </button>
            </div>

            <select
              value={latestRunsValue}
              onChange={(event) => setLatestRunsValue(event.target.value as LatestRunsValue)}
              className="rounded-xl border border-white/12 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none"
            >
              <option value="all">All runs</option>
              <option value="1">Latest run</option>
              <option value="2">Latest 2 runs</option>
              <option value="4">Latest 4 runs</option>
              <option value="8">Latest 8 runs</option>
            </select>

            <select
              value={deviceValue}
              onChange={(event) => setDeviceValue(event.target.value as DeviceValue)}
              className="rounded-xl border border-white/12 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none"
            >
              <option value="all">All devices</option>
              <option value="desktop">Desktop</option>
              <option value="mobile">Mobile</option>
            </select>

            <select
              value={windowValue}
              onChange={(event) => setWindowValue(event.target.value as WindowValue)}
              className="rounded-xl border border-white/12 bg-white/[0.06] px-3 py-2 text-sm text-white outline-none"
            >
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7d</option>
              <option value="30d">Last 30d</option>
            </select>

            <button
              type="button"
              onClick={() => setRefreshTick((value) => value + 1)}
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-white/12 bg-white/[0.06] text-white hover:bg-white/[0.1]"
              aria-label="Refresh performance data"
              title="Refresh"
            >
              <RefreshCcw className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="mt-5 rounded-2xl border border-amber-400/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          These metrics are being demoted during the telemetry overhaul. Treat them as legacy comparison telemetry until Web Vitals,
          minimal CartoSky RUM, PostHog, Prometheus, and tracing are in place.
        </div>

        {error ? (
          <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        {latestRuns ? (
          <div className="mt-5 rounded-2xl border border-white/10 bg-white/[0.035] px-4 py-3 text-sm text-white/68">
            Showing only telemetry from the latest {latestRuns} run{latestRuns === 1 ? "" : "s"} per model within the selected window.
          </div>
        ) : null}
        </section>

        {metricSections.map((section) => (
          <SectionLabel key={section.label} label={section.label} description={section.description}>
            <div className={`grid gap-4 ${section.metrics.length >= 4 ? "xl:grid-cols-4" : section.metrics.length === 2 ? "xl:grid-cols-2" : "xl:grid-cols-3"}`}>
              {section.metrics.map((metric) => (
                <MetricCard
                  key={metric.key}
                  title={metric.title}
                  icon={metric.icon}
                  metric={metric.metric}
                  onClick={() => setActiveMetric(metric.key)}
                />
              ))}
            </div>
          </SectionLabel>
        ))}
      </div>

      <MetricDetailDialog
        open={activeMetricConfig !== undefined}
        title={activeMetricConfig?.title ?? ""}
        description={activeMetricConfig?.description ?? ""}
        icon={activeMetricConfig?.icon ?? Gauge}
        metric={activeMetricConfig?.metric}
        onClose={() => setActiveMetric(null)}
      >
        {activeMetricConfig?.detailContent}
      </MetricDetailDialog>
    </>
  );
}
