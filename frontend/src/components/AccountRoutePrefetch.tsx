import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/react";

export function AccountRoutePrefetch() {
  const { isLoaded, isSignedIn } = useAuth();
  const prefetchedRef = useRef(false);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || prefetchedRef.current) {
      return;
    }

    prefetchedRef.current = true;
    void import("@/pages/account");
  }, [isLoaded, isSignedIn]);

  return null;
}
