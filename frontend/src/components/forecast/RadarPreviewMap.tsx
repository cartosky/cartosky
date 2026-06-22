import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { GridManifestResponse } from "@/lib/api";
import type { LegendPayload } from "@/components/map-legend";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import { GridWebglLayerController } from "@/lib/grid-webgl";
import {
  buildRadarPreviewMapStyle,
  RADAR_PREVIEW_FRAME_UPDATE_TIMEOUT_MS,
  RADAR_PREVIEW_INITIAL_TIMEOUT_MS,
  RADAR_PREVIEW_LOOP_MS,
  RADAR_PREVIEW_ZOOM,
  type PreviewFrame,
} from "@/lib/radar-preview";

const PREVIEW_GRID_LAYER_ID = "radar-preview-grid-webgl";

export type RadarPreviewMapProps = {
  lat: number;
  lon: number;
  manifest: GridManifestResponse;
  legend: LegendPayload | null;
  loopFrames: PreviewFrame[];
  lodLevel: number | null;
  animationEnabled: boolean;
  isPaused: boolean;
  onReady?: () => void;
  onDegrade?: (reason: string) => void;
  onFatal?: (reason: string) => void;
  onFrameIndexChange?: (index: number) => void;
};

export function RadarPreviewMap({
  lat,
  lon,
  manifest,
  legend,
  loopFrames,
  lodLevel,
  animationEnabled,
  isPaused,
  onReady,
  onDegrade,
  onFatal,
  onFrameIndexChange,
}: RadarPreviewMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const controllerRef = useRef<GridWebglLayerController | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [frameIndex, setFrameIndex] = useState(0);

  const readyFiredRef = useRef(false);
  const degradedRef = useRef(false);
  const consecutiveTimeoutsRef = useRef(0);
  const frameTimeoutRef = useRef<number | null>(null);
  const initialTimeoutRef = useRef<number | null>(null);
  const lastPaintedIndexRef = useRef(0);
  const currentFrameUrlRef = useRef<string | null>(null);
  const currentFrameHourRef = useRef<number | null>(null);
  const frameIndexRef = useRef(0);

  const onReadyRef = useRef(onReady);
  const onDegradeRef = useRef(onDegrade);
  const onFatalRef = useRef(onFatal);
  const onFrameIndexChangeRef = useRef(onFrameIndexChange);
  onReadyRef.current = onReady;
  onDegradeRef.current = onDegrade;
  onFatalRef.current = onFatal;
  onFrameIndexChangeRef.current = onFrameIndexChange;

  const selectionKey = `${manifest.model}:${manifest.run}:${manifest.var}`;

  const clearFrameTimeout = useCallback(() => {
    if (frameTimeoutRef.current !== null) {
      window.clearTimeout(frameTimeoutRef.current);
      frameTimeoutRef.current = null;
    }
  }, []);

  const signalReady = useCallback(() => {
    if (readyFiredRef.current) {
      return;
    }
    readyFiredRef.current = true;
    if (initialTimeoutRef.current !== null) {
      window.clearTimeout(initialTimeoutRef.current);
      initialTimeoutRef.current = null;
    }
    onReadyRef.current?.();
  }, []);

  const handleFramePaintSuccess = useCallback((frameUrl: string, frameHour: number | null) => {
    if (frameUrl !== currentFrameUrlRef.current) {
      return;
    }
    if (frameHour !== null && currentFrameHourRef.current !== null && frameHour !== currentFrameHourRef.current) {
      return;
    }
    clearFrameTimeout();
    consecutiveTimeoutsRef.current = 0;
    lastPaintedIndexRef.current = frameIndexRef.current;
    onFrameIndexChangeRef.current?.(frameIndexRef.current);
    mapRef.current?.triggerRepaint();
    signalReady();
  }, [clearFrameTimeout, signalReady]);

  const startFrameTimeout = useCallback(() => {
    clearFrameTimeout();
    frameTimeoutRef.current = window.setTimeout(() => {
      consecutiveTimeoutsRef.current += 1;
      if (consecutiveTimeoutsRef.current >= 2 && !degradedRef.current) {
        degradedRef.current = true;
        onDegradeRef.current?.("frame_update_timeout");
        setFrameIndex(lastPaintedIndexRef.current);
      }
    }, RADAR_PREVIEW_FRAME_UPDATE_TIMEOUT_MS);
  }, [clearFrameTimeout]);

  const applyFrame = useCallback((index: number) => {
    const frame = loopFrames[index];
    const map = mapRef.current;
    const controller = controllerRef.current;
    if (!frame || !map || !controller || !mapLoaded) {
      return;
    }

    frameIndexRef.current = index;
    currentFrameUrlRef.current = frame.url;
    currentFrameHourRef.current = frame.hour;

    const prefetchUrls = loopFrames
      .map((entry) => entry.url)
      .filter((url, urlIndex, all) => all.indexOf(url) === urlIndex && url !== frame.url);

    controller.ensureAttached(map);
    controller.update({
      active: true,
      manifest,
      lodLevel,
      frameUrl: frame.url,
      frameHour: frame.hour,
      legend,
      opacity: OVERLAY_DEFAULT_OPACITY,
      selectionEpoch: 0,
      selectionKey,
      prefetchUrls,
      isAnimating: animationEnabled && !isPaused && loopFrames.length >= 2,
      onFrameReady: (url) => {
        handleFramePaintSuccess(url, frame.hour);
      },
      onFrameVisible: (payload) => {
        if (payload.frameHour === frame.hour) {
          handleFramePaintSuccess(frame.url, frame.hour);
        }
      },
      requestRepaint: () => {
        mapRef.current?.triggerRepaint();
      },
    });

    if (controller.isFrameAvailable(frame.url) === "texture") {
      handleFramePaintSuccess(frame.url, frame.hour);
    } else {
      startFrameTimeout();
    }

    map.triggerRepaint();
  }, [
    animationEnabled,
    handleFramePaintSuccess,
    isPaused,
    legend,
    lodLevel,
    loopFrames,
    manifest,
    mapLoaded,
    selectionKey,
    startFrameTimeout,
  ]);

  const loopFramesKey = loopFrames.map((frame) => `${frame.hour}:${frame.url}`).join("|");

  useEffect(() => {
    frameIndexRef.current = 0;
    setFrameIndex(0);
    readyFiredRef.current = false;
    degradedRef.current = false;
    consecutiveTimeoutsRef.current = 0;
    lastPaintedIndexRef.current = 0;
    currentFrameUrlRef.current = null;
    currentFrameHourRef.current = null;
  }, [lat, lon, manifest.run, manifest.var, loopFramesKey]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }

    const controller = new GridWebglLayerController(PREVIEW_GRID_LAYER_ID);
    controllerRef.current = controller;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildRadarPreviewMapStyle(),
      center: [lon, lat],
      zoom: RADAR_PREVIEW_ZOOM,
      minZoom: 3,
      maxZoom: 14,
      minPitch: 0,
      maxPitch: 0,
      pitchWithRotate: false,
      dragRotate: false,
      touchPitch: false,
      interactive: false,
      attributionControl: false,
      preserveDrawingBuffer: false,
    });

    map.dragPan.disable();
    map.scrollZoom.disable();
    map.doubleClickZoom.disable();
    map.boxZoom.disable();
    map.touchZoomRotate.disable();
    map.keyboard.disable();

    const handleMapError = (event: { error?: unknown }) => {
      const err = event?.error;
      const errName =
        typeof err === "object" && err !== null && "name" in err
          ? String((err as { name?: unknown }).name ?? "")
          : "";
      const errMessage =
        typeof err === "object" && err !== null && "message" in err
          ? String((err as { message?: unknown }).message ?? "")
          : "";
      if (errName === "AbortError" || errMessage === "AbortError") {
        return;
      }
      if (readyFiredRef.current) {
        degradedRef.current = true;
        onDegradeRef.current?.("map_error");
        setFrameIndex(lastPaintedIndexRef.current);
        return;
      }
      onFatalRef.current?.("map_error");
    };

    map.on("error", handleMapError as (event: { error?: unknown }) => void);
    map.on("load", () => {
      setMapLoaded(true);
    });

    mapRef.current = map;

    initialTimeoutRef.current = window.setTimeout(() => {
      if (!readyFiredRef.current) {
        onFatalRef.current?.("initial_timeout");
      }
    }, RADAR_PREVIEW_INITIAL_TIMEOUT_MS);

    const resizeObserver = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
        map.resize();
      })
      : null;
    resizeObserver?.observe(containerRef.current);

    return () => {
      clearFrameTimeout();
      if (initialTimeoutRef.current !== null) {
        window.clearTimeout(initialTimeoutRef.current);
        initialTimeoutRef.current = null;
      }
      resizeObserver?.disconnect();
      controller.remove(map);
      map.remove();
      mapRef.current = null;
      controllerRef.current = null;
      setMapLoaded(false);
    };
  }, [clearFrameTimeout, lat, lon]);

  useEffect(() => {
    if (mapLoaded) {
      mapRef.current?.setCenter([lon, lat]);
    }
  }, [lat, lon, mapLoaded]);

  useEffect(() => {
    applyFrame(frameIndex);
  }, [applyFrame, frameIndex]);

  useEffect(() => {
    if (!animationEnabled || isPaused || degradedRef.current || loopFrames.length < 2) {
      return;
    }
    const intervalId = window.setInterval(() => {
      setFrameIndex((current) => (current + 1) % loopFrames.length);
    }, RADAR_PREVIEW_LOOP_MS);
    return () => window.clearInterval(intervalId);
  }, [animationEnabled, isPaused, loopFrames.length]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full pointer-events-none"
      aria-hidden
    />
  );
}
