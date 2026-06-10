import { useCallback } from "react";
import { useAuth } from "@clerk/react";

import { clerkJwtTemplate } from "@/lib/admin-api";

export function useAuthFetch() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useCallback(
    async (url: string, init: RequestInit = {}) => {
      if (!isLoaded) {
        throw new Error("Checking CartoSky sign-in status.");
      }
      if (!isSignedIn) {
        throw new Error("Sign in to CartoSky to continue.");
      }

      const token = await getToken({ template: clerkJwtTemplate() });
      if (!token) {
        throw new Error("Unable to load CartoSky auth token.");
      }

      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${token}`);
      headers.set("Accept", headers.get("Accept") || "application/json");

      return fetch(url, {
        ...init,
        credentials: init.credentials ?? "omit",
        headers,
      });
    },
    [getToken, isLoaded, isSignedIn]
  );
}
