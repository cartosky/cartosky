import { getClerkAuthToken } from "@/lib/admin-api";
import { API_V4_BASE } from "@/lib/config";

type BillingEndpointBody = {
  success_url?: string;
  cancel_url?: string;
  return_url?: string;
};

type BillingEndpointResponse = {
  url?: unknown;
  detail?: unknown;
  message?: unknown;
};

function errorMessageFromBody(body: BillingEndpointResponse): string | null {
  if (typeof body.message === "string" && body.message.trim()) {
    return body.message;
  }
  if (typeof body.detail === "string" && body.detail.trim()) {
    return body.detail;
  }
  if (body.detail && typeof body.detail === "object") {
    const detail = body.detail as { error?: { message?: unknown } };
    if (typeof detail.error?.message === "string" && detail.error.message.trim()) {
      return detail.error.message;
    }
  }
  return null;
}

async function billingRequest(path: string, body: BillingEndpointBody): Promise<string> {
  const token = await getClerkAuthToken();
  if (!token) {
    throw new Error("Sign in to manage CartoSky billing.");
  }

  const response = await fetch(`${API_V4_BASE}${path}`, {
    method: "POST",
    credentials: "omit",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const parsedBody = (await response.json().catch(() => null)) as BillingEndpointResponse | null;
  if (!response.ok) {
    throw new Error(parsedBody ? errorMessageFromBody(parsedBody) ?? `Billing request failed (${response.status})` : `Billing request failed (${response.status})`);
  }

  const url = typeof parsedBody?.url === "string" ? parsedBody.url.trim() : "";
  if (!url) {
    throw new Error("Billing endpoint did not return a redirect URL.");
  }
  return url;
}

export function createCheckoutSession(successUrl: string, cancelUrl: string): Promise<string> {
  return billingRequest("/billing/create-checkout-session", {
    success_url: successUrl,
    cancel_url: cancelUrl,
  });
}

export function createPortalSession(returnUrl: string): Promise<string> {
  return billingRequest("/billing/create-portal-session", {
    return_url: returnUrl,
  });
}