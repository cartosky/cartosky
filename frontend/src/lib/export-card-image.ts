import type { RefObject } from "react";

import { CHART_THEME } from "@/lib/chart-constants";

// Fixed block heights composited around the chart canvases (CSS px).
const HEADER_HEIGHT = 80;
const FOOTER_HEIGHT = 36;
const HEADER_GAP = 8;
const SIDE_PADDING = 16;
const LOGO_HEIGHT = 22;
const MIN_WIDTH = 480;
const MAX_WIDTH = 1280;

const FONT_FAMILY = "ui-sans-serif, system-ui, sans-serif";

/** Load an image and resolve once decoded; resolves null if it fails to load. */
function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

/** Promisified canvas.toBlob (PNG). */
function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/png"));
}

/** Trigger a browser download of a blob under the given filename. */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Composite the full Model Detail card (header + every uPlot chart canvas +
 * footer) into one PNG.
 *
 * `mode: "clipboard"` (default) copies to the clipboard, falling back to a
 * download if the clipboard write is unavailable/denied. `mode: "download"`
 * always downloads the PNG directly.
 */
export async function exportCardImage(opts: {
  cardRef: RefObject<HTMLDivElement | null>;
  filename: string;
  headerText: string; // e.g. "ECMWF · 12z Jun 24"
  locationText: string; // e.g. "Nashville, TN · 36.1659°N, 86.7844°W"
  logoUrl: string; // "/assets/new_logo.png"
  mode?: "clipboard" | "download";
}): Promise<"copied" | "downloaded"> {
  const card = opts.cardRef.current;
  if (!card) throw new Error("exportCardImage: card ref is not mounted");

  // Step 1 — collect uPlot chart canvases in DOM (strip) order.
  const canvases = [...card.querySelectorAll("canvas")] as HTMLCanvasElement[];

  // Step 2 — measure total height + output width.
  const canvasHeight = canvases.reduce((sum, c) => sum + c.offsetHeight, 0);
  const totalHeight = HEADER_HEIGHT + HEADER_GAP + canvasHeight + FOOTER_HEIGHT;
  const totalWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, card.offsetWidth));

  // Step 3 — offscreen canvas with devicePixelRatio scaling for retina sharpness.
  const dpr = window.devicePixelRatio || 1;
  const out = document.createElement("canvas");
  out.width = Math.round(totalWidth * dpr);
  out.height = Math.round(totalHeight * dpr);
  const ctx = out.getContext("2d");
  if (!ctx) throw new Error("exportCardImage: 2D context unavailable");
  ctx.scale(dpr, dpr);

  ctx.fillStyle = CHART_THEME.background;
  ctx.fillRect(0, 0, totalWidth, totalHeight);

  // Step 4 — header block (vertically centered in the 80px band).
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.font = `500 16px ${FONT_FAMILY}`;
  ctx.fillText(opts.headerText, SIDE_PADDING, HEADER_HEIGHT / 2 - 2);
  ctx.fillStyle = "rgba(255,255,255,0.40)";
  ctx.font = `12px ${FONT_FAMILY}`;
  ctx.fillText(opts.locationText, SIDE_PADDING, HEADER_HEIGHT / 2 + 18);

  // Step 5 — draw each chart canvas, stretched to the full output width so all
  // strips align regardless of their rendered pixel width.
  let yOffset = HEADER_HEIGHT + HEADER_GAP;
  for (const canvas of canvases) {
    const h = canvas.offsetHeight;
    ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height, 0,yOffset, totalWidth, h);
    yOffset += h;
  }

  // Step 6 — footer block.
  const footerTop = totalHeight - FOOTER_HEIGHT;
  const footerMid = footerTop + FOOTER_HEIGHT / 2;
  const logo = await loadImage(opts.logoUrl);
  if (logo && logo.naturalHeight > 0) {
    const iconSrcW = 340;  // icon mark ends at col ~331, add small margin
    const iconSrcH = logo.naturalHeight;
    const iconDestH = LOGO_HEIGHT + 4;
    const iconDestW = Math.round(iconSrcW * (iconDestH / iconSrcH));

    // Step-down resample: halve repeatedly until within 2x of target.
    // Single-pass extreme downscale (6.9x here) produces aliasing even with
    // imageSmoothingQuality "high". Each halving pass is clean.
    let current = document.createElement("canvas");
    current.width = iconSrcW;
    current.height = iconSrcH;
    const initCtx = current.getContext("2d")!;
    initCtx.drawImage(logo, 0, 0, iconSrcW, iconSrcH, 0, 0, iconSrcW, iconSrcH);

    const targetW = iconDestW * dpr;
    const targetH = iconDestH * dpr;

    while (current.width / 2 > targetW) {
      const next = document.createElement("canvas");
      next.width = Math.round(current.width / 2);
      next.height = Math.round(current.height / 2);
      const nx = next.getContext("2d")!;
      nx.imageSmoothingEnabled = true;
      nx.imageSmoothingQuality = "high";
      nx.drawImage(current, 0, 0, next.width, next.height);
      current = next;
    }

    // Final draw onto output canvas at logical coordinates
    // (ctx is already scaled by dpr, so use logical dest dimensions)
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(current, SIDE_PADDING, footerMid - iconDestH / 2, iconDestW, iconDestH);
  }
  ctx.fillStyle = "rgba(255,255,255,0.40)";
  ctx.font = `11px ${FONT_FAMILY}`;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  ctx.fillText("cartosky.com", totalWidth - SIDE_PADDING, footerMid);

  // Step 7 — export per mode.
  const blob = await canvasToPngBlob(out);
  if (!blob) throw new Error("exportCardImage: failed to encode PNG");

  if (opts.mode === "download") {
    downloadBlob(blob, opts.filename);
    return "downloaded";
  }

  // Default: clipboard first, download as fallback.
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    return "copied";
  } catch {
    downloadBlob(blob, opts.filename);
    return "downloaded";
  }
}
