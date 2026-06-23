import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

type Props = {
  // uPlot options without width — width is measured from the container and kept
  // responsive via ResizeObserver.
  options: Omit<uPlot.Options, "width">;
  data: uPlot.AlignedData;
  className?: string;
};

/**
 * Thin React wrapper around uPlot (canvas time-series). The chart is recreated
 * when `options` identity changes (e.g. series set changes on a pill toggle) and
 * its data is updated in place on `data` changes. Memoize `options` in the
 * parent so it only changes when the chart structure changes.
 */
export function UplotChart({ options, data, className }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const dataRef = useRef<uPlot.AlignedData>(data);
  dataRef.current = data;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const width = Math.max(1, Math.floor(el.clientWidth) || 600);
    const plot = new uPlot({ ...options, width }, dataRef.current, el);
    plotRef.current = plot;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const nextWidth = Math.floor(entry.contentRect.width);
        if (nextWidth > 0) {
          plot.setSize({ width: nextWidth, height: options.height });
        }
      }
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, [options]);

  useEffect(() => {
    if (plotRef.current) {
      plotRef.current.setData(data);
    }
  }, [data]);

  return <div ref={containerRef} className={className} />;
}
