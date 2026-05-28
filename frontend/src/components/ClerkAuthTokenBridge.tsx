import { useEffect } from "react";
import { useAuth } from "@clerk/react";

import { clerkJwtTemplate, setClerkAuthTokenProvider } from "@/lib/admin-api";

export function ClerkAuthTokenBridge() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  useEffect(() => {
    if (!isLoaded) {
      setClerkAuthTokenProvider(null);
      return undefined;
    }

    setClerkAuthTokenProvider(async () => {
      if (!isSignedIn) {
        return null;
      }
      return getToken({ template: clerkJwtTemplate() });
    });

    return () => {
      setClerkAuthTokenProvider(null);
    };
  }, [getToken, isLoaded, isSignedIn]);

  return null;
}