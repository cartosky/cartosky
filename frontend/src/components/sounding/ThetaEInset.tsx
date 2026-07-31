import { Fragment, useMemo } from "react";

import {
  buildThetaE,
  THETAE_P_BOTTOM,
  THETAE_P_TOP,
  THETAE_TICK_PRESSURES,
  type ThetaEInput,
} from "@/lib/sounding-insets";
import { SKEWT_COLORS } from "@/lib/skewt-geometry";

/**
 * θe against pressure (Skew-T design §7 Phase 5) — the panel TT places
 * bottom-right. Log-p on y over 1000-300 hPa, a data-driven θe domain on x.
 *
 * The classic read is the vertical minimum: the height of the driest-equivalent
 * air, which is why the y axis keeps the same log-p sense as the main chart
 * instead of being flipped to read like a normal line chart.
 */

const WIDTH = 210;
const HEIGHT = 210;
const MARGIN = { top: 8, right: 10, bottom: 20, left: 30 } as const;

const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;
const LOG_BOTTOM = Math.log(THETAE_P_BOTTOM);
const LOG_TOP = Math.log(THETAE_P_TOP);

export type ThetaEInsetProps = ThetaEInput & { className?: string };

export function ThetaEInset({
  levelsHPa,
  thetaE,
  surfaceP,
  surfaceValue,
  className,
}: ThetaEInsetProps) {
  const model = useMemo(
    () => buildThetaE({ levelsHPa, thetaE, surfaceP, surfaceValue }),
    [levelsHPa, thetaE, surfaceP, surfaceValue],
  );

  if (!model) return null;

  const yOf = (p: number) =>
    MARGIN.top + PLOT_H - ((LOG_BOTTOM - Math.log(p)) / (LOG_BOTTOM - LOG_TOP)) * PLOT_H;
  const xOf = (value: number) =>
    MARGIN.left + ((value - model.xMin) / (model.xMax - model.xMin)) * PLOT_W;

  const d = model.points
    .map((point, index) => `${index === 0 ? "M" : "L"}${xOf(point.value).toFixed(1)} ${yOf(point.p).toFixed(1)}`)
    .join(" ");

  return (
    <figure className={className} style={{ margin: 0 }}>
      <figcaption className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">
        Theta-E (K)
      </figcaption>
      <svg
        data-testid="sounding-thetae"
        data-points={model.points.length}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Equivalent potential temperature against pressure"
        style={{ display: "block", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
      >
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={PLOT_W}
          height={PLOT_H}
          fill={SKEWT_COLORS.plotBackground}
          stroke={SKEWT_COLORS.frame}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />

        {THETAE_TICK_PRESSURES.map((p) => {
          const y = yOf(p);
          return (
            <Fragment key={`p-${p}`}>
              <line
                x1={MARGIN.left}
                y1={y}
                x2={MARGIN.left + PLOT_W}
                y2={y}
                stroke={SKEWT_COLORS.gridMinor}
                strokeWidth={0.5}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={MARGIN.left - 4}
                y={y + 3}
                fontSize={8}
                textAnchor="end"
                fill={SKEWT_COLORS.tickLabel}
              >
                {p}
              </text>
            </Fragment>
          );
        })}

        {model.xTicks.map((tick) => {
          const x = xOf(tick);
          if (x < MARGIN.left - 0.5 || x > MARGIN.left + PLOT_W + 0.5) return null;
          return (
            <Fragment key={`x-${tick}`}>
              <line
                x1={x}
                y1={MARGIN.top}
                x2={x}
                y2={MARGIN.top + PLOT_H}
                stroke={SKEWT_COLORS.gridMinor}
                strokeWidth={0.5}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={x}
                y={MARGIN.top + PLOT_H + 11}
                fontSize={8}
                textAnchor="middle"
                fill={SKEWT_COLORS.tickLabel}
              >
                {tick}
              </text>
            </Fragment>
          );
        })}

        <path
          data-testid="thetae-trace"
          d={d}
          fill="none"
          stroke={SKEWT_COLORS.thetaE}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </figure>
  );
}

export default ThetaEInset;
