import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

export type TourStepDef = {
  targetSelector: string | null;
  title: string;
  body: string;
  linkText?: string;
  linkHref?: string;
  tooltipAnchorBottom?: boolean;
};

type Props = {
  steps: TourStepDef[];
  currentStep: number;
  isActive: boolean;
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  onComplete: () => void;
  completionVisible: boolean;
  onDismissCompletion: () => void;
};

type Rect = { x: number; y: number; width: number; height: number };

const PADDING = 8;
const TOOLTIP_WIDTH = 260;
const TOOLTIP_MARGIN = 14;
const MOBILE_BOTTOM_OFFSET = 130;

function queryTargetRect(selector: string | null): Rect | null {
  if (!selector) return null;
  const el = document.querySelector(selector);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  return { x: r.left, y: r.top, width: r.width, height: r.height };
}

export function TourOverlay({
  steps,
  currentStep,
  isActive,
  onNext,
  onBack,
  onSkip,
  onComplete,
  completionVisible,
  onDismissCompletion,
}: Props) {
  const [targetRect, setTargetRect] = useState<Rect | null>(null);
  const step = steps[currentStep];
  const isLastStep = currentStep === steps.length - 1;

  const refreshRect = useCallback(() => {
    if (!isActive || !step) {
      setTargetRect(null);
      return;
    }
    setTargetRect(queryTargetRect(step.targetSelector));
  }, [isActive, step]);

  useEffect(() => {
    refreshRect();
  }, [refreshRect]);

  useEffect(() => {
    if (!isActive) return;
    window.addEventListener("resize", refreshRect);
    return () => window.removeEventListener("resize", refreshRect);
  }, [isActive, refreshRect]);

  // Keyboard navigation
  useEffect(() => {
    if (!isActive) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onSkip();
      else if (e.key === "ArrowRight") {
        if (isLastStep) onComplete();
        else onNext();
      } else if (e.key === "ArrowLeft" && currentStep > 0) {
        onBack();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isActive, currentStep, isLastStep, onSkip, onNext, onBack, onComplete]);

  // Auto-dismiss completion modal after 3s
  useEffect(() => {
    if (!completionVisible) return;
    const t = setTimeout(onDismissCompletion, 3000);
    return () => clearTimeout(t);
  }, [completionVisible, onDismissCompletion]);

  if (!isActive && !completionVisible) return null;

  const highlightRect = targetRect
    ? {
        x: targetRect.x - PADDING,
        y: targetRect.y - PADDING,
        width: targetRect.width + 2 * PADDING,
        height: targetRect.height + 2 * PADDING,
      }
    : null;

  // Compute tooltip position
  const viewH = typeof window !== "undefined" ? window.innerHeight : 800;
  const viewW = typeof window !== "undefined" ? window.innerWidth : 1200;

  let tooltipStyle: React.CSSProperties = {};

  if (!isActive || !step) {
    tooltipStyle = {};
  } else if (step.tooltipAnchorBottom) {
    tooltipStyle = {
      position: "fixed",
      bottom: MOBILE_BOTTOM_OFFSET,
      left: "50%",
      transform: "translateX(-50%)",
      width: TOOLTIP_WIDTH,
    };
  } else if (highlightRect) {
    const tooltipLeft = Math.max(
      12,
      Math.min(
        viewW - TOOLTIP_WIDTH - 12,
        highlightRect.x + highlightRect.width / 2 - TOOLTIP_WIDTH / 2
      )
    );
    const spaceBelow = viewH - (highlightRect.y + highlightRect.height);
    const spaceAbove = highlightRect.y;

    if (spaceBelow >= 180 || spaceBelow >= spaceAbove) {
      tooltipStyle = {
        position: "fixed",
        top: highlightRect.y + highlightRect.height + TOOLTIP_MARGIN,
        left: tooltipLeft,
        width: TOOLTIP_WIDTH,
      };
    } else {
      tooltipStyle = {
        position: "fixed",
        bottom: viewH - highlightRect.y + TOOLTIP_MARGIN,
        left: tooltipLeft,
        width: TOOLTIP_WIDTH,
      };
    }
  } else {
    tooltipStyle = {
      position: "fixed",
      top: "50%",
      left: "50%",
      transform: "translate(-50%, -50%)",
      width: TOOLTIP_WIDTH,
    };
  }

  const overlay = isActive && step ? (
    <>
      {/* Full-viewport dim when no spotlight */}
      {!highlightRect ? (
        <div
          aria-hidden="true"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 9990,
            background: "rgba(0,0,0,0.6)",
            pointerEvents: "none",
          }}
        />
      ) : null}

      {/* Spotlight cutout using box-shadow technique */}
      {highlightRect ? (
        <div
          aria-hidden="true"
          style={{
            position: "fixed",
            left: highlightRect.x,
            top: highlightRect.y,
            width: highlightRect.width,
            height: highlightRect.height,
            borderRadius: 6,
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.6)",
            border: "1.5px solid #4a9eff",
            zIndex: 9990,
            pointerEvents: "none",
          }}
        />
      ) : null}

      {/* Tooltip card */}
      <div
        role="dialog"
        aria-label={`Tour step ${currentStep + 1} of ${steps.length}: ${step.title}`}
        style={{
          ...tooltipStyle,
          zIndex: 9995,
          background: "#1e2330",
          border: "1px solid rgba(74,158,255,0.2)",
          borderRadius: 10,
          padding: "14px 16px",
          boxShadow: "0 8px 32px rgba(0,0,0,0.55), inset 0 1px 0 rgba(100,180,255,0.07)",
          color: "rgba(255,255,255,0.92)",
          fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
        }}
      >
        {/* Step counter */}
        <div
          style={{
            fontSize: 10,
            color: "rgba(255,255,255,0.38)",
            marginBottom: 5,
            letterSpacing: "0.06em",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {currentStep + 1} of {steps.length}
        </div>

        {/* Title */}
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>
          {step.title}
        </div>

        {/* Body */}
        <div
          style={{
            fontSize: 11,
            color: "rgba(255,255,255,0.58)",
            lineHeight: 1.55,
            marginBottom: 14,
          }}
        >
          {step.body}
          {step.linkText && step.linkHref ? (
            <>
              {" "}
              <a
                href={step.linkHref}
                style={{
                  color: "#4a9eff",
                  textDecoration: "underline",
                  textUnderlineOffset: 2,
                }}
              >
                {step.linkText}
              </a>
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 8 }}>
          {/* Skip + dot progress */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <button
              type="button"
              onClick={onSkip}
              style={{
                background: "none",
                border: "none",
                color: "rgba(255,255,255,0.32)",
                fontSize: 11,
                cursor: "pointer",
                padding: 0,
                textAlign: "left",
                lineHeight: 1,
              }}
            >
              Skip tour
            </button>
            <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
              {steps.map((_, i) => (
                <span
                  key={i}
                  style={{
                    display: "inline-block",
                    width: i === currentStep ? 14 : 6,
                    height: 6,
                    borderRadius: 9999,
                    background:
                      i === currentStep
                        ? "#4a9eff"
                        : i < currentStep
                          ? "rgba(74,158,255,0.45)"
                          : "rgba(255,255,255,0.18)",
                    transition: "width 200ms ease, background 200ms ease",
                  }}
                />
              ))}
            </div>
          </div>

          {/* Back / Next|Done buttons */}
          <div style={{ display: "flex", gap: 7, flexShrink: 0 }}>
            {currentStep > 0 ? (
              <button
                type="button"
                onClick={onBack}
                style={{
                  background: "rgba(255,255,255,0.05)",
                  border: "1px solid rgba(255,255,255,0.12)",
                  color: "rgba(255,255,255,0.65)",
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: "pointer",
                  padding: "5px 12px",
                  borderRadius: 6,
                  lineHeight: 1,
                }}
              >
                Back
              </button>
            ) : null}
            <button
              type="button"
              onClick={isLastStep ? onComplete : onNext}
              style={{
                background: "#4a9eff",
                border: "none",
                color: "#fff",
                fontSize: 12,
                fontWeight: 600,
                cursor: "pointer",
                padding: "5px 14px",
                borderRadius: 6,
                lineHeight: 1,
              }}
            >
              {isLastStep ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </>
  ) : null;

  const completionModal = completionVisible ? (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9995,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.45)",
      }}
      onClick={onDismissCompletion}
    >
      <div
        role="status"
        aria-live="polite"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#1e2330",
          border: "1px solid rgba(74,158,255,0.2)",
          borderRadius: 14,
          padding: "24px 28px",
          maxWidth: 300,
          textAlign: "center",
          color: "rgba(255,255,255,0.88)",
          fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
          boxShadow: "0 16px 48px rgba(0,0,0,0.55)",
        }}
      >
        <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 18 }}>
          You&rsquo;re all set — tour can be replayed any time from the settings menu
        </div>
        <button
          type="button"
          onClick={onDismissCompletion}
          style={{
            background: "#4a9eff",
            border: "none",
            color: "#fff",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            padding: "7px 20px",
            borderRadius: 7,
          }}
        >
          Got it
        </button>
      </div>
    </div>
  ) : null;

  return createPortal(
    <>
      {overlay}
      {completionModal}
    </>,
    document.body
  );
}
