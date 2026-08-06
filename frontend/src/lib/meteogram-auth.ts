import { clerkJwtTemplate } from "@/lib/admin-api";

export type MeteogramAuthScope = "anon" | "auth";

/**
 * Cache/fetch scope for meteogram responses. Backend payloads vary by bearer
 * token (ECMWF returns `not_entitled` without auth), so the module cache must
 * wait for Clerk hydration and partition anon vs auth entries.
 */
export function meteogramAuthScope(
  isLoaded: boolean,
  isSignedIn: boolean,
): MeteogramAuthScope | null {
  if (!isLoaded) return null;
  return isSignedIn ? "auth" : "anon";
}

/** Auth headers for meteogram requests (optional Clerk bearer token). */
export async function meteogramAuthHeaders(
  getToken: (options?: { template?: string }) => Promise<string | null>,
  isSignedIn: boolean,
): Promise<Record<string, string>> {
  if (!isSignedIn) return {};
  let token: string | null = null;
  try {
    token = await getToken({ template: clerkJwtTemplate() });
  } catch {
    token = null;
  }
  if (!token) {
    // Do not send an unauthenticated request under an "auth" cache scope —
    // that would cache `not_entitled` and block Pro ECMWF for the TTL.
    throw new Error("Unable to load CartoSky auth token.");
  }
  return { Authorization: `Bearer ${token}` };
}
