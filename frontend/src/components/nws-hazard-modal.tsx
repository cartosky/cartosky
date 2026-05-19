import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Loader2, X } from "lucide-react";

import { fetchNwsHazardAlertDetail, type NwsHazardAlertDetail } from "@/lib/api";
import type { VectorHazardSelection } from "@/components/map-canvas";

type NwsHazardModalProps = {
  open: boolean;
  onClose: () => void;
  hazard: VectorHazardSelection;
};

type LoadState = {
  loading: boolean;
  error: string | null;
  alerts: NwsHazardAlertDetail[];
};

function formatTime(isoString: string | null | undefined): string {
  if (!isoString) return "Unknown";
  try {
    return new Date(isoString).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return "Unknown";
  }
}

function paragraphLines(text: string | null | undefined): string[] {
  return (text ?? "")
    .split(/\n{2,}|\r?\n\*/g)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function DetailPill({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.05] px-2.5 py-1.5">
      <div className="text-[10px] uppercase text-white/40">{label}</div>
      <div className="mt-0.5 text-xs font-medium text-white/85">{value}</div>
    </div>
  );
}

function AlertDetailCard({ alert }: { alert: NwsHazardAlertDetail }) {
  const descriptionLines = paragraphLines(alert.description);
  const instructionLines = paragraphLines(alert.instruction);

  return (
    <section className="border-t border-white/[0.06] pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold leading-snug text-white">
            {alert.headline || alert.event || "NWS Hazard"}
          </h3>
          {alert.area_description ? (
            <p className="mt-1 text-xs text-white/50">{alert.area_description}</p>
          ) : null}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <DetailPill label="Severity" value={alert.severity} />
        <DetailPill label="Urgency" value={alert.urgency} />
        <DetailPill label="Certainty" value={alert.certainty} />
        <DetailPill label="Expires" value={formatTime(alert.expires)} />
      </div>

      {descriptionLines.length ? (
        <div className="mt-4 space-y-2 text-sm leading-relaxed text-white/75">
          {descriptionLines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : null}

      {instructionLines.length ? (
        <div className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2.5 text-sm leading-relaxed text-amber-50/90">
          {instructionLines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function NwsHazardModal({ open, onClose, hazard }: NwsHazardModalProps) {
  const [state, setState] = useState<LoadState>({ loading: false, error: null, alerts: [] });

  const alertIds = useMemo(
    () => Array.from(new Set(hazard.alertIds.map((id) => id.trim()).filter(Boolean))).slice(0, 6),
    [hazard.alertIds],
  );

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open || alertIds.length === 0) {
      setState({ loading: false, error: null, alerts: [] });
      return;
    }

    const controller = new AbortController();
    setState({ loading: true, error: null, alerts: [] });
    Promise.all(alertIds.map((id) => fetchNwsHazardAlertDetail(id, controller.signal)))
      .then((results) => {
        if (controller.signal.aborted) return;
        setState({
          loading: false,
          error: null,
          alerts: results.filter((alert): alert is NwsHazardAlertDetail => Boolean(alert)),
        });
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setState({
          loading: false,
          error: err instanceof Error ? err.message : "Failed to load hazard details.",
          alerts: [],
        });
      });

    return () => controller.abort();
  }, [open, alertIds]);

  if (!open) return null;

  const hazards = hazard.activeHazards.length
    ? hazard.activeHazards
    : hazard.riskLabel
      ? [hazard.riskLabel]
      : [];

  return (
    <div
      className="fixed inset-0 z-[82] flex items-start justify-center overflow-y-auto bg-slate-950/46 p-2 backdrop-blur-sm backdrop-brightness-[0.62] backdrop-saturate-75 sm:items-center sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={hazard.title}
    >
      <div
        className="glass-overlay my-2 flex max-h-[calc(100dvh-1rem)] w-full max-w-xl flex-col overflow-hidden rounded-2xl text-white sm:my-4 sm:max-h-[calc(100dvh-2rem)]"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 px-4 py-3.5">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-snug text-white sm:text-base">{hazard.title}</h2>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {hazards.map((label) => (
                <span
                  key={label}
                  className="rounded-md border border-white/[0.08] bg-white/[0.06] px-2 py-1 text-[11px] font-medium text-white/75"
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/[0.08] text-white/80 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-colors hover:bg-white/[0.12]"
            aria-label="Close hazard details"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="legend-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-4">
          {state.loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-white/60">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading hazard details
            </div>
          ) : null}

          {!state.loading && state.error ? (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2.5 text-sm text-amber-50/85">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">Details are temporarily unavailable.</div>
                <div className="mt-1 text-xs text-amber-50/70">
                  Expires {formatTime(hazard.expiresTime)}
                </div>
              </div>
            </div>
          ) : null}

          {!state.loading && !state.error && state.alerts.length ? (
            <div className="space-y-4">
              {state.alerts.map((alert, index) => (
                <AlertDetailCard key={alert.id || `${alert.headline || alert.event || "alert"}-${index}`} alert={alert} />
              ))}
            </div>
          ) : null}

          {!state.loading && !state.error && !state.alerts.length ? (
            <div className="rounded-lg border border-white/[0.08] bg-white/[0.05] px-3 py-2.5 text-sm text-white/70">
              Details are not attached to this hazard yet. Expires {formatTime(hazard.expiresTime)}.
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
