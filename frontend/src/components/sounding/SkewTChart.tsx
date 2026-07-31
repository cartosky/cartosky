import { Fragment, useMemo } from "react";

import backgroundLines from "@/assets/skewt-background.json";
import {
  anchorProfile,
  BARB_CALM_KT,
  celsiusToFahrenheit,
  createSkewTGeometry,
  dataPath,
  decomposeBarb,
  SKEWT_COLORS,
  SKEWT_DEFAULT_PLOT_W,
  SKEWT_ISOTHERM_MIN,
  SKEWT_ISOTHERM_STEP,
  SKEWT_MAJOR_PRESSURES,
  SKEWT_MIXR_LABEL_P,
  SKEWT_MIXR_TOP_P,
  SKEWT_P_BOTTOM,
  SKEWT_P_TOP,
  SKEWT_T_MAX,
  SKEWT_TICK_PRESSURES,
  windSpeedKt,
  type SkewTGeometry,
} from "@/lib/skewt-geometry";
import type { SoundingFrame } from "@/lib/sounding-types";

/**
 * Hand-coded SVG Skew-T, ported from the validated v4 prototype
 * (Skew-T design 2026-07-30 §6, styling decisions §8 #3).
 *
 * Everything quiet in the background, two loud traces: the hierarchy is the
 * whole point of the reference composition. Geometry, palette and the surface
 * anchoring live in `lib/skewt-geometry.ts` so they are testable without a DOM.
 */

type BackgroundCurve = { label: number; p: number[]; t: number[] };
type BackgroundAsset = {
  dry_adiabats: BackgroundCurve[];
  moist_adiabats: BackgroundCurve[];
  mixing_ratio_lines: BackgroundCurve[];
};

const BACKGROUND = backgroundLines as unknown as BackgroundAsset;

/** Minimum vertical separation between wind barbs, in user units. */
const BARB_MIN_SPACING = 14;
/** Barb glyph geometry (fixed px, never scaled with the plot). */
const BARB_SHAFT = 26;
const BARB_FLAG = 9;
const BARB_STEP = 4.5;

const LEGEND_ROWS: Array<[string, string, number, string | undefined]> = [
  [SKEWT_COLORS.temperature, "Temperature", 2.1, undefined],
  [SKEWT_COLORS.dewpoint, "Dewpoint", 2.1, undefined],
  [SKEWT_COLORS.dryAdiabat, "Dry adiabats", 1, undefined],
  [SKEWT_COLORS.moistAdiabat, "Moist adiabats", 1, undefined],
  [SKEWT_COLORS.mixingRatio, "Mixing ratio", 1, "2 3"],
];

/**
 * The static background: isotherms, dry/moist adiabats, mixing-ratio lines.
 * Depends only on the geometry, so it is memoized away from the frame.
 */
function SkewTBackground({ geo }: { geo: SkewTGeometry }) {
  const isotherms = useMemo(() => {
    const out: Array<{ t: number; d: string }> = [];
    for (let t = SKEWT_ISOTHERM_MIN; t <= SKEWT_T_MAX; t += SKEWT_ISOTHERM_STEP) {
      out.push({ t, d: dataPath(geo, [SKEWT_P_BOTTOM, SKEWT_P_TOP], [t, t]) });
    }
    return out;
  }, [geo]);

  const mixingRatio = useMemo(
    () =>
      BACKGROUND.mixing_ratio_lines.map((curve) => {
        const keep = curve.p
          .map((_, index) => index)
          .filter((index) => curve.p[index] >= SKEWT_MIXR_TOP_P);
        const labelIndex = curve.p.findIndex((p) => p <= SKEWT_MIXR_LABEL_P);
        const label =
          labelIndex > 0
            ? {
                x: geo.xOf(curve.t[labelIndex], curve.p[labelIndex]),
                y: geo.yOf(curve.p[labelIndex]),
                text: String(curve.label),
              }
            : null;
        return {
          label: curve.label,
          d: dataPath(geo, keep.map((i) => curve.p[i]), keep.map((i) => curve.t[i])),
          valueLabel: label && label.x > geo.x0 + 8 && label.x < geo.x1 - 8 ? label : null,
        };
      }),
    [geo],
  );

  return (
    <g clipPath="url(#skewt-plotclip)">
      {isotherms.map(({ t, d }) => (
        <path
          key={`iso-${t}`}
          d={d}
          fill="none"
          stroke={t === 0 ? SKEWT_COLORS.isothermZero : SKEWT_COLORS.isotherm}
          strokeWidth={t === 0 ? 1 : 0.6}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {BACKGROUND.dry_adiabats.map((curve) => (
        <path
          key={`dry-${curve.label}`}
          d={dataPath(geo, curve.p, curve.t)}
          fill="none"
          stroke={SKEWT_COLORS.dryAdiabat}
          strokeWidth={0.6}
          opacity={0.5}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {BACKGROUND.moist_adiabats.map((curve) => (
        <path
          key={`moist-${curve.label}`}
          d={dataPath(geo, curve.p, curve.t)}
          fill="none"
          stroke={SKEWT_COLORS.moistAdiabat}
          strokeWidth={0.6}
          opacity={0.55}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {mixingRatio.map((curve) => (
        <Fragment key={`mixr-${curve.label}`}>
          <path
            d={curve.d}
            fill="none"
            stroke={SKEWT_COLORS.mixingRatio}
            strokeWidth={0.8}
            strokeDasharray="2 3"
            opacity={0.7}
            vectorEffect="non-scaling-stroke"
          />
          {curve.valueLabel ? (
            <text
              x={curve.valueLabel.x}
              y={curve.valueLabel.y + 3}
              fontSize={9}
              fontWeight={600}
              fill={SKEWT_COLORS.mixingRatioLabel}
              textAnchor="middle"
              stroke={SKEWT_COLORS.plotBackground}
              strokeWidth={2.4}
              paintOrder="stroke"
            >
              {curve.valueLabel.text}
            </text>
          ) : null}
        </Fragment>
      ))}
    </g>
  );
}

/** Standard barb: half = 5 kt, full = 10 kt, pennant = 50 kt. */
function WindBarb({ cx, cy, u, v }: { cx: number; cy: number; u: number; v: number }) {
  const speed = windSpeedKt(u, v);
  const stroke = SKEWT_COLORS.barb;

  if (speed < BARB_CALM_KT) {
    return <circle cx={cx} cy={cy} r={3} fill="none" stroke={stroke} strokeWidth={1} />;
  }

  const { pennants, fulls, halves } = decomposeBarb(speed);
  const magnitude = Math.hypot(u, v);
  // Shaft points toward where the wind comes FROM: geo (-u,-v) -> screen (-u, +v).
  const dx = -u / magnitude;
  const dy = v / magnitude;
  // Barbs sit on the left-of-travel side (NH): geo (-v, u) -> screen (-v, -u).
  const bx = -v / magnitude;
  const by = -u / magnitude;

  const at = (distance: number): [number, number] => [cx + dx * distance, cy + dy * distance];
  const nodes: React.ReactNode[] = [];
  let s = BARB_SHAFT;

  for (let i = 0; i < pennants; i += 1) {
    const [ax, ay] = at(s);
    const [ex, ey] = at(s - BARB_FLAG);
    nodes.push(
      <polygon
        key={`pennant-${i}`}
        points={`${ax},${ay} ${ax + bx * BARB_FLAG},${ay + by * BARB_FLAG} ${ex},${ey}`}
        fill={stroke}
        stroke={stroke}
        strokeWidth={0.5}
      />,
    );
    s -= BARB_FLAG + 1.5;
  }
  if (pennants && (fulls || halves)) s -= 1.5;
  for (let i = 0; i < fulls; i += 1) {
    const [ax, ay] = at(s);
    nodes.push(
      <line
        key={`full-${i}`}
        x1={ax}
        y1={ay}
        x2={ax + bx * BARB_FLAG}
        y2={ay + by * BARB_FLAG}
        stroke={stroke}
        strokeWidth={1.1}
      />,
    );
    s -= BARB_STEP;
  }
  if (halves) {
    // A lone half-barb is inset from the tip so it reads as a half, not a full.
    if (!pennants && !fulls) s -= BARB_STEP;
    const [ax, ay] = at(s);
    nodes.push(
      <line
        key="half"
        x1={ax}
        y1={ay}
        x2={ax + bx * BARB_FLAG * 0.5}
        y2={ay + by * BARB_FLAG * 0.5}
        stroke={stroke}
        strokeWidth={1.1}
      />,
    );
  }

  const [tipX, tipY] = at(BARB_SHAFT);
  return (
    <g>
      <line x1={cx} y1={cy} x2={tipX} y2={tipY} stroke={stroke} strokeWidth={1.1} />
      <circle cx={cx} cy={cy} r={1.4} fill={stroke} />
      {nodes}
    </g>
  );
}

export type SkewTChartProps = {
  levelsHPa: number[];
  frame: SoundingFrame;
  /** Plot-rectangle width in user units; the SVG scales to its container. */
  plotWidth?: number;
  className?: string;
  title?: string;
};

export function SkewTChart({
  levelsHPa,
  frame,
  plotWidth = SKEWT_DEFAULT_PLOT_W,
  className,
  title,
}: SkewTChartProps) {
  const geo = useMemo(() => createSkewTGeometry(plotWidth), [plotWidth]);
  const profile = useMemo(() => anchorProfile(levelsHPa, frame), [levelsHPa, frame]);

  const tracePaths = useMemo(
    () => ({
      // Decision #3: the dewpoint trace is drawn FULL height — no fade, no clip.
      td: dataPath(geo, profile.pressures, profile.td),
      t: dataPath(geo, profile.pressures, profile.t),
    }),
    [geo, profile],
  );

  const barbs = useMemo(() => {
    const out: Array<{ key: string; y: number; u: number; v: number }> = [];
    let lastY = Number.POSITIVE_INFINITY;
    for (let i = 0; i < profile.pressures.length; i += 1) {
      const u = profile.u[i];
      const v = profile.v[i];
      if (u == null || v == null || !Number.isFinite(u) || !Number.isFinite(v)) continue;
      const y = geo.yOf(profile.pressures[i]);
      if (y < geo.y0 || y > geo.y1) continue;
      if (lastY - y < BARB_MIN_SPACING) continue;
      lastY = y;
      out.push({ key: `barb-${i}`, y, u, v });
    }
    return out;
  }, [geo, profile]);

  const surfaceY = profile.surfacePressure != null ? geo.yOf(profile.surfacePressure) : null;
  const surfaceMarkers =
    profile.surfacePressure != null
      ? ([
          [profile.td[0], SKEWT_COLORS.dewpoint, -7, "end" as const, "td"],
          [profile.t[0], SKEWT_COLORS.temperature, 7, "start" as const, "t"],
        ] as const)
      : [];

  const legendWidth = 104;
  const legendHeight = 11 + LEGEND_ROWS.length * 12;
  const legendX = geo.x0 + 8;
  const legendY = geo.y0 + 8;

  return (
    <svg
      className={className}
      data-testid="skewt-chart"
      data-masked-levels={profile.maskedCount}
      data-barbs={barbs.length}
      viewBox={`0 0 ${geo.width.toFixed(1)} ${geo.height.toFixed(1)}`}
      width="100%"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={title ?? "Skew-T log-p sounding"}
      style={{ display: "block", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
    >
      <defs>
        <clipPath id="skewt-plotclip">
          <rect x={geo.x0} y={geo.y0} width={geo.plotW} height={geo.plotH} />
        </clipPath>
      </defs>

      <rect
        x={geo.x0}
        y={geo.y0}
        width={geo.plotW}
        height={geo.plotH}
        fill={SKEWT_COLORS.plotBackground}
      />

      <SkewTBackground geo={geo} />

      {/* --- isobars + pressure labels (TT ladder) --- */}
      {SKEWT_TICK_PRESSURES.map((p) => {
        const y = geo.yOf(p);
        const major = (SKEWT_MAJOR_PRESSURES as readonly number[]).includes(p);
        return (
          <Fragment key={`p-${p}`}>
            <line
              x1={geo.x0}
              y1={y}
              x2={geo.x1}
              y2={y}
              stroke={major ? "hsl(215 14% 40%)" : SKEWT_COLORS.gridMinor}
              strokeWidth={major ? 0.9 : 0.5}
              vectorEffect="non-scaling-stroke"
            />
            <line
              x1={geo.x0 - (major ? 5 : 3)}
              y1={y}
              x2={geo.x0}
              y2={y}
              stroke="hsl(215 14% 45%)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            {major ? (
              <text
                x={geo.x0 - 8}
                y={y + 3.5}
                fontSize={10}
                textAnchor="end"
                fill={SKEWT_COLORS.tickLabel}
              >
                {p}
              </text>
            ) : null}
          </Fragment>
        );
      })}
      <text
        x={13}
        y={geo.y0 + geo.plotH / 2}
        fontSize={10}
        textAnchor="middle"
        fill={SKEWT_COLORS.axisLabel}
        transform={`rotate(-90 13 ${geo.y0 + geo.plotH / 2})`}
      >
        pressure (hPa)
      </text>

      {/* --- isotherm labels along the bottom axis --- */}
      {(() => {
        const labels: React.ReactNode[] = [];
        for (let t = SKEWT_ISOTHERM_MIN; t <= SKEWT_T_MAX; t += SKEWT_ISOTHERM_STEP) {
          const x = geo.xOf(t, SKEWT_P_BOTTOM);
          if (x < geo.x0 - 1 || x > geo.x1 + 1) continue;
          labels.push(
            <Fragment key={`tlabel-${t}`}>
              <line
                x1={x}
                y1={geo.y1}
                x2={x}
                y2={geo.y1 + 4}
                stroke="hsl(215 14% 45%)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={x}
                y={geo.y1 + 15}
                fontSize={10}
                textAnchor="middle"
                fill={SKEWT_COLORS.tickLabel}
              >
                {t}
              </text>
            </Fragment>,
          );
        }
        return labels;
      })()}
      <text
        x={geo.x0 + geo.plotW / 2}
        y={geo.y1 + 32}
        fontSize={10}
        textAnchor="middle"
        fill={SKEWT_COLORS.axisLabel}
      >
        temperature (°C)
      </text>

      <rect
        x={geo.x0}
        y={geo.y0}
        width={geo.plotW}
        height={geo.plotH}
        fill="none"
        stroke={SKEWT_COLORS.frame}
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />

      {/* --- data traces --- */}
      <g clipPath="url(#skewt-plotclip)">
        <path
          data-testid="skewt-trace-td"
          d={tracePaths.td}
          fill="none"
          stroke={SKEWT_COLORS.dewpoint}
          strokeWidth={2.1}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        <path
          data-testid="skewt-trace-t"
          d={tracePaths.t}
          fill="none"
          stroke={SKEWT_COLORS.temperature}
          strokeWidth={2.1}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />

        {surfaceY != null ? (
          <line
            data-testid="skewt-ground-line"
            x1={geo.x0}
            y1={surfaceY}
            x2={geo.x1}
            y2={surfaceY}
            stroke={SKEWT_COLORS.ground}
            strokeWidth={1}
            strokeDasharray="2 3"
            opacity={0.8}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}

        {surfaceY != null
          ? surfaceMarkers.map(([value, color, dx, anchor, key]) => {
              if (value == null || !Number.isFinite(value)) return null;
              const x = geo.xOf(value, Number(profile.surfacePressure));
              return (
                <Fragment key={`sfc-${key}`}>
                  <circle cx={x} cy={surfaceY} r={2.6} fill={color} />
                  <text
                    x={x + dx}
                    y={surfaceY + 3.5}
                    fontSize={10}
                    fontWeight={700}
                    fill={color}
                    textAnchor={anchor}
                    stroke={SKEWT_COLORS.plotBackground}
                    strokeWidth={2.6}
                    paintOrder="stroke"
                  >
                    {`${Math.round(celsiusToFahrenheit(value))}F`}
                  </text>
                </Fragment>
              );
            })
          : null}
      </g>

      {/* --- wind barbs --- */}
      <line
        x1={geo.barbCx}
        y1={geo.y0}
        x2={geo.barbCx}
        y2={geo.y1}
        stroke={SKEWT_COLORS.gridMinor}
        strokeWidth={0.5}
        vectorEffect="non-scaling-stroke"
      />
      <g data-testid="skewt-barbs">
        {barbs.map((barb) => (
          <WindBarb key={barb.key} cx={geo.barbCx} cy={barb.y} u={barb.u} v={barb.v} />
        ))}
      </g>
      <text
        x={geo.barbCx}
        y={geo.y1 + 15}
        fontSize={10}
        textAnchor="middle"
        fill={SKEWT_COLORS.axisLabel}
      >
        kt
      </text>

      {/* --- legend --- */}
      <g data-testid="skewt-legend">
        <rect
          x={legendX}
          y={legendY}
          width={legendWidth}
          height={legendHeight}
          rx={4}
          fill="hsl(222 24% 8% / 0.9)"
          stroke="hsl(222 16% 24%)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
        {LEGEND_ROWS.map(([color, label, weight, dash], index) => {
          const y = legendY + 14 + index * 12;
          return (
            <Fragment key={label}>
              <line
                x1={legendX + 7}
                y1={y - 3}
                x2={legendX + 21}
                y2={y - 3}
                stroke={color}
                strokeWidth={weight}
                strokeDasharray={dash}
                vectorEffect="non-scaling-stroke"
              />
              <text x={legendX + 26} y={y} fontSize={8.5} fill="hsl(215 14% 62%)">
                {label}
              </text>
            </Fragment>
          );
        })}
      </g>
    </svg>
  );
}

export default SkewTChart;
