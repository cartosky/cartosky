import { useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────

type ClimateIndexWidgetProps = {
  title: string;          // e.g. "Arctic Oscillation (AO)"
  source: string;         // e.g. "CPC · GEFS Ensemble"
  cadence: string;        // e.g. "Daily" | "Weekly · Thu" | "Monthly"
  proxyUrl: string;       // full URL: `/api/v4/climate/image-proxy?url=<encoded>`
  sourceUrl: string;      // original source URL for the "open source" link
  aspectRatio?: "landscape" | "square" | "tall"; // default "landscape" = 16/9
  fetchedAt?: string | null; // ISO timestamp from the proxy, shown as freshness
};

const ASPECT_CLASSES: Record<NonNullable<ClimateIndexWidgetProps["aspectRatio"]>, string> = {
  landscape: "aspect-video",
  square: "aspect-square",
  tall: "aspect-[3/2]",
};

// ── Freshness pip ─────────────────────────────────────────────────────

function computeFreshness(
  fetchedAt: string | null | undefined,
): { dotColor: string; label: string } | null {
  if (fetchedAt === undefined) return null; // prop omitted → don't show pip
  if (!fetchedAt) return { dotColor: "bg-red-500", label: "Unknown" };
  const d = new Date(fetchedAt);
  if (isNaN(d.getTime())) return { dotColor: "bg-red-500", label: "Unknown" };
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const fetchedStart = new Date(d);
  fetchedStart.setHours(0, 0, 0, 0);
  const diffDays = Math.round(
    (todayStart.getTime() - fetchedStart.getTime()) / 86400000,
  );
  if (diffDays === 0) return { dotColor: "bg-emerald-400", label: "Today" };
  if (diffDays <= 6) return { dotColor: "bg-amber-400", label: `${diffDays}d ago` };
  return { dotColor: "bg-red-500", label: `${diffDays}d ago` };
}

// ── Component ─────────────────────────────────────────────────────────

export default function ClimateIndexWidget({
  title,
  source,
  cadence,
  proxyUrl,
  sourceUrl,
  aspectRatio = "landscape",
  fetchedAt,
}: ClimateIndexWidgetProps) {
  const [loaded, setLoaded] = useState(false);
  const [hasError, setHasError] = useState(false);
  const aspectClass = ASPECT_CLASSES[aspectRatio];
  const freshness = computeFreshness(fetchedAt);

  function openSource() {
    window.location.href = sourceUrl;
  }

  function handleExpandClick(e: React.MouseEvent) {
    e.stopPropagation();
    window.location.href = sourceUrl;
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openSource();
    }
  }

  return (
    <div
      className="group relative flex flex-col overflow-hidden rounded-xl border border-white/10 bg-[#07111f] cursor-pointer transition-colors hover:border-white/20 hover:bg-white/[0.025]"
      onClick={openSource}
      role="link"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label={`${title} — view source`}
    >
      {/* Image area */}
      <div className={`relative w-full ${aspectClass} bg-white/[0.04]`}>
        {/* Shimmer while loading */}
        {!loaded && !hasError && (
          <div className="absolute inset-0 animate-pulse bg-white/[0.05]" />
        )}

        {!hasError ? (
          <img
            src={proxyUrl}
            alt={title}
            className={`absolute inset-0 h-full w-full object-contain transition-opacity duration-300 ${
              loaded ? "opacity-100" : "opacity-0"
            }`}
            onLoad={() => setLoaded(true)}
            onError={() => {
              setHasError(true);
              setLoaded(true);
            }}
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
            <span className="text-[13px] text-white/35">Image unavailable</span>
            <a
              href={sourceUrl}
              onClick={(e) => e.stopPropagation()}
              className="text-[12px] text-cyan-400/70 underline hover:text-cyan-300"
            >
              Open source ↗
            </a>
          </div>
        )}

        {/* Hover expand button */}
        <button
          type="button"
          onClick={handleExpandClick}
          aria-label="Open source in new tab"
          className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-md border border-white/10 bg-black/50 text-sm text-white/60 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 hover:border-white/20 hover:text-white"
        >
          ↗
        </button>
      </div>

      {/* Footer */}
      <div className="flex items-start gap-2 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium text-white/85">{title}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="text-[11px] text-white/40">{source}</span>
            {freshness !== null && (
              <span className="flex items-center gap-1 text-[11px] text-white/35">
                <span
                  className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${freshness.dotColor}`}
                />
                {freshness.label}
              </span>
            )}
          </div>
        </div>
        <span className="mt-0.5 shrink-0 rounded-full border border-white/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] text-white/40">
          {cadence}
        </span>
      </div>
    </div>
  );
}
