import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Clipboard, ClipboardCheck, Download, Loader2 } from "lucide-react";

import { exportCardImage } from "@/lib/export-card-image";

/**
 * Metadata for the card's copy/download-image buttons. When provided, the
 * container renders Copy + Download buttons (once the chart has rendered a
 * canvas) that composite the card into a shareable PNG via `exportCardImage`.
 */
export type ChartExportConfig = {
  /** Bold first line of the exported image header, e.g. "Temperature". */
  headerText: string;
  /** Dim second line, e.g. "Nashville, TN · 36.1659°N, 86.7844°W". */
  locationText?: string;
  /** Slug used in the download filename: `cartosky-<slug>-<ts>.png`. */
  filenameSlug: string;
};

type ChartContainerProps = {
  title: string;
  subtitle?: string;
  filterSlot?: ReactNode;
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** Enables the copy/download-image buttons for this card. */
  exportImage?: ChartExportConfig;
  children?: ReactNode;
};

/** Copy + Download image buttons, shown once the card has a rendered canvas. */
function ExportButtons({
  cardRef,
  config,
}: {
  cardRef: React.RefObject<HTMLDivElement | null>;
  config: ChartExportConfig;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "downloaded">("idle");
  const [downloadState, setDownloadState] = useState<"idle" | "downloaded">("idle");
  // Only offer export once the chart has actually painted a canvas — empty-data
  // states render a message div with no canvas, and there's nothing to export.
  // A MutationObserver (rather than a one-shot check) is required because the
  // chart's uPlot canvas is appended in the child's effect, which can run after
  // this component mounts; the observer flips `hasCanvas` when it appears.
  const [hasCanvas, setHasCanvas] = useState(false);

  useEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const update = () => setHasCanvas(el.querySelectorAll("canvas").length > 0);
    update();
    const observer = new MutationObserver(update);
    observer.observe(el, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [cardRef]);

  function runExport(mode: "clipboard" | "download") {
    return exportCardImage({
      cardRef,
      filename: `cartosky-${config.filenameSlug}-${Date.now()}.png`,
      headerText: config.headerText,
      locationText: config.locationText ?? "",
      logoUrl: "/assets/new_logo.png",
      mode,
    });
  }

  async function handleCopy() {
    const result = await runExport("clipboard");
    setCopyState(result === "copied" ? "copied" : "downloaded");
    setTimeout(() => setCopyState("idle"), 2000);
  }

  async function handleDownload() {
    await runExport("download");
    setDownloadState("downloaded");
    setTimeout(() => setDownloadState("idle"), 2000);
  }

  if (!hasCanvas) return null;

  const buttonClass = (active: boolean) =>
    `rounded-md border border-white/10 p-1.5 transition-colors hover:bg-white/[0.06] ${
      active ? "text-green-400" : "text-white/55 hover:text-white/85"
    }`;

  const copyIcon =
    copyState === "copied" ? (
      <ClipboardCheck className="h-4 w-4" />
    ) : copyState === "downloaded" ? (
      <Download className="h-4 w-4" />
    ) : (
      <Clipboard className="h-4 w-4" />
    );

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={handleCopy}
        title="Copy image"
        aria-label="Copy image"
        className={buttonClass(copyState !== "idle")}
      >
        {copyIcon}
      </button>
      <button
        type="button"
        onClick={handleDownload}
        title="Download image"
        aria-label="Download image"
        className={buttonClass(downloadState !== "idle")}
      >
        {downloadState === "downloaded" ? (
          <Check className="h-4 w-4" />
        ) : (
          <Download className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}

/**
 * Card shell for Model Guidance charts. Matches Forecast page card styling
 * (p-4 md:p-5, bg-white/[0.03], border-white/10, rounded-xl). Renders loading
 * skeleton and inline error states while preserving the chart area.
 */
export function ChartContainer({
  title,
  subtitle,
  filterSlot,
  isLoading,
  error,
  onRetry,
  exportImage,
  children,
}: ChartContainerProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const showExport = Boolean(exportImage) && !isLoading && !error;

  return (
    <div ref={cardRef} className="rounded-xl border border-white/10 bg-white/[0.03] p-4 md:p-5">
      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-[14px] font-medium text-white/85">{title}</h3>
          {subtitle && <p className="mt-0.5 text-[12px] text-white/40">{subtitle}</p>}
        </div>
        {(filterSlot || showExport) && (
          <div className="flex items-center gap-2 sm:flex-none">
            {filterSlot}
            {showExport && exportImage && (
              <ExportButtons cardRef={cardRef} config={exportImage} />
            )}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex h-[320px] w-full items-center justify-center rounded-lg bg-white/[0.02]">
          <Loader2 className="h-6 w-6 animate-spin text-cyan-200/80" />
        </div>
      ) : error ? (
        <div className="flex h-[320px] w-full flex-col items-center justify-center gap-3 rounded-lg bg-white/[0.02] text-center">
          <p className="px-6 text-[13px] text-white/55">{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md border border-white/15 px-3 py-1.5 text-[12px] text-white/70 transition-colors hover:bg-white/[0.06]"
            >
              Retry
            </button>
          )}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
