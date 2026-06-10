import { useEffect } from "react";
import { useAuth } from "@clerk/react";

import { useBootstrapComplete } from "@/lib/bootstrap-loading";
import { prefetchRoute, scheduleIdleRoutePrefetch } from "@/lib/route-prefetch";

export function RoutePrefetchBridge() {
  const bootstrapComplete = useBootstrapComplete();
  const { isLoaded, isSignedIn } = useAuth();

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      return;
    }
    prefetchRoute("account");
  }, [isLoaded, isSignedIn]);

  useEffect(() => {
    if (!bootstrapComplete) {
      return;
    }

    const idleRoutes: Array<"viewer" | "forecast" | "account"> = ["viewer", "forecast"];
    if (isLoaded && isSignedIn) {
      idleRoutes.push("account");
    }

    return scheduleIdleRoutePrefetch(idleRoutes);
  }, [bootstrapComplete, isLoaded, isSignedIn]);

  return null;
}
