import { useEffect, useRef, useState } from "react";
import { useAuth } from "@clerk/react";
import { AlertCircle, Bug, CheckCircle2, Gauge, Lightbulb, MessageSquareText, Send, Sparkles, X } from "lucide-react";
import { useLocation } from "react-router-dom";

import { API_ORIGIN } from "@/lib/config";
import { useFeedbackContext } from "@/lib/feedback-context";
import { clerkJwtTemplate } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

type FeedbackCategory = "bug" | "performance" | "feature" | "data_accuracy" | "ui_ux";

type SubmitState = "idle" | "submitting" | "success" | "rate-limited" | "error";

const MESSAGE_MAX_LENGTH = 1000;
const REPORTER_NAME_MAX_LENGTH = 80;
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
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const feedbackContext = useFeedbackContext();
  const { isFeedbackOpen, closeFeedback } = feedbackContext;
  const closeTimerRef = useRef<number | null>(null);
  const [category, setCategory] = useState<FeedbackCategory | null>(null);
  const [reporterName, setReporterName] = useState("");
  const [message, setMessage] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);

  const remainingChars = MESSAGE_MAX_LENGTH - message.length;
  const canSubmit = Boolean(category && message.trim().length > 0 && submitState !== "submitting");

  useEffect(() => {
    if (!isFeedbackOpen) return;
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setCategory(null);
    setReporterName("");
    setMessage("");
    setSubmitState("idle");
    setSubmitMessage(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFeedbackOpen]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!isFeedbackOpen) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeWidget();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [isFeedbackOpen]);

  function closeWidget() {
    closeFeedback();
    setSubmitState("idle");
    setSubmitMessage(null);
  }

  async function submitFeedback() {
    if (!category) {
      setSubmitMessage("Choose a category before sending.");
      return;
    }
    if (!message.trim()) {
      setSubmitMessage("Add a short note before sending.");
      return;
    }

    setSubmitState("submitting");
    setSubmitMessage(null);
    try {
      const submissionContext = {
        pageContext: feedbackContext.pageContext || buildPageContext(location),
        modelContext: feedbackContext.modelContext,
        variableContext: feedbackContext.variableContext,
        runContext: feedbackContext.runContext,
        fhrContext: feedbackContext.fhrContext,
        animationStateContext: feedbackContext.animationStateContext,
      };
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (isLoaded && isSignedIn) {
        const token = await getToken({ template: clerkJwtTemplate() });
        if (token) {
          headers.Authorization = `Bearer ${token}`;
        }
      }
      const response = await fetch(`${API_ORIGIN}/api/v4/feedback`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({
          category,
          message: message.trim(),
          reporter_name: reporterName.trim() || null,
          page_context: submissionContext.pageContext,
          model_context: submissionContext.modelContext,
          variable_context: submissionContext.variableContext,
          run_context: submissionContext.runContext,
          fhr_context: submissionContext.fhrContext,
          animation_state_context: submissionContext.animationStateContext,
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
      {isFeedbackOpen ? (
        <div
          className="viewer-mobile-backdrop fixed inset-0 z-[80] flex items-end justify-center sm:items-center sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Feedback"
          onClick={closeWidget}
        >
          <div
            className="viewer-mobile-surface w-full max-w-[520px] flex flex-col overflow-hidden rounded-t-3xl text-white sm:rounded-2xl"
            style={{ maxHeight: "calc(100dvh - env(safe-area-inset-top, 0px) - 1.5rem)" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex justify-center pb-1 pt-3 sm:hidden">
              <div className="h-1 w-9 rounded-full bg-white/20" />
            </div>

            <div className="shrink-0 flex items-center justify-between gap-3 border-b border-white/8 px-4 pb-3 pt-3 sm:px-5 sm:pt-4">
              <div>
                <div className="text-base font-semibold tracking-tight text-white">Feedback</div>
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

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
              <div className="space-y-4">
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
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
                          : "viewer-mobile-inset text-white/72 hover:border-white/18 hover:bg-white/[0.07]"
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
                <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.18em] text-white/44" htmlFor="feedback-reporter-name">
                  Name / Username (optional)
                </label>
                <input
                  id="feedback-reporter-name"
                  type="text"
                  value={reporterName}
                  onChange={(event) => setReporterName(event.target.value.slice(0, REPORTER_NAME_MAX_LENGTH))}
                  maxLength={REPORTER_NAME_MAX_LENGTH}
                  className="viewer-mobile-field w-full rounded-lg px-3 py-2.5 text-sm text-white placeholder:text-white/34"
                  placeholder="Enter a name with your report, or submit anonymously"
                  autoComplete="nickname"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.18em] text-white/44" htmlFor="feedback-details">
                  Details
                </label>
                <textarea
                  id="feedback-details"
                  value={message}
                  onChange={(event) => setMessage(event.target.value.slice(0, MESSAGE_MAX_LENGTH))}
                  rows={4}
                  maxLength={MESSAGE_MAX_LENGTH}
                  className="viewer-mobile-field min-h-[96px] w-full resize-none rounded-lg px-3 py-3 text-sm leading-6 text-white placeholder:text-white/34"
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
            </div>

            <div
              className="shrink-0 flex items-center justify-end gap-2 border-t border-white/8 px-4 pt-3 sm:px-5"
              style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
            >
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