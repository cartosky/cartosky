import type {
  FrameRow,
  GridManifestResponse,
  LegendMeta,
} from "@/lib/api";

const DEFAULT_API_V4_BASE = "https://api.cartosky.com/api/v4";

type ResolveGridContourGeoJsonUrlParams = {
  model: string | null | undefined;
  run: string | null | undefined;
  variable: string | null | undefined;
  hour: number | null | undefined;
  gridManifest: GridManifestResponse | null | undefined;
  frameRows: FrameRow[];
  apiBase?: string;
};

function extractContourMeta(row: FrameRow | null | undefined): LegendMeta | null {
  const rawMeta = row?.meta?.meta ?? null;
  if (!rawMeta) {
    return null;
  }
  const nested = (rawMeta as { meta?: LegendMeta | null }).meta;
  return nested ?? (rawMeta as LegendMeta);
}

export function resolveGridContourGeoJsonUrl({
  model,
  run,
  variable,
  hour,
  gridManifest,
  frameRows,
  apiBase = DEFAULT_API_V4_BASE,
}: ResolveGridContourGeoJsonUrlParams): string | null {
  const resolvedModel = String(model ?? "").trim();
  const resolvedRun = String(run ?? "").trim();
  const resolvedVariable = String(variable ?? "").trim();
  if (!resolvedModel || !resolvedRun || !resolvedVariable || !Number.isFinite(hour)) {
    return null;
  }

  const frame = frameRows.find((row) => Number(row.fh) === Number(hour)) ?? null;
  const frameMeta = extractContourMeta(frame) ?? extractContourMeta(frameRows[0] ?? null);
  const contours = gridManifest?.contours ?? frameMeta?.contours;
  if (!contours || typeof contours !== "object") {
    return null;
  }

  const contourKey = Object.keys(contours)[0];
  if (!contourKey) {
    return null;
  }

  const enc = encodeURIComponent;
  const resolvedApiBase = String(apiBase || DEFAULT_API_V4_BASE).replace(/\/$/, "");
  return `${resolvedApiBase}/${enc(resolvedModel)}/${enc(resolvedRun)}/${enc(resolvedVariable)}/${enc(Number(hour))}/contours/${enc(contourKey)}`;
}
