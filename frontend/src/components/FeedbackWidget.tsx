import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Bug, CheckCircle2, Gauge, Lightbulb, MessageSquareText, Send, Sparkles, X } from "lucide-react";
import { useLocation } from "react-router-dom";

import { API_ORIGIN } from "@/lib/config";
import { useFeedbackContext } from "@/lib/feedback-context";
import { cn } from "@/lib/utils";

type FeedbackCategory = "bug" | "performance" | "feature" | "data_accuracy" | "ui_ux";

type TwfStatus = {
  linked: boolean;
  admin?: boolean;
  member_id?: number;
  display_name?: string;
  photo_url?: string | null;
};

type CapturedContext = {
  pageContext: string;
  modelContext: string | null;
  fhrContext: number | null;
};

type SubmitState = "idle" | "submitting" | "success" | "rate-limited" | "error";

const MESSAGE_MAX_LENGTH = 1000;
const APP_VERSION = String(import.meta.env.VITE_APP_VERSION ?? import.meta.env.VITE_RELEASE_SHA ?? "").trim() || null;

const CATEGORY_OPTIONS: Array<{
  value: FeedbackCategory;
  label: string;
  icon: typeof Bug;
}> = [
  { value: "bug", label: "Bug / Broken", icon: Bug },
  { value: "performance", label: "Performance", icon: Gauge },
  { value: "feature", label: "Feature", icon: Lightbulb },
  { value: "data_accuracy", label: "Data Accuracy", icon: Sparkles },
  { value: "ui_ux", label: "UI / UX", icon: MessageSquareText },
];

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeTwfStatus(value: unknown): TwfStatus {
  if (!isObject(value) || value.linked !== true) {
    return { linked: false };
  }
  const displayName = typeof value.display_name === "string" ? value.display_name.trim() : "";
  return {
    linked: true,
    admin: value.admin === true,
    member_id: Number.isFinite(Number(value.member_id)) ? Number(value.member_id) : undefined,
    display_name: displayName || undefined,
    photo_url: typeof value.photo_url === "string" ? value.photo_url : null,
  };
}

async function readApiMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as unknown;
    if (isObject(body) && isObject(body.error) && typeof body.error.message === "string") {
      return body.error.message;
    }
  } catch {
    // Keep the status fallback below.
  }
  return `Request failed (${response.status})`;
}

function buildPageContext(location: ReturnType<typeof useLocation>): string {
  return `${location.pathname}${location.search}${location.hash}` || "/";
}

export function FeedbackWidget() {
  const location = useLocation();
  const feedbackContext = useFeedbackContext();
  const closeTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [capturedContext, setCapturedContext] = useState<CapturedContext | null>(null);
  const [twfStatus, setTwfStatus] = useState<TwfStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [category, setCategory] = useState<FeedbackCategory | null>(null);
  const [message, setMessage] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);

  const remainingChars = MESSAGE_MAX_LENGTH - message.length;
  const canSubmit = Boolean(
    category
    && message.trim().length > 0
    && twfStatus?.linked
    && submitState !== "submitting"
    && !statusLoading
  );

  const sessionStatusLabel = useMemo(() => {
    if (statusLoading) {
      return "Checking TWF session";
    }
    if (twfStatus?.linked) {
      const displayName = twfStatus.display_name || (twfStatus.member_id ? `member-${twfStatus.member_id}` : "Weather Forums member");
      return `Submitting as ${displayName}`;
    }
    return "Log in to your TWF account before submitting feedback.";
  }, [statusLoading, twfStatus]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }

    const controller = new AbortController();
    setStatusLoading(true);

    fetch(`${API_ORIGIN}/auth/twf/status`, {
      method: "GET",
      credentials: "include",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(await readApiMessage(response));
        }
        return response.json() as Promise<unknown>;
      })
      .then((value) => {
        setTwfStatus(normalizeTwfStatus(value));
      })
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        setTwfStatus({ linked: false });
        setSubmitMessage((error as Error).message || "Unable to check Weather Forums session.");
      })
      .finally(() => setStatusLoading(false));

    return () => controller.abort();
  }, [open]);

  function openWidget() {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setCapturedContext({
      pageContext: buildPageContext(location),
      modelContext: feedbackContext.modelContext,
      fhrContext: feedbackContext.fhrContext,
    });
    setCategory(null);
    setMessage("");
    setSubmitState("idle");
    setSubmitMessage(null);
    setOpen(true);
  }

  function closeWidget() {
    setOpen(false);
    setSubmitState("idle");
    setSubmitMessage(null);
  }

  async function submitFeedback() {
    if (!category || !capturedContext) {
      setSubmitMessage("Choose a category before sending.");
      return;
    }
    if (!message.trim()) {
      setSubmitMessage("Add a short note before sending.");
      return;
    }
    if (!twfStatus?.linked) {
      setSubmitMessage("Log in to your TWF account before submitting a feedback report.");
      return;
    }

    setSubmitState("submitting");
    setSubmitMessage(null);
    try {
      const response = await fetch(`${API_ORIGIN}/api/v4/feedback`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category,
          message: message.trim(),
          page_context: capturedContext.pageContext,
          model_context: capturedContext.modelContext,
          fhr_context: capturedContext.fhrContext,
          app_version: APP_VERSION,
        }),
      });
      if (response.status === 429) {
        setSubmitState("rate-limited");
        setSubmitMessage(await readApiMessage(response));
        return;
      }
      if (!response.ok) {
        throw new Error(await readApiMessage(response));
      }
      setSubmitState("success");
      setSubmitMessage("Sent. Thank you.");
      closeTimerRef.current = window.setTimeout(() => {
        closeWidget();
      }, 1200);
    } catch (error: unknown) {
      setSubmitState("error");
      setSubmitMessage((error as Error).message || "Unable to send feedback.");
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={openWidget}
        className="fixed bottom-[calc(env(safe-area-inset-bottom)+4.25rem)] right-3 z-[44] inline-flex h-12 w-12 items-center justify-center rounded-full border border-cyan-200/25 bg-[#0c1a2d]/92 text-cyan-100 shadow-[0_18px_42px_rgba(0,0,0,0.35)] backdrop-blur-md transition hover:border-cyan-100/40 hover:bg-[#10243d] focus:outline-none focus:ring-2 focus:ring-cyan-300/50 sm:right-4"
        aria-label="Send feedback"
        title="Send feedback"
      >
        <MessageSquareText className="h-5 w-5" />
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-[80] flex items-end justify-center bg-slate-950/60 backdrop-blur-sm backdrop-brightness-[0.62] backdrop-saturate-75 sm:items-center sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Feedback"
          onClick={closeWidget}
        >
          <div
            className="glass w-full max-w-[520px] overflow-hidden rounded-t-3xl text-white sm:rounded-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex justify-center pb-1 pt-3 sm:hidden">
              <div className="h-1 w-9 rounded-full bg-white/20" />
            </div>

            <div className="flex items-center justify-between gap-3 border-b border-white/8 px-4 pb-3 pt-3 sm:px-5 sm:pt-4">
              <div>
                <div className="text-base font-semibold tracking-tight text-white">Feedback</div>
                <div className="mt-1 text-xs text-white/52">{sessionStatusLabel}</div>
              </div>
              <button
                type="button"
                onClick={closeWidget}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-white/[0.08] text-white/70 transition-colors hover:bg-white/[0.12]"
                aria-label="Close feedback"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4 px-4 py-4 sm:px-5">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {CATEGORY_OPTIONS.map((option) => {
                  const Icon = option.icon;
                  const selected = category === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setCategory(option.value)}
                      className={cn(
                        "flex min-h-16 flex-col items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-center text-[11px] font-semibold leading-tight transition",
                        selected
                          ? "border-cyan-200/50 bg-cyan-300/14 text-cyan-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]"
                          : "border-white/10 bg-white/[0.04] text-white/72 hover:border-white/18 hover:bg-white/[0.07]"
                      )}
                      aria-pressed={selected}
                    >
                      <Icon className="h-4 w-4" />
                      <span>{option.label}</span>
                    </button>
                  );
                })}
              </div>

              <div>
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value.slice(0, MESSAGE_MAX_LENGTH))}
                  rows={6}
                  maxLength={MESSAGE_MAX_LENGTH}
                  className="min-h-[132px] w-full resize-none rounded-lg border border-cyan-200/10 bg-[#091322]/75 px-3 py-3 text-sm leading-6 text-white outline-none transition-colors placeholder:text-white/34 focus:border-cyan-300/34 focus:bg-[#0c182a]"
                  placeholder="What should we know?"
                />
                <div className="mt-2 flex items-center justify-end text-xs text-white/45">
                  <span className={remainingChars < 80 ? "text-amber-200" : undefined}>{message.length}/{MESSAGE_MAX_LENGTH}</span>
                </div>
              </div>

              {submitMessage ? (
                <div
                  className={cn(
                    "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
                    submitState === "success"
                      ? "border-emerald-300/24 bg-emerald-400/10 text-emerald-100"
                      : "border-amber-300/22 bg-amber-400/10 text-amber-100"
                  )}
                >
                  {submitState === "success" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
                  <span>{submitMessage}</span>
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-white/8 px-4 py-3 sm:px-5">
              {!twfStatus?.linked && !statusLoading ? (
                <a
                  href="/login"
                  className="mr-auto inline-flex h-9 items-center rounded-lg border border-white/12 bg-white/[0.04] px-3 text-sm font-semibold text-white/78 transition hover:bg-white/[0.07]"
                >
                  Sign in
                </a>
              ) : null}
              <button
                type="button"
                onClick={submitFeedback}
                disabled={!canSubmit}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-cyan-200/30 bg-[linear-gradient(135deg,#102438_0%,#1a4f68_52%,#6ab7d4_100%)] px-4 text-sm font-semibold text-white shadow-[0_14px_34px_rgba(17,68,92,0.34)] transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:brightness-100"
              >
                {submitState === "submitting" ? "Sending" : "Send"}
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}