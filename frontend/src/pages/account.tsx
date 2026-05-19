import { useAuth, Show, UserProfile } from "@clerk/react";
import { AlertTriangle, CheckCircle2, Link2, RefreshCw, Unlink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { clerkAppearance } from "@/lib/clerk-appearance";
import { API_ORIGIN } from "@/lib/config";
import { clerkJwtTemplate } from "@/lib/admin-api";

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

function TwfConnectedAccount() {
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
    if (searchParams.get("twf_connected") !== "true") return;
    setSuccess("TWF account connected.");
    void loadStatus();
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("twf_connected");
    setSearchParams(nextParams, { replace: true });
  }, [loadStatus, searchParams, setSearchParams]);

  const handleConnect = useCallback(async () => {
    setAction("connect");
    setError(null);
    setSuccess(null);
    try {
      const response = await authedFetch(`${API_ORIGIN}/auth/twf/start?${new URLSearchParams({ return_to: "/account?twf_connected=true" })}`, { method: "GET" });
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
    <section className="mt-6 rounded-2xl border border-sky-200/12 bg-[#08182a]/95 p-5 text-white shadow-[0_18px_70px_rgba(0,0,0,0.38)] backdrop-blur-xl">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-white">Connected Accounts</div>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-300">
            Link The Weather Forums to share CartoSky maps and posts with your forum account.
          </p>
        </div>
        <button
          type="button"
          onClick={loadStatus}
          disabled={loading || action !== null}
          className="inline-flex items-center justify-center gap-2 rounded-lg border border-sky-200/12 bg-white/[0.04] px-3 py-2 text-sm font-medium text-slate-200 transition hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="mt-5 rounded-xl border border-sky-200/10 bg-[#061323]/80 p-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-cyan-300/18 bg-cyan-300/[0.08] text-cyan-200">
              <Link2 className="h-4 w-4" />
            </div>
            <div>
              <div className="text-sm font-semibold text-white">The Weather Forums</div>
              {loading ? (
                <p className="mt-1 text-sm text-slate-400">Checking connection status...</p>
              ) : status?.connected ? (
                <p className="mt-1 text-sm text-slate-300">
                  Connected as <span className="font-medium text-cyan-200">{status.twf_username || "TWF user"}</span>
                </p>
              ) : (
                <p className="mt-1 text-sm text-slate-400">Not connected</p>
              )}
            </div>
          </div>

          {status?.connected ? (
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={loading || action !== null}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/[0.08] px-3 py-2 text-sm font-medium text-rose-100 transition hover:bg-rose-300/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {action === "disconnect" ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Unlink className="h-4 w-4" />}
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              onClick={handleConnect}
              disabled={loading || action !== null}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#67e8f9_0%,#38bdf8_100%)] px-3 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {action === "connect" ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              Connect
            </button>
          )}
        </div>
      </div>

      {success ? (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-emerald-300/18 bg-emerald-300/[0.08] px-3 py-2 text-sm text-emerald-100">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{success}</span>
        </div>
      ) : null}

      {error ? (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-rose-300/18 bg-rose-300/[0.08] px-3 py-2 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  );
}

export default function Account() {
  return (
    <div className="relative min-h-[calc(100vh-9rem)] overflow-hidden bg-[#04101e] px-4 py-8 md:px-6 md:py-12">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[24rem] w-[24rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
      </div>

      <div className="cartosky-clerk-profile relative mx-auto max-w-5xl">
        <Show when="signed-in">
          <>
            <UserProfile routing="path" path="/account" appearance={clerkAppearance} />
            <TwfConnectedAccount />
          </>
        </Show>
        <Show when="signed-out">
          <Navigate to="/login" replace />
        </Show>
      </div>
    </div>
  );
}