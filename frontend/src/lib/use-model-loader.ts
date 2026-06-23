import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchFrames,
  fetchGridManifest,
  fetchManifest,
  fetchRuns,
  readCapabilityRenderSubstrates,
  type CapabilitiesResponse,
  type FrameRow,
  type GridManifestFrame,
  type GridManifestResponse,
  type RegionPreset,
  type RunManifestResponse,
} from "@/lib/api";
import {
  makeVariableOptions,
  mergeManifestRowsWithPrevious,
  normalizeCapabilityVarRows,
  resolveManifestFrames,
  selectableFramesForVariable,
  type VariableOption,
} from "@/lib/app-utils";
import { selectGridManifestLod } from "@/lib/grid-lod";
import { pickLatestRunId, sortRunIdsDescending } from "@/lib/run-options";

function hasSameJsonContent(left: unknown, right: unknown): boolean {
  if (left === right) {
    return true;
  }
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function preserveReferenceIfEqual<T>(previous: T, next: T): T {
  return hasSameJsonContent(previous, next) ? previous : next;
}

export interface UseModelLoaderParams {
  model: string;
  /** "latest" or a specific run ID. */
  run: string;
  variable: string;
  region: string;
  ensembleView?: string;
  capabilities: CapabilitiesResponse;
  regionPresets: Record<string, RegionPreset>;
}

export interface UseModelLoaderResult {
  runs: string[];
  variables: VariableOption[];
  runManifest: RunManifestResponse | null;
  gridManifest: GridManifestResponse | null;
  frameRows: FrameRow[];
  frameHours: number[];
  selectableFrameHours: number[];
  gridFrameHours: number[];
  gridFrameByHour: Map<number, GridManifestFrame>;
  resolvedRun: string;
  selectedVariableDefaultFh: number | null;
  prefersGridSubstrate: boolean;
  loading: boolean;
  error: string | null;
}

/**
 * Data-only loader for a single model/run/variable/region selection.
 *
 * This is a deliberately narrowed re-implementation of the viewer's selection
 * pipeline (App.tsx): it loads runs, the run manifest, the grid manifest, and
 * the frame rows for one grid-backed selection. It intentionally excludes all
 * of the viewer's presentation concerns — map/WebGL refs, active-frame URL
 * derivation, playback, permalink/URL state, display settings, anchor labels,
 * share payloads, raster_rgb/true_color handling.
 */
export function useModelLoader(params: UseModelLoaderParams): UseModelLoaderResult {
  const { model, run, variable, region, ensembleView, capabilities } = params;
  const resolvedEnsembleView = ensembleView ?? "";

  const [runs, setRuns] = useState<string[]>([]);
  const [runManifest, setRunManifest] = useState<RunManifestResponse | null>(null);
  const [gridManifest, setGridManifest] = useState<GridManifestResponse | null>(null);
  const [frameRows, setFrameRows] = useState<FrameRow[]>([]);
  const [resolvedGridLatestRunId, setResolvedGridLatestRunId] = useState<string | null>(null);

  const [runsLoading, setRunsLoading] = useState(true);
  const [manifestLoading, setManifestLoading] = useState(true);
  const [gridLoading, setGridLoading] = useState(true);
  const [framesLoading, setFramesLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Tracks the selection that produced the currently loaded frame rows, so the
  // manifest-hydration path can decide whether to carry forward prior metadata.
  // Kept as a ref (not state) so updating it never re-triggers the frame effect.
  const loadedFramesKeyRef = useRef<string>("");

  // ── Capability-derived selection facts (pure) ──────────────────────────
  const selectedModelCapability = useMemo(
    () => (model ? capabilities.model_catalog?.[model] ?? null : null),
    [capabilities, model],
  );

  const capabilityVars = useMemo(
    () => normalizeCapabilityVarRows(selectedModelCapability),
    [selectedModelCapability],
  );

  const capabilityVarMap = useMemo(
    () => new Map(capabilityVars.map((entry) => [entry.id, entry])),
    [capabilityVars],
  );

  // HARD REQUIREMENT: only expose variables whose render substrates include
  // "grid", using readCapabilityRenderSubstrates as the source of truth.
  const gridCapabilityVars = useMemo(
    () =>
      capabilityVars.filter((entry) =>
        readCapabilityRenderSubstrates(selectedModelCapability?.variables?.[entry.id]).includes("grid"),
      ),
    [capabilityVars, selectedModelCapability],
  );

  const variables = useMemo(
    () => makeVariableOptions(gridCapabilityVars, model),
    [gridCapabilityVars, model],
  );

  const selectedVariableDefaultFh = capabilityVarMap.get(variable)?.defaultFh ?? null;

  const selectedVariableRenderSubstrates = useMemo(() => {
    if (!variable) {
      return [] as ReturnType<typeof readCapabilityRenderSubstrates>;
    }
    return readCapabilityRenderSubstrates(selectedModelCapability?.variables?.[variable]);
  }, [selectedModelCapability, variable]);
  const prefersGridSubstrate = selectedVariableRenderSubstrates.includes("grid");

  const manifestVarIds = useMemo(() => {
    const vars = runManifest?.variables;
    return vars ? new Set(Object.keys(vars)) : new Set<string>();
  }, [runManifest]);

  const hasRenderableSelection = Boolean(
    model && variable && (capabilityVarMap.has(variable) || manifestVarIds.has(variable)),
  );

  // ── Run resolution ─────────────────────────────────────────────────────
  const latestRunId = useMemo(() => {
    const runsLatest = pickLatestRunId(runs);
    const availabilityLatest =
      model && capabilities.availability?.[model] ? capabilities.availability[model].latest_run ?? null : null;
    return runsLatest ?? availabilityLatest ?? null;
  }, [runs, model, capabilities]);

  const latestGridRunCandidates = useMemo(() => {
    if (!prefersGridSubstrate || run !== "latest") {
      return [] as string[];
    }
    // Only probe runs still present in the retained list — a stale latestRunId
    // can point at a pruned run and would otherwise trigger 404s.
    const retained = new Set(runs);
    return Array.from(
      new Set(
        [latestRunId, ...runs].filter((value): value is string => Boolean(value)).filter((value) => retained.has(value)),
      ),
    );
  }, [prefersGridSubstrate, latestRunId, run, runs]);

  const resolvedRun = useMemo(() => {
    // Never resolve "latest" to a client-side run id that is no longer retained
    // — fall back to the "latest" sentinel and let the server resolve it.
    const retainedOrLatest = (candidate: string | null) =>
      candidate && runs.includes(candidate) ? candidate : "latest";
    if (prefersGridSubstrate && run === "latest") {
      return retainedOrLatest(resolvedGridLatestRunId ?? latestRunId);
    }
    return run === "latest" ? retainedOrLatest(latestRunId) : run;
  }, [prefersGridSubstrate, run, runs, resolvedGridLatestRunId, latestRunId]);

  const selectionRunKey =
    prefersGridSubstrate && run === "latest"
      ? resolvedGridLatestRunId ?? "pending-grid"
      : run === "latest"
        ? "latest"
        : resolvedRun;
  const selectionKey = `${model}:${selectionRunKey}:${variable}:${region}:${resolvedEnsembleView || "-"}`;

  // ── Frame-hour projections (pure) ──────────────────────────────────────
  const frameHours = useMemo(() => {
    const hours = frameRows.map((row) => Number(row.fh)).filter(Number.isFinite);
    return Array.from(new Set(hours)).sort((a, b) => a - b);
  }, [frameRows]);

  const selectableFrameHours = useMemo(
    () => selectableFramesForVariable(frameHours, selectedVariableDefaultFh),
    [frameHours, selectedVariableDefaultFh],
  );

  // Grid frames come from the base (level-zero) LOD; this hook has no map zoom.
  const gridFrameByHour = useMemo(() => {
    const map = new Map<number, GridManifestFrame>();
    const lod = selectGridManifestLod(gridManifest, null);
    const frames = Array.isArray(lod?.frames) ? lod.frames : [];
    for (const frame of frames) {
      const fh = Number(frame?.fh);
      if (Number.isFinite(fh)) {
        map.set(fh, frame);
      }
    }
    return map;
  }, [gridManifest]);

  const gridFrameHours = useMemo(
    () => Array.from(gridFrameByHour.keys()).sort((a, b) => a - b),
    [gridFrameByHour],
  );

  // ── Effects ────────────────────────────────────────────────────────────

  // Reset run/manifest/frame state when the model changes so stale data from a
  // previous model can't leak into the next selection's request resolution.
  useEffect(() => {
    setRuns((prevRuns) => (prevRuns.length === 0 ? prevRuns : []));
    setRunManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
    setGridManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
    setFrameRows((prevRows) => (prevRows.length === 0 ? prevRows : []));
    setResolvedGridLatestRunId(null);
    loadedFramesKeyRef.current = "";
  }, [model]);

  // Drop the resolved grid run whenever the selection stops being grid-latest.
  useEffect(() => {
    if (!prefersGridSubstrate || run !== "latest") {
      setResolvedGridLatestRunId(null);
    }
  }, [prefersGridSubstrate, model, run, variable, resolvedEnsembleView]);

  // Load the available runs for the model.
  useEffect(() => {
    if (!model) {
      setRunsLoading(false);
      return;
    }
    const controller = new AbortController();
    setRunsLoading(true);
    fetchRuns(model, { signal: controller.signal })
      .then((raw) => {
        if (controller.signal.aborted) {
          return;
        }
        const sortedRuns = sortRunIdsDescending(raw);
        setRuns((prevRuns) => preserveReferenceIfEqual(prevRuns, sortedRuns));
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load runs");
        setRuns((prevRuns) => (prevRuns.length === 0 ? prevRuns : []));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setRunsLoading(false);
        }
      });
    return () => controller.abort();
  }, [model]);

  const manifestRunKey = useMemo(() => {
    if (run !== "latest") {
      return run;
    }
    return prefersGridSubstrate && resolvedGridLatestRunId && runs.includes(resolvedGridLatestRunId)
      ? resolvedGridLatestRunId
      : "latest";
  }, [run, prefersGridSubstrate, resolvedGridLatestRunId, runs]);

  // Load the run manifest. Re-runs once the grid probe resolves a concrete run
  // so the manifest is refetched against the run actually being rendered.
  useEffect(() => {
    if (!model) {
      setManifestLoading(false);
      return;
    }
    const controller = new AbortController();
    setManifestLoading(true);
    fetchManifest(model, manifestRunKey, region, resolvedEnsembleView, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) {
          return;
        }
        setRunManifest((prevManifest) => preserveReferenceIfEqual(prevManifest, data));
      })
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        setRunManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setManifestLoading(false);
        }
      });
    return () => controller.abort();
  }, [model, manifestRunKey, region, resolvedEnsembleView]);

  // Resolve and load the grid manifest. For a grid-only "latest" selection,
  // probe candidate runs in parallel and adopt the first that returns a valid
  // manifest (App.tsx run-probe pattern).
  useEffect(() => {
    if (!prefersGridSubstrate || !hasRenderableSelection) {
      setGridManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
      setGridLoading(false);
      return;
    }

    const controller = new AbortController();
    setGridLoading(true);
    setGridManifest((prevManifest) => (prevManifest === null ? prevManifest : null));

    const resolveManifest = async () => {
      if (run === "latest") {
        const results = await Promise.allSettled(
          latestGridRunCandidates.map((candidateRun) =>
            fetchGridManifest(model, candidateRun, variable, region, resolvedEnsembleView, {
              signal: controller.signal,
            }).then((manifest) => ({ candidateRun, manifest })),
          ),
        );
        if (controller.signal.aborted) {
          return;
        }
        for (const result of results) {
          if (result.status === "fulfilled" && result.value.manifest) {
            setResolvedGridLatestRunId(result.value.candidateRun);
            setGridManifest((prevManifest) => preserveReferenceIfEqual(prevManifest, result.value.manifest));
            return;
          }
        }
        setResolvedGridLatestRunId(null);
        setGridManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
        return;
      }

      const manifest = await fetchGridManifest(model, resolvedRun, variable, region, resolvedEnsembleView, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        return;
      }
      setGridManifest((prevManifest) => preserveReferenceIfEqual(prevManifest, manifest));
    };

    void resolveManifest()
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        if (run === "latest") {
          setResolvedGridLatestRunId(null);
        }
        setGridManifest((prevManifest) => (prevManifest === null ? prevManifest : null));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setGridLoading(false);
        }
      });

    return () => controller.abort();
  }, [
    prefersGridSubstrate,
    hasRenderableSelection,
    latestGridRunCandidates,
    model,
    region,
    resolvedRun,
    run,
    variable,
    resolvedEnsembleView,
  ]);

  // Clear frame rows when the effective selection changes.
  useEffect(() => {
    setFrameRows((prevRows) => (prevRows.length === 0 ? prevRows : []));
    loadedFramesKeyRef.current = "";
  }, [selectionKey]);

  // Load frame rows. Hydrate the slider from the manifest's expected hours
  // first (for continuity), then merge the COG-ready hours from fetchFrames.
  useEffect(() => {
    if (!model || !variable || !hasRenderableSelection) {
      setFramesLoading(false);
      return;
    }
    // Grid-only "latest" can't fetch frames until the run is resolved.
    if (prefersGridSubstrate && run === "latest" && !resolvedGridLatestRunId) {
      setFrameRows((prevRows) => (prevRows.length === 0 ? prevRows : []));
      loadedFramesKeyRef.current = "";
      setFramesLoading(false);
      return;
    }

    const controller = new AbortController();
    setFramesLoading(true);

    const loadFrames = async () => {
      setError(null);
      let hydratedFromManifest = false;

      const manifestMatchesSelection =
        Boolean(runManifest) &&
        runManifest?.model === model &&
        (run === "latest" || runManifest?.run === run || runManifest?.run === resolvedRun);
      const manifestFrameList = manifestMatchesSelection
        ? resolveManifestFrames(runManifest, variable)
        : { rows: [] as FrameRow[], hasFrameList: false };

      if (manifestMatchesSelection && manifestFrameList.hasFrameList) {
        const { rows } = manifestFrameList;
        const allowCarryForward = loadedFramesKeyRef.current === selectionKey;
        setFrameRows((prevRows) =>
          preserveReferenceIfEqual(prevRows, mergeManifestRowsWithPrevious(rows, prevRows, allowCarryForward)),
        );
        loadedFramesKeyRef.current = selectionKey;
        hydratedFromManifest = true;
      }

      try {
        const framesRunKey =
          prefersGridSubstrate && run === "latest"
            ? resolvedGridLatestRunId
            : run === "latest"
              ? "latest"
              : resolvedRun;
        if (!framesRunKey) {
          return;
        }
        const rows = await fetchFrames(model, framesRunKey, variable, region, resolvedEnsembleView, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) {
          return;
        }
        // Merge rather than hard-replace: manifest hydration may have populated
        // expected-but-not-yet-ready hours that fetchFrames omits. A hard
        // replace would contract the slider on still-populating runs.
        setFrameRows((prevRows) => {
          if (prevRows.length === 0) {
            return preserveReferenceIfEqual(prevRows, rows);
          }
          const merged = new Map<number, FrameRow>();
          for (const row of prevRows) {
            const fh = Number(row.fh);
            if (Number.isFinite(fh)) {
              merged.set(fh, row);
            }
          }
          for (const row of rows) {
            const fh = Number(row.fh);
            if (Number.isFinite(fh)) {
              merged.set(fh, row);
            }
          }
          return preserveReferenceIfEqual(
            prevRows,
            Array.from(merged.values()).sort((a, b) => Number(a.fh) - Number(b.fh)),
          );
        });
        loadedFramesKeyRef.current = selectionKey;
      } catch (err) {
        if (controller.signal.aborted) {
          return;
        }
        if (!hydratedFromManifest) {
          loadedFramesKeyRef.current = "";
          setError(err instanceof Error ? err.message : "Failed to load frames");
          setFrameRows((prevRows) => (prevRows.length === 0 ? prevRows : []));
        }
      }
    };

    void loadFrames().finally(() => {
      if (!controller.signal.aborted) {
        setFramesLoading(false);
      }
    });

    return () => controller.abort();
  }, [
    model,
    variable,
    run,
    region,
    resolvedEnsembleView,
    hasRenderableSelection,
    prefersGridSubstrate,
    resolvedGridLatestRunId,
    resolvedRun,
    runManifest,
    selectionKey,
  ]);

  const loading = runsLoading || manifestLoading || framesLoading || (prefersGridSubstrate && gridLoading);

  return {
    runs,
    variables,
    runManifest,
    gridManifest,
    frameRows,
    frameHours,
    selectableFrameHours,
    gridFrameHours,
    gridFrameByHour,
    resolvedRun,
    selectedVariableDefaultFh,
    prefersGridSubstrate,
    loading,
    error,
  };
}
