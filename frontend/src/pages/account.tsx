import { useAuth, Show, UserProfile, useUser } from "@clerk/react";
import { AlertTriangle, CheckCircle2, CreditCard, Link2, Plug, RefreshCw, Unlink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { createPortalSession } from "@/lib/billing";
import { clerkAppearance } from "@/lib/clerk-appearance";
import { billingEnabled, planFromPublicMetadata } from "@/lib/entitlements";
import { API_ORIGIN } from "@/lib/config";
import { clerkJwtTemplate } from "@/lib/admin-api";

const INTEGRATIONS_HASH = "#/integrations";

type TwfConnectionStatus = {
  connected: boolean;
  twf_username: string | null;
};

type ApiErrorBody = {
  detail?: unknown;
  message?: unknown;
};

async function readApiError(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (typeof body.message === "string" && body.message.trim()) return body.message;
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    return null;
  }
  return null;
}

function readTwfStatus(body: unknown): TwfConnectionStatus {
  if (!body || typeof body !== "object") {
    return { connected: false, twf_username: null };
  }
  const value = body as Record<string, unknown>;
  const connected = value.connected === true || value.linked === true;
  const username =
    typeof value.twf_username === "string"
      ? value.twf_username
      : typeof value.display_name === "string"
        ? value.display_name
        : null;
  return { connected, twf_username: username };
}

function TwfConnectedAccountReturn() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (location.pathname === "/account" && (params.get("twf_connected") === "true" || params.get("twf") === "linked" || params.get("twf") === "error")) {
      if (window.location.hash !== INTEGRATIONS_HASH) {
        window.location.hash = INTEGRATIONS_HASH;
      }
      navigate(`/account${location.search}${INTEGRATIONS_HASH}`, { replace: true });
    }
  }, [location.pathname, location.search, navigate]);

  return null;
}

function TwfConnectionSection() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState<TwfConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<"connect" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const authedFetch = useCallback(
    async (url: string, init: RequestInit = {}) => {
      if (!isLoaded) throw new Error("Checking CartoSky sign-in status.");
      if (!isSignedIn) throw new Error("Sign in to CartoSky before managing TWF.");
      const token = await getToken({ template: clerkJwtTemplate() });
      if (!token) throw new Error("Unable to load CartoSky auth token.");

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

  const loadStatus = useCallback(async () => {
    if (!isLoaded || !isSignedIn) return;
    setLoading(true);
    setError(null);
    try {
      const response = await authedFetch(`${API_ORIGIN}/auth/twf/status`);
      if (!response.ok) {
        const apiError = await readApiError(response);
        throw new Error(apiError || `Unable to load TWF status (${response.status})`);
      }
      const body = await response.json();
      setStatus(readTwfStatus(body));
    } catch (err) {
      setStatus(null);
      setError((err as Error).message || "Unable to load TWF connection status.");
    } finally {
      setLoading(false);
    }
  }, [authedFetch, isLoaded, isSignedIn]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    const twfResult = searchParams.get("twf");
    if (searchParams.get("twf_connected") !== "true" && twfResult !== "linked" && twfResult !== "error") return;

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("twf_connected");
    nextParams.delete("twf");
    const message = nextParams.get("twf_message");
    nextParams.delete("twf_message");

    if (twfResult === "error") {
      setError(message || "TWF connection failed. Please try again.");
    } else {
      setSuccess("TWF account connected.");
      void loadStatus();
    }

    setSearchParams(nextParams, { replace: true });
  }, [loadStatus, searchParams, setSearchParams]);

  const handleConnect = useCallback(async () => {
    setAction("connect");
    setError(null);
    setSuccess(null);
    try {
      const response = await authedFetch(`${API_ORIGIN}/auth/twf/start?${new URLSearchParams({ return_to: "/account" })}`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        const apiError = await readApiError(response);
        throw new Error(apiError || `Unable to start TWF connection (${response.status})`);
      }
      const body = (await response.json()) as { authorization_url?: unknown; authorize_url?: unknown };
      const authorizationUrl =
        typeof body.authorization_url === "string"
          ? body.authorization_url
          : typeof body.authorize_url === "string"
            ? body.authorize_url
            : "";
      if (!authorizationUrl.trim()) throw new Error("TWF authorization URL was not returned.");
      window.location.assign(authorizationUrl);
    } catch (err) {
      setError((err as Error).message || "Unable to start TWF connection.");
      setAction(null);
    }
  }, [authedFetch]);

  const handleDisconnect = useCallback(async () => {
    setAction("disconnect");
    setError(null);
    setSuccess(null);
    try {
      const response = await authedFetch(`${API_ORIGIN}/api/v4/user/connections/twf`, { method: "DELETE" });
      if (!response.ok) {
        const apiError = await readApiError(response);
        throw new Error(apiError || `Unable to disconnect TWF (${response.status})`);
      }
      setStatus({ connected: false, twf_username: null });
      setSuccess("TWF account disconnected.");
    } catch (err) {
      setError((err as Error).message || "Unable to disconnect TWF.");
    } finally {
      setAction(null);
    }
  }, [authedFetch]);

  return (
    <section className="text-white">
      <div className="border-b border-sky-200/[0.06] pb-5">
        <div>
          <h2 className="text-lg font-semibold text-white">Connected accounts</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
            Link The Weather Forums to share CartoSky maps and posts with your forum account.
          </p>
        </div>
      </div>

      <div className="border-b border-sky-200/[0.06] py-5">
        <div className="grid gap-4 sm:grid-cols-[12rem_1fr_auto] sm:items-start">
          <div className="text-sm font-medium text-white">The Weather Forums</div>
          <div>
            {loading ? (
              <p className="text-sm text-slate-400">Checking connection status...</p>
            ) : status?.connected ? (
              <p className="text-sm text-slate-300">
                Connected as <span className="font-medium text-cyan-200">{status.twf_username || "TWF user"}</span>
              </p>
            ) : (
              <p className="text-sm text-slate-400">Not connected</p>
            )}
          </div>

          <div className="flex flex-wrap gap-2 sm:justify-end">
            <button
              type="button"
              onClick={loadStatus}
              disabled={loading || action !== null}
              className="inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-cyan-200 transition hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            {status?.connected ? (
              <button
                type="button"
                onClick={handleDisconnect}
                disabled={loading || action !== null}
                className="inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-rose-200 transition hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {action === "disconnect" ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Unlink className="h-4 w-4" />}
                Disconnect
              </button>
            ) : (
              <button
                type="button"
                onClick={handleConnect}
                disabled={loading || action !== null}
                className="inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-cyan-200 transition hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {action === "connect" ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                Connect
              </button>
            )}
          </div>
        </div>
      </div>

      {success ? (
        <div className="mt-4 flex items-start gap-2 text-sm text-emerald-100">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{success}</span>
        </div>
      ) : null}

      {error ? (
        <div className="mt-4 flex items-start gap-2 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}

function SubscriptionSection() {
  const { user } = useUser();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plan = planFromPublicMetadata(user?.publicMetadata);

  const handleManageSubscription = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = await createPortalSession("/account#/subscription");
      window.location.assign(url);
    } catch (err) {
      setError((err as Error).message || "Unable to open Stripe Customer Portal.");
      setLoading(false);
    }
  }, []);

  return (
    <section className="text-white">
      <div className="border-b border-sky-200/[0.06] pb-5">
        <div>
          <h2 className="text-lg font-semibold text-white">Subscription</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
            Stripe manages your CartoSky subscription while Clerk continues to handle authentication.
          </p>
        </div>
      </div>

      <div className="border-b border-sky-200/[0.06] py-5">
        <div className="grid gap-4 sm:grid-cols-[12rem_1fr_auto] sm:items-start">
          <div className="text-sm font-medium text-white">Current plan</div>
          <div>
            <p className="text-sm text-slate-300">{plan === "pro" ? "Pro" : "Free"}</p>
          </div>

          <div className="flex flex-wrap gap-2 sm:justify-end">
            {plan === "pro" ? (
              <button
                type="button"
                onClick={() => void handleManageSubscription()}
                disabled={loading}
                className="inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-cyan-200 transition hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <CreditCard className="h-4 w-4" />}
                Manage Subscription
              </button>
            ) : (
              <Link
                to="/pricing"
                className="inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-cyan-200 transition hover:text-cyan-100"
              >
                Upgrade
              </Link>
            )}
          </div>
        </div>
      </div>

      {error ? (
        <div className="mt-4 flex items-start gap-2 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}

export default function Account() {
  return (
    <div className="relative min-h-[calc(100dvh-9rem)] overflow-y-auto bg-[#04101e] px-4 py-8 md:px-6 md:py-12">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[24rem] w-[24rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
      </div>

      <div className="cartosky-clerk-profile relative mx-auto max-w-5xl">
        <Show when="signed-in">
          <>
            <TwfConnectedAccountReturn />
            <UserProfile appearance={clerkAppearance}>
              {billingEnabled ? (
                <UserProfile.Page label="Subscription" labelIcon={<CreditCard className="h-4 w-4" />} url="subscription">
                  <SubscriptionSection />
                </UserProfile.Page>
              ) : null}
              <UserProfile.Page label="Integrations" labelIcon={<Plug className="h-4 w-4" />} url="integrations">
                <TwfConnectionSection />
              </UserProfile.Page>
            </UserProfile>
          </>
        </Show>
        <Show when="signed-out">
          <Navigate to="/login" replace />
        </Show>
      </div>
    </div>
  );
}