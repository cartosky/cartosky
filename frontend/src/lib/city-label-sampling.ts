import type { CityLabelPoint } from "@/lib/city-labels";

export type CityFrameSamplingPayload = {
  frameHour: number;
  selectionEpoch?: number;
  selectionKey?: string;
  gridSampled: boolean;
  points: CityLabelPoint[];
  values: Record<string, number | null>;
  units: string;
};

type CityGridSample = {
  values: Record<string, number | null>;
  units: string;
} | null;

export type CityFrameSamplingOutcome =
  | { kind: "direct"; values: Record<string, number | null>; units: string }
  | { kind: "fallback"; payload: CityFrameSamplingPayload };

export function resolveCityFrameSamplingOutcome(params: {
  frameHour: number;
  selectionEpoch?: number;
  selectionKey?: string;
  points: CityLabelPoint[];
  sampled: CityGridSample;
}): CityFrameSamplingOutcome {
  if (params.sampled) {
    return {
      kind: "direct",
      values: params.sampled.values,
      units: params.sampled.units,
    };
  }

  return {
    kind: "fallback",
    payload: {
      frameHour: params.frameHour,
      selectionEpoch: params.selectionEpoch,
      selectionKey: params.selectionKey,
      gridSampled: false,
      points: params.points,
      values: {},
      units: "",
    },
  };
}
