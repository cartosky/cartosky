import type { LegendPayload } from "@/components/map-legend";

export const GRID_LUT_SIZE = 4096;

function hexToRgba(color: string): [number, number, number, number] {
  const normalized = color.trim().replace(/^#/, "");
  if (normalized.length !== 6 && normalized.length !== 8) {
    return [0, 0, 0, 0];
  }
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  const a = normalized.length === 8 ? Number.parseInt(normalized.slice(6, 8), 16) : 255;
  return [
    Number.isFinite(r) ? r : 0,
    Number.isFinite(g) ? g : 0,
    Number.isFinite(b) ? b : 0,
    Number.isFinite(a) ? a : 255,
  ];
}

function lerpColor(left: [number, number, number, number], right: [number, number, number, number], t: number) {
  return [
    Math.round(left[0] + (right[0] - left[0]) * t),
    Math.round(left[1] + (right[1] - left[1]) * t),
    Math.round(left[2] + (right[2] - left[2]) * t),
    Math.round(left[3] + (right[3] - left[3]) * t),
  ] as [number, number, number, number];
}

export function buildLegendLut(legend: LegendPayload | null, size = GRID_LUT_SIZE): { pixels: Uint8Array; min: number; max: number } {
  const normalizedKind = String(legend?.kind ?? "").trim().toLowerCase();
  const isCategorical = normalizedKind === "indexed" || normalizedKind === "categorical";
  const isDiscrete = normalizedKind === "discrete";
  const entries = Array.isArray(legend?.entries)
    ? legend.entries
      .map((entry) => ({ value: Number(entry.value), rgba: hexToRgba(entry.color) }))
      .filter((entry) => Number.isFinite(entry.value))
      .sort((left, right) => (isCategorical ? 0 : left.value - right.value))
    : [];

  const pixels = new Uint8Array(size * 4);
  if (entries.length === 0) {
    for (let index = 0; index < size; index += 1) {
      const offset = index * 4;
      pixels[offset] = 0;
      pixels[offset + 1] = 0;
      pixels[offset + 2] = 0;
      pixels[offset + 3] = 0;
    }
    return { pixels, min: 0, max: 1 };
  }

  if (isCategorical) {
    const maxIndex = Math.max(0, entries.length - 1);
    const denom = Math.max(1, size - 1);
    for (let index = 0; index < size; index += 1) {
      const paletteIndex = Math.min(maxIndex, Math.round((maxIndex * index) / denom));
      const rgba = entries[paletteIndex]?.rgba ?? [0, 0, 0, 0];
      const offset = index * 4;
      pixels[offset] = rgba[0];
      pixels[offset + 1] = rgba[1];
      pixels[offset + 2] = rgba[2];
      pixels[offset + 3] = rgba[3];
    }
    return { pixels, min: 0, max: maxIndex };
  }

  if (isDiscrete) {
    const min = entries[0].value;
    const max = entries[entries.length - 1].value;
    const denom = Math.max(1e-6, max - min);

    for (let index = 0; index < size; index += 1) {
      const value = min + (denom * index) / Math.max(1, size - 1);
      let selected = entries[0];
      for (let cursor = 0; cursor < entries.length; cursor += 1) {
        const current = entries[cursor];
        const next = entries[cursor + 1];
        selected = current;
        if (!next || value < next.value) {
          break;
        }
      }
      const offset = index * 4;
      pixels[offset] = selected.rgba[0];
      pixels[offset + 1] = selected.rgba[1];
      pixels[offset + 2] = selected.rgba[2];
      pixels[offset + 3] = selected.rgba[3];
    }

    return { pixels, min, max };
  }

  const min = entries[0].value;
  const max = entries[entries.length - 1].value;
  const denom = Math.max(1e-6, max - min);

  for (let index = 0; index < size; index += 1) {
    const value = min + (denom * index) / Math.max(1, size - 1);
    let left = entries[0];
    let right = entries[entries.length - 1];
    for (let cursor = 0; cursor < entries.length - 1; cursor += 1) {
      const current = entries[cursor];
      const next = entries[cursor + 1];
      if (value >= current.value && value <= next.value) {
        left = current;
        right = next;
        break;
      }
      if (value < entries[0].value) {
        left = entries[0];
        right = entries[0];
        break;
      }
    }
    const span = Math.max(1e-6, right.value - left.value);
    const t = right.value <= left.value ? 0 : (value - left.value) / span;
    const rgba = lerpColor(left.rgba, right.rgba, Math.max(0, Math.min(1, t)));
    const offset = index * 4;
    pixels[offset] = rgba[0];
    pixels[offset + 1] = rgba[1];
    pixels[offset + 2] = rgba[2];
    pixels[offset + 3] = rgba[3];
  }

  return { pixels, min, max };
}
