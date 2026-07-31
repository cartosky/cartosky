import { Fragment, useMemo } from "react";

import {
  buildHodograph,
  HODOGRAPH_RING_STEP_KT,
  type HodographInput,
} from "@/lib/sounding-insets";
import { SKEWT_COLORS } from "@/lib/skewt-geometry";

/**
 * Height-coloured hodograph (Skew-T design §7 Phase 5, TT reference).
 *
 * Rings every 10 kt (labelled every 20), the u/v trace coloured by height band
 * with the colour changes interpolated onto the exact 1 / 3 / 6 / 9 km
 * boundaries, km labels on the trace and a dot at the surface wind.
 *
 * All of the arithmetic lives in `lib/sounding-insets.ts`; this file is
 * projection + SVG only. When the server sent no `profiles` (a pre-Phase-5
 * response) `buildHodograph` returns null and the caller renders nothing —
 * a hodograph without heights would be a plain uncoloured squiggle.
 */

const SIZE = 210;
/** Room for the outermost ring label and the km labels on the trace. */
const PAD = 15;
const RADIUS = (SIZE - PAD * 2) / 2;
const CENTER = SIZE / 2;

export type HodographProps = HodographInput & { className?: string };

export function Hodograph({ u, v, heightM, surfaceU, surfaceV, className }: HodographProps) {
  // Memoised on the arrays themselves, not on a spread props object: the frame
  // arrays are stable per forecast hour, a rebuilt object never would be.
  const model = useMemo(
    () => buildHodograph({ u, v, heightM, surfaceU, surfaceV }),
    [u, v, heightM, surfaceU, surfaceV],
  );

  if (!model) return null;

  const scale = RADIUS / model.ringMaxKt;
  const px = (u: number) => CENTER + u * scale;
  const py = (v: number) => CENTER - v * scale;

  const rings: number[] = [];
  for (let r = HODOGRAPH_RING_STEP_KT; r <= model.ringMaxKt + 1e-9; r += HODOGRAPH_RING_STEP_KT) {
    rings.push(r);
  }

  return (
    <figure className={className} style={{ margin: 0 }}>
      <figcaption className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">
        Hodograph (kt)
      </figcaption>
      <svg
        data-testid="sounding-hodograph"
        data-ring-max-kt={model.ringMaxKt}
        data-segments={model.segments.length}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Hodograph of wind with height"
        style={{ display: "block", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
      >
        <rect
          x={0}
          y={0}
          width={SIZE}
          height={SIZE}
          rx={6}
          fill={SKEWT_COLORS.plotBackground}
          stroke={SKEWT_COLORS.frame}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />

        {rings.map((r) => {
          const labelled = r % (HODOGRAPH_RING_STEP_KT * 2) === 0;
          return (
            <Fragment key={`ring-${r}`}>
              <circle
                cx={CENTER}
                cy={CENTER}
                r={r * scale}
                fill="none"
                stroke={labelled ? SKEWT_COLORS.gridMinor : SKEWT_COLORS.isotherm}
                strokeWidth={labelled ? 0.8 : 0.5}
                vectorEffect="non-scaling-stroke"
              />
              {labelled ? (
                <text
                  x={CENTER + r * scale - 1}
                  y={CENTER - 2.5}
                  fontSize={7}
                  textAnchor="end"
                  fill={SKEWT_COLORS.axisLabel}
                >
                  {r}
                </text>
              ) : null}
            </Fragment>
          );
        })}

        <line
          x1={PAD}
          y1={CENTER}
          x2={SIZE - PAD}
          y2={CENTER}
          stroke={SKEWT_COLORS.gridMinor}
          strokeWidth={0.6}
          vectorEffect="non-scaling-stroke"
        />
        <line
          x1={CENTER}
          y1={PAD}
          x2={CENTER}
          y2={SIZE - PAD}
          stroke={SKEWT_COLORS.gridMinor}
          strokeWidth={0.6}
          vectorEffect="non-scaling-stroke"
        />

        {model.segments.map((segment, index) => (
          <polyline
            key={`seg-${segment.band}-${index}`}
            data-testid="hodograph-segment"
            data-band={segment.band}
            points={segment.points.map((point) => `${px(point.u).toFixed(1)},${py(point.v).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={segment.color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {model.ticks.map((tick) => (
          <text
            key={`tick-${tick.km}`}
            data-testid={`hodograph-tick-${tick.km}`}
            x={px(tick.u) + 4}
            y={py(tick.v) - 3}
            fontSize={8}
            fontWeight={600}
            fill={SKEWT_COLORS.tickLabel}
            stroke={SKEWT_COLORS.plotBackground}
            strokeWidth={2.2}
            paintOrder="stroke"
          >
            {tick.km}
          </text>
        ))}

        {model.surface ? (
          <circle
            data-testid="hodograph-surface"
            cx={px(model.surface.u)}
            cy={py(model.surface.v)}
            r={2.8}
            fill={SKEWT_COLORS.tickLabel}
            stroke={SKEWT_COLORS.plotBackground}
            strokeWidth={1}
          />
        ) : null}
      </svg>
    </figure>
  );
}

export default Hodograph;
