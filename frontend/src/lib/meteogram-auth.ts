import { clerkJwtTemplate } from "@/lib/admin-api";

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
