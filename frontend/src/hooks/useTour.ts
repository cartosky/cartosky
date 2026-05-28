import { useCallback, useEffect, useRef, useState } from "react";

const TOUR_KEY = "csky_viewer_tour_v1";

export function useTour({ isMapReady }: { isMapReady: boolean }) {
  const [hasSeen] = useState(() => !!localStorage.getItem(TOUR_KEY));
  const [delayDone, setDelayDone] = useState(false);
  const [isActive, setIsActive] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [completionVisible, setCompletionVisible] = useState(false);
  const startedRef = useRef(false);

  // 1.5s minimum delay before tour starts
  useEffect(() => {
    if (hasSeen) return;
    const t = setTimeout(() => setDelayDone(true), 1500);
    return () => clearTimeout(t);
  }, [hasSeen]);

  // Start tour when both delay and map-ready conditions are met
  useEffect(() => {
    if (hasSeen || !delayDone || !isMapReady || startedRef.current) return;
    startedRef.current = true;
    setIsActive(true);
  }, [hasSeen, delayDone, isMapReady]);

  const nextStep = useCallback(() => {
    setCurrentStep((s) => s + 1);
  }, []);

  const prevStep = useCallback(() => {
    setCurrentStep((s) => Math.max(0, s - 1));
  }, []);

  const complete = useCallback(() => {
    localStorage.setItem(TOUR_KEY, "completed");
    setIsActive(false);
    setCompletionVisible(true);
  }, []);

  const skip = useCallback(() => {
    localStorage.setItem(TOUR_KEY, "completed");
    setIsActive(false);
  }, []);

  const replayTour = useCallback(() => {
    localStorage.removeItem(TOUR_KEY);
    startedRef.current = true;
    setCurrentStep(0);
    setCompletionVisible(false);
    setIsActive(true);
  }, []);

  const dismissCompletion = useCallback(() => {
    setCompletionVisible(false);
  }, []);

  return {
    isActive,
    currentStep,
    nextStep,
    prevStep,
    complete,
    skip,
    replayTour,
    completionVisible,
    dismissCompletion,
  };
}
