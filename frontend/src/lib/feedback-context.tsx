import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type FeedbackViewerContext = {
  modelContext: string | null;
  fhrContext: number | null;
};

type FeedbackContextValue = FeedbackViewerContext & {
  setViewerContext: (context: FeedbackViewerContext) => void;
  clearViewerContext: () => void;
  isFeedbackOpen: boolean;
  openFeedback: () => void;
  closeFeedback: () => void;
};

const FeedbackContext = createContext<FeedbackContextValue | null>(null);

const emptyViewerContext: FeedbackViewerContext = {
  modelContext: null,
  fhrContext: null,
};

function normalizeViewerContext(context: FeedbackViewerContext): FeedbackViewerContext {
  const modelContext = context.modelContext?.trim() || null;
  const fhrContext = Number.isFinite(context.fhrContext) ? Number(context.fhrContext) : null;
  return { modelContext, fhrContext };
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [viewerContext, setViewerContextState] = useState<FeedbackViewerContext>(emptyViewerContext);
  const [isFeedbackOpen, setIsFeedbackOpen] = useState(false);

  const setViewerContext = useCallback((context: FeedbackViewerContext) => {
    setViewerContextState(normalizeViewerContext(context));
  }, []);

  const clearViewerContext = useCallback(() => {
    setViewerContextState(emptyViewerContext);
  }, []);

  const openFeedback = useCallback(() => setIsFeedbackOpen(true), []);
  const closeFeedback = useCallback(() => setIsFeedbackOpen(false), []);

  const value = useMemo<FeedbackContextValue>(() => ({
    ...viewerContext,
    setViewerContext,
    clearViewerContext,
    isFeedbackOpen,
    openFeedback,
    closeFeedback,
  }), [clearViewerContext, setViewerContext, viewerContext, isFeedbackOpen, openFeedback, closeFeedback]);

  return <FeedbackContext.Provider value={value}>{children}</FeedbackContext.Provider>;
}

export function useFeedbackContext(): FeedbackContextValue {
  const value = useContext(FeedbackContext);
  if (!value) {
    throw new Error("useFeedbackContext must be used inside FeedbackProvider");
  }
  return value;
}