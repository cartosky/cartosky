const API_ORIGIN_ENV = String(import.meta.env.VITE_API_BASE ?? "").trim();
export const API_ORIGIN = (API_ORIGIN_ENV || "https://api.cartosky.com").replace(/\/$/, "");
export const API_V4_BASE = `${API_ORIGIN}/api/v4`;

const TILES_BASE_ENV = String(import.meta.env.VITE_TILES_BASE ?? "").trim();
export const TILES_BASE = (TILES_BASE_ENV || API_ORIGIN).replace(/\/$/, "");

export const WEBP_RENDER_MODE_THRESHOLDS = {
  tier0Max: 5.8,
  tier1Max: 6.6,
  hysteresis: 0.2,
  dwellMs: 200,
  desktopHiDpiMinDpr: 1.75,
  desktopHiDpiTier1Bias: 0.35,
};

export const MAP_VIEW_DEFAULTS = {
  region: "conus",
  center: [39.83, -98.58] as [number, number],
  zoom: 4,
};

export const OVERLAY_DEFAULT_OPACITY = 0.9;

export type PlaybackBufferPolicy = {
  bufferTarget: number;
  minStartBuffer: number;
  minAheadWhilePlaying: number;
};

export type LoopPlaybackPolicy = {
  minStartBuffer: number;
  minAheadWhilePlaying: number;
  shortAheadTarget: number;
  targetWarmAhead: number;
  maxCriticalInFlight: number;
  maxIdleInFlight: number;
};

export function getPlaybackBufferPolicy(params: {
  totalFrames: number;
  autoplayTickMs: number;
}): PlaybackBufferPolicy {
  const totalFrames = Math.max(0, Number(params.totalFrames) || 0);
  const tickMs = Math.max(60, Number(params.autoplayTickMs) || 250);

  let bufferTarget = 12;
  if (totalFrames >= 85) {
    bufferTarget = 12;
  } else if (totalFrames >= 49) {
    bufferTarget = totalFrames >= 56 ? 16 : 14;
  } else if (totalFrames >= 30) {
    bufferTarget = 10;
  } else {
    bufferTarget = Math.max(6, Math.min(10, totalFrames));
  }

  const minStartBuffer = totalFrames >= 49 ? 3 : 2;

  let minAheadWhilePlaying = 5;
  if (tickMs <= 180) {
    minAheadWhilePlaying = 7;
  } else if (tickMs <= 250) {
    minAheadWhilePlaying = 6;
  } else if (tickMs >= 350) {
    minAheadWhilePlaying = 4;
  }

  return {
    bufferTarget: Math.max(minStartBuffer, Math.min(bufferTarget, totalFrames || bufferTarget)),
    minStartBuffer,
    minAheadWhilePlaying,
  };
}

export function getLoopPlaybackPolicy(params: {
  totalFrames: number;
  autoplayTickMs: number;
}): LoopPlaybackPolicy {
  const totalFrames = Math.max(0, Number(params.totalFrames) || 0);
  const tickMs = Math.max(60, Number(params.autoplayTickMs) || 250);
  const safeFrameCount = Math.max(1, totalFrames);

  let minStartBuffer = 4;
  if (totalFrames >= 72) {
    minStartBuffer = 5;
  } else if (totalFrames >= 18) {
    minStartBuffer = 4;
  } else if (totalFrames > 0) {
    minStartBuffer = Math.min(3, totalFrames);
  }

  let minAheadWhilePlaying = 4;
  if (tickMs <= 180) {
    minAheadWhilePlaying = 5;
  } else if (tickMs >= 350) {
    minAheadWhilePlaying = 3;
  }

  let targetWarmAhead = 8;
  if (totalFrames >= 72) {
    targetWarmAhead = 10;
  } else if (totalFrames >= 36) {
    targetWarmAhead = 8;
  } else if (totalFrames >= 18) {
    targetWarmAhead = 6;
  } else if (totalFrames > 0) {
    targetWarmAhead = Math.max(4, Math.min(6, totalFrames));
  }

  const maxCriticalInFlight = totalFrames >= 72 ? 6 : totalFrames >= 36 ? 5 : 4;
  const maxIdleInFlight = totalFrames >= 24 ? 2 : 1;

  const resolvedMinStartBuffer = Math.max(1, Math.min(minStartBuffer, safeFrameCount));
  const resolvedMinAheadWhilePlaying = Math.max(1, Math.min(minAheadWhilePlaying, safeFrameCount));
  const resolvedShortAheadTarget = Math.max(
    resolvedMinAheadWhilePlaying,
    Math.min(tickMs >= 350 ? 3 : 4, safeFrameCount),
  );
  const resolvedTargetWarmAhead = Math.max(
    resolvedShortAheadTarget,
    Math.min(targetWarmAhead, safeFrameCount),
  );

  return {
    minStartBuffer: resolvedMinStartBuffer,
    minAheadWhilePlaying: resolvedMinAheadWhilePlaying,
    shortAheadTarget: resolvedShortAheadTarget,
    targetWarmAhead: resolvedTargetWarmAhead,
    maxCriticalInFlight,
    maxIdleInFlight,
  };
}

export function isWebpDefaultRenderEnabled(): boolean {
  return readBooleanEnv(
    import.meta.env.VITE_CARTOSKY_WEBP_DEFAULT_ENABLED ?? import.meta.env.VITE_TWF_V3_WEBP_DEFAULT_ENABLED,
    true,
  );
}

function readBooleanEnv(value: unknown, fallback: boolean): boolean {
  const envValue = String(value ?? "").trim().toLowerCase();
  if (envValue === "1" || envValue === "true" || envValue === "yes" || envValue === "on") {
    return true;
  }
  if (envValue === "0" || envValue === "false" || envValue === "no" || envValue === "off") {
    return false;
  }
  return fallback;
}

export function isTileFirstInitialPaintEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_TILE_FIRST_INITIAL_PAINT, true);
}

export function isDeferredNonCriticalBootstrapEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_DEFER_NON_CRITICAL_BOOTSTRAP, true);
}

export function isDeferredPrefetchUntilFirstPaintEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_DEFER_PREFETCH_UNTIL_FIRST_PAINT, true);
}

export function isViewportAwareTileReadinessEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_VIEWPORT_AWARE_TILE_READINESS, false);
}

export function isAdminEmbedsEnabled(): boolean {
  return readBooleanEnv(import.meta.env.VITE_CARTOSKY_ADMIN_EMBEDS_ENABLED, false);
}
