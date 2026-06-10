import { useAuth, Show, useClerk, useUser } from "@clerk/react";
import { AlertTriangle, CheckCircle2, CreditCard, Link2, Lock, Plug, RefreshCw, Unlink, User } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { createPortalSession } from "@/lib/billing";
import { billingEnabled, planFromPublicMetadata } from "@/lib/entitlements";
import { API_ORIGIN } from "@/lib/config";
import { clerkJwtTemplate } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

const INTEGRATIONS_HASH = "#/integrations";

type AccountTab = "profile" | "security" | "integrations";

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
          <h2 className="text-lg font-semibold text-white">Integrations</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
            Connect external services to CartoSky. Link The Weather Forums to share CartoSky maps and posts with your forum account.
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

function SubscriptionRow() {
  const { user } = useUser();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plan = planFromPublicMetadata(user?.publicMetadata);

  const handleManageSubscription = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = await createPortalSession("/account");
      window.location.assign(url);
    } catch (err) {
      setError((err as Error).message || "Unable to open Stripe Customer Portal.");
      setLoading(false);
    }
  }, []);

  return (
    <div className="p-5">
      <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Subscription</div>
      <div className="flex items-center text-sm text-slate-300">
        <CreditCard className="mr-2 h-4 w-4 shrink-0 text-slate-400" />
        <span>{plan === "pro" ? "Pro" : "Free"}</span>
        {plan === "pro" ? (
          <button
            type="button"
            onClick={() => void handleManageSubscription()}
            disabled={loading}
            className="ml-auto inline-flex items-center gap-2 text-sm text-cyan-300 hover:text-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : null}
            Manage
          </button>
        ) : (
          <Link to="/pricing" className="ml-auto text-sm text-cyan-300 hover:text-cyan-200">
            Upgrade
          </Link>
        )}
      </div>
      {error ? (
        <div className="mt-3 flex items-start gap-2 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
    </div>
  );
}

function ProfileSection() {
  const { user } = useUser();
  const clerk = useClerk();

  const initials = `${user?.firstName?.[0] ?? ""}${user?.lastName?.[0] ?? ""}`.toUpperCase() || "?";
  const fullName = [user?.firstName, user?.lastName].filter(Boolean).join(" ") || user?.fullName || "—";
  const primaryEmail = user?.primaryEmailAddress?.emailAddress ?? null;
  const externalAccounts = user?.externalAccounts ?? [];

  return (
    <section>
      <h2 className="text-lg font-semibold text-white mb-6">Profile details</h2>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] divide-y divide-white/[0.06]">
        <div className="flex items-center gap-3 p-5">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-cyan-300/[0.2] bg-cyan-300/[0.15] text-sm font-semibold text-cyan-200">
            {initials}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-white">{fullName}</div>
            {primaryEmail ? <div className="text-xs text-slate-400">{primaryEmail}</div> : null}
          </div>
          <a
            href="#"
            onClick={(event) => {
              event.preventDefault();
              clerk.openUserProfile();
            }}
            className="ml-auto text-sm text-cyan-300 hover:text-cyan-200"
          >
            Edit
          </a>
        </div>

        <div className="p-5">
          <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Email addresses</div>
          {primaryEmail ? (
            <div className="flex items-center text-sm text-slate-300">
              <span>{primaryEmail}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-200 ml-2">
                Primary
              </span>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No email addresses</p>
          )}
        </div>

        <div className="p-5">
          <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Connected accounts</div>
          {externalAccounts.length > 0 ? (
            <div className="flex flex-col gap-2">
              {externalAccounts.map((account) => (
                <div key={account.id} className="flex items-center gap-2 text-sm text-slate-300">
                  <span className="font-medium capitalize text-white">{account.provider}</span>
                  <span className="text-slate-400">{account.emailAddress || account.username || ""}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">None connected</p>
          )}
        </div>

        {billingEnabled ? <SubscriptionRow /> : null}
      </div>
    </section>
  );
}

type SessionInfo = {
  id: string;
  latestActivity?: {
    browserName?: string | null;
    browserVersion?: string | null;
    deviceType?: string | null;
    isMobile?: boolean | null;
  } | null;
};

function SecuritySection() {
  const { user } = useUser();
  const { sessionId } = useAuth();
  const clerk = useClerk();
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const load = async () => {
      try {
        const result = await user.getSessions();
        if (!cancelled && result) setSessions(result);
      } catch {
        if (!cancelled) setSessions(null);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [user]);

  return (
    <section>
      <h2 className="text-lg font-semibold text-white mb-6">Security</h2>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] divide-y divide-white/[0.06]">
        <div className="p-5">
          <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Password</div>
          <div className="flex items-center">
            <span className="text-slate-400 text-sm tracking-widest">••••••••</span>
            {/* Password changes require Clerk's hosted profile UI — open it rather than reimplementing the flow. */}
            <button
              type="button"
              onClick={() => clerk.openUserProfile()}
              className="text-sm text-cyan-300 hover:text-cyan-200 ml-auto"
            >
              Update password
            </button>
          </div>
        </div>

        <div className="p-5">
          <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Active devices</div>
          {sessions && sessions.length > 0 ? (
            <div className="flex flex-col gap-3">
              {sessions.map((session) => {
                const activity = session.latestActivity;
                const device = activity?.deviceType || (activity?.isMobile ? "Mobile device" : "Desktop device");
                const browser = [activity?.browserName, activity?.browserVersion].filter(Boolean).join(" ");
                return (
                  <div key={session.id} className="flex items-center gap-2 text-sm text-slate-300">
                    <span className="font-medium text-white">{device}</span>
                    {browser ? <span className="text-slate-400">{browser}</span> : null}
                    {session.id === sessionId ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-200">
                        This device
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex items-center text-sm text-slate-400">
              <span>Manage active sessions in your profile</span>
              <button
                type="button"
                onClick={() => clerk.openUserProfile()}
                className="text-sm text-cyan-300 hover:text-cyan-200 ml-auto"
              >
                Manage
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export default function Account() {
  const [activeTab, setActiveTab] = useState<AccountTab>("profile");

  useEffect(() => {
    const readTabFromHash = () => {
      const hash = window.location.hash;
      if (hash.includes("integrations")) {
        setActiveTab("integrations");
      } else if (hash.includes("security")) {
        setActiveTab("security");
      } else {
        setActiveTab("profile");
      }
    };
    readTabFromHash();
    window.addEventListener("hashchange", readTabFromHash);
    return () => window.removeEventListener("hashchange", readTabFromHash);
  }, []);

  const handleTabClick = (tab: AccountTab) => {
    history.replaceState(null, "", window.location.pathname);
    setActiveTab(tab);
  };

  return (
    <div className="relative min-h-svh w-full bg-[#04101e]">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[24rem] w-[24rem] -translate-x-1/2 rounded-full bg-sky-500/10 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-4xl px-4 py-8 md:px-6 md:py-12">
        {/* TEMP-PREVIEW: signed-in gate bypassed for layout screenshots — revert before commit */}
        {true ? (
          <>
            <TwfConnectedAccountReturn />

            {/* Mobile tab strip — hidden on md+ */}
            <div className="mb-5 md:hidden">
              <div className="flex rounded-xl border border-white/10 bg-white/[0.04] p-1 gap-1">
                {(["profile", "security", "integrations"] as AccountTab[]).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => handleTabClick(tab)}
                    className={cn(
                      "flex-1 rounded-lg px-3 py-2 text-xs font-medium capitalize transition-colors duration-150",
                      activeTab === tab
                        ? "bg-cyan-300/[0.12] text-cyan-200 border border-cyan-300/20"
                        : "text-slate-400 hover:text-white border border-transparent"
                    )}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>

            {/* Main layout — sidebar on md+, stacked on mobile */}
            <div className="md:flex md:gap-8">
              {/* Sidebar — hidden on mobile */}
              <aside className="hidden md:block md:w-48 md:shrink-0">
                <nav className="flex flex-col gap-0.5">
                  {(["profile", "security", "integrations"] as AccountTab[]).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => handleTabClick(tab)}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium capitalize transition-colors duration-150 text-left w-full",
                        activeTab === tab
                          ? "bg-cyan-300/[0.10] text-cyan-200"
                          : "text-slate-400 hover:bg-white/[0.05] hover:text-white"
                      )}
                    >
                      {tab === "profile" && <User className="h-4 w-4 shrink-0" />}
                      {tab === "security" && <Lock className="h-4 w-4 shrink-0" />}
                      {tab === "integrations" && <Plug className="h-4 w-4 shrink-0" />}
                      {tab}
                    </button>
                  ))}
                </nav>
              </aside>

              {/* Content area */}
              <div className="min-w-0 flex-1">
                {activeTab === "profile" && <ProfileSection />}
                {activeTab === "security" && <SecuritySection />}
                {activeTab === "integrations" && <TwfConnectionSection />}
              </div>
            </div>

            {/* Clerk branding */}
            <div className="mt-8 text-center">
              <span className="text-xs text-slate-600">Secured by Clerk</span>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
