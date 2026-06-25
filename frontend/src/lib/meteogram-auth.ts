import { clerkJwtTemplate } from "@/lib/admin-api";

export type MeteogramAuthScope = "anon" | "auth";

/** Cache/fetch scope for meteogram responses (vary by bearer token on the backend). */
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
  try {
    const token = await getToken({ template: clerkJwtTemplate() });
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}
