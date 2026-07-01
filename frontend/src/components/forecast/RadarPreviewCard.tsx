import { lazy, Suspense, useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import {
  formatRadarFrameAge,
  isRadarPreviewAvailable,
  RADAR_PREVIEW_MAP_HEIGHT_CLASS,
  viewerRadarHref,
} from "@/lib/radar-preview";
import { useRadarPreview } from "@/lib/use-radar-preview";

const RadarPreviewMap = lazy(() =>
  import("./RadarPreviewMap").then((module) => ({ default: module.RadarPreviewMap })),
);

type CardRenderMode = "loading" | "animated" | "static" | "hidden";

export type RadarPreviewCardProps = {
  lat: number;
  lon: number;
  className?: string;
  mapHeightClassName?: string;
};

export function RadarPreviewCard({
  lat,
  lon,
  className = "",
  mapHeightClassName = RADAR_PREVIEW_MAP_HEIGHT_CLASS,
}: RadarPreviewCardProps) {
  const navigate = useNavigate();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [isInView, setIsInView] = useState(true);
  const [isDocumentVisible, setIsDocumentVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );
  const [renderMode, setRenderMode] = useState<CardRenderMode>("loading");
  const [mapPaintPending, setMapPaintPending] = useState(true);
  const [displayFrameIndex, setDisplayFrameIndex] = useState(0);

  const available = isRadarPreviewAvailable(lat, lon);
  const { manifest, legend, loopFrames, lodLevel, loading, error, supportsAnimation } = useRadarPreview(
    lat,
    lon,
    available,
  );

  const isPaused = !isInView || !isDocumentVisible;

  useEffect(() => {
    setMapPaintPending(true);
    setDisplayFrameIndex(0);
  }, [lat, lon]);

  useEffect(() => {
    if (loopFrames.length === 0) {
      return;
    }
    setDisplayFrameIndex((current) => {
      if (current >= loopFrames.length) {
        return loopFrames.length - 1;
      }
      return current;
    });
  }, [loopFrames]);

  useEffect(() => {
    if (!available) {
      setRenderMode("hidden");
      return;
    }
    if (loading) {
      setRenderMode("loading");
      return;
    }
    if (error || !manifest || !legend || loopFrames.length === 0) {
      setRenderMode("hidden");
      return;
    }
    setRenderMode(supportsAnimation ? "animated" : "static");
  }, [available, error, legend, loading, loopFrames.length, manifest, supportsAnimation]);

  useEffect(() => {
    const node = rootRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        setIsInView(Boolean(entry?.isIntersecting));
      },
      { threshold: 0.1 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState === "visible");
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  const handleReady = useCallback(() => {
    setMapPaintPending(false);
    if (renderMode === "loading") {
      setRenderMode(supportsAnimation ? "animated" : "static");
    }
  }, [renderMode, supportsAnimation]);

  const handleDegrade = useCallback(() => {
    setRenderMode("static");
  }, []);

  const handleFatal = useCallback(() => {
    setRenderMode("hidden");
  }, []);

  const handleFrameIndexChange = useCallback((index: number) => {
    setDisplayFrameIndex(index);
  }, []);

  const handleOpenViewer = useCallback(() => {
    navigate(viewerRadarHref(lat, lon));
  }, [lat, lon, navigate]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleOpenViewer();
      }
    },
    [handleOpenViewer],
  );

  if (!available || renderMode === "hidden") {
    return null;
  }

  const currentFrame = loopFrames[displayFrameIndex] ?? loopFrames[loopFrames.length - 1] ?? null;
  const frameAgeLabel = formatRadarFrameAge(currentFrame?.validTime);
  const animationEnabled = renderMode === "animated";
  const canRenderMap = Boolean(manifest && legend && loopFrames.length > 0);
  const showSkeleton = loading || (mapPaintPending && canRenderMap);

  return (
    <div
      ref={rootRef}
      role="button"
      tabIndex={0}
      onClick={handleOpenViewer}
      onKeyDown={handleKeyDown}
      className={`flex h-full flex-col overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03] cursor-pointer transition hover:border-cyan-300/30 hover:bg-white/[0.05] focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/40 ${className}`}
      aria-label="Open live MRMS radar in the viewer"
    >
      <div className={`relative w-full flex-1 ${mapHeightClassName}`}>
        {showSkeleton ? (
          <div className="absolute inset-0 animate-pulse bg-white/[0.04]" />
        ) : null}
        {canRenderMap && manifest && legend ? (
          <Suspense fallback={<div className="absolute inset-0 animate-pulse bg-white/[0.04]" />}>
            <RadarPreviewMap
              lat={lat}
              lon={lon}
              manifest={manifest}
              legend={legend}
              loopFrames={loopFrames}
              lodLevel={lodLevel}
              animationEnabled={animationEnabled}
              isPaused={isPaused}
              onReady={handleReady}
              onDegrade={handleDegrade}
              onFatal={handleFatal}
              onFrameIndexChange={handleFrameIndexChange}
            />
          </Suspense>
        ) : null}
        <div
          className="pointer-events-none absolute left-1/2 top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan-300 ring-2 ring-white/80"
          aria-hidden
        />
      </div>
      <div className="flex items-center justify-between border-t border-white/[0.06] px-3 py-2 text-[11px]">
        <span className="text-cyan-200/80">View Full Radar</span>
        <span className="flex items-center gap-1 text-white/45">
          {frameAgeLabel || "MRMS"}
          <ArrowRight className="h-3 w-3" />
        </span>
      </div>
    </div>
  );
}
