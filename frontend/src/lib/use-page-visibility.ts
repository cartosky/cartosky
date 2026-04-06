import { useEffect, useState } from "react";

/**
 * Tracks whether the page is currently visible (i.e. the browser tab is focused).
 *
 * Returns `true` when `document.hidden` is `false`, and updates reactively via
 * the `visibilitychange` event.
 */
export function usePageVisibility(): boolean {
  const [isPageVisible, setIsPageVisible] = useState(() =>
    typeof document === "undefined" ? true : !document.hidden
  );

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsPageVisible(!document.hidden);
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return isPageVisible;
}
