import { useClerk, useReverification, useUser } from "@clerk/react";
import { isReverificationCancelledError } from "@clerk/react/errors";
import { AlertTriangle, CheckCircle2, CreditCard, Eye, EyeOff, Link2, Lock, Plug, RefreshCw, Unlink, User } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { createPortalSession } from "@/lib/billing";
import { billingEnabled, planFromPublicMetadata } from "@/lib/entitlements";
import { API_ORIGIN } from "@/lib/config";
import { useAuthFetch } from "@/hooks/useAuthFetch";
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
  const authedFetch = useAuthFetch();
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState<TwfConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<"connect" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
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
  }, [authedFetch]);

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

const INPUT_CLASS =
  "w-full rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-cyan-300/40 focus:outline-none focus:ring-0";
const LABEL_CLASS = "block text-xs font-medium text-slate-400 mb-1";
const SAVE_BUTTON_CLASS =
  "inline-flex items-center gap-2 rounded-lg bg-cyan-500/[0.15] border border-cyan-300/25 px-4 py-2 text-sm font-medium text-cyan-200 transition hover:bg-cyan-500/[0.22] disabled:opacity-50";
const CANCEL_BUTTON_CLASS =
  "inline-flex items-center gap-2 rounded-lg border border-white/10 px-4 py-2 text-sm font-medium text-slate-400 transition hover:text-white";

type SaveStatus = "idle" | "saving" | "success" | "error";

function ProfileSection() {
  const { user } = useUser();
  const [editing, setEditing] = useState(false);
  const [firstName, setFirstName] = useState(user?.firstName ?? "");
  const [lastName, setLastName] = useState(user?.lastName ?? "");
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (saveStatus !== "success") return;
    const timer = setTimeout(() => setSaveStatus("idle"), 2500);
    return () => clearTimeout(timer);
  }, [saveStatus]);

  const initials = `${user?.firstName?.[0] ?? ""}${user?.lastName?.[0] ?? ""}`.toUpperCase() || "?";
  const fullName = [user?.firstName, user?.lastName].filter(Boolean).join(" ") || user?.fullName || "—";
  const primaryEmail = user?.primaryEmailAddress?.emailAddress ?? null;
  const externalAccounts = user?.externalAccounts ?? [];

  const startEditing = () => {
    setFirstName(user?.firstName ?? "");
    setLastName(user?.lastName ?? "");
    setSaveStatus("idle");
    setSaveError(null);
    setEditing(true);
  };

  const cancelEditing = () => {
    setFirstName(user?.firstName ?? "");
    setLastName(user?.lastName ?? "");
    setSaveStatus("idle");
    setSaveError(null);
    setEditing(false);
  };

  // Wrapped so Clerk can pop its step-up verification modal and retry when the
  // session needs reverification, instead of surfacing a raw 403.
  const updateProfile = useReverification((params: { firstName: string; lastName: string }) => {
    if (!user) throw new Error("Not signed in.");
    return user.update(params);
  });

  const handleSave = async () => {
    if (!user) return;
    setSaveStatus("saving");
    setSaveError(null);
    try {
      await updateProfile({ firstName: firstName.trim(), lastName: lastName.trim() });
      setSaveStatus("success");
      setEditing(false);
    } catch (err) {
      if (isReverificationCancelledError(err)) {
        setSaveStatus("idle");
        return;
      }
      setSaveStatus("error");
      setSaveError((err as Error).message || "Unable to update profile.");
    }
  };

  return (
    <section>
      <h2 className="text-lg font-semibold text-white mb-6">Profile details</h2>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] divide-y divide-white/[0.06]">
        <div className="p-5">
          {editing ? (
            <div className="flex flex-col gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="profile-first-name" className={LABEL_CLASS}>
                    First name
                  </label>
                  <input
                    id="profile-first-name"
                    type="text"
                    value={firstName}
                    onChange={(event) => setFirstName(event.target.value)}
                    className={INPUT_CLASS}
                  />
                </div>
                <div>
                  <label htmlFor="profile-last-name" className={LABEL_CLASS}>
                    Last name
                  </label>
                  <input
                    id="profile-last-name"
                    type="text"
                    value={lastName}
                    onChange={(event) => setLastName(event.target.value)}
                    className={INPUT_CLASS}
                  />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => void handleSave()} disabled={saveStatus === "saving"} className={SAVE_BUTTON_CLASS}>
                  {saveStatus === "saving" ? <RefreshCw className="h-4 w-4 animate-spin" /> : null}
                  Save
                </button>
                <button type="button" onClick={cancelEditing} className={CANCEL_BUTTON_CLASS}>
                  Cancel
                </button>
              </div>
              {saveStatus === "error" && saveError ? (
                <div className="flex items-start gap-2 text-sm text-rose-100">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{saveError}</span>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-cyan-300/[0.2] bg-cyan-300/[0.15] text-sm font-semibold text-cyan-200">
                {initials}
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-white">{fullName}</div>
                {primaryEmail ? <div className="text-xs text-slate-400">{primaryEmail}</div> : null}
              </div>
              <button type="button" onClick={startEditing} className="ml-auto text-sm text-cyan-300 hover:text-cyan-200">
                Edit
              </button>
            </div>
          )}
          {saveStatus === "success" ? (
            <div className="mt-3 flex items-center gap-2 text-sm text-emerald-100">
              <CheckCircle2 className="h-4 w-4" />
              <span>Profile updated</span>
            </div>
          ) : null}
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

function PasswordField({
  id,
  label,
  value,
  onChange,
  show,
  onToggleShow,
  autoComplete,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  onToggleShow: () => void;
  autoComplete?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Chromium breaks click-to-place-caret after an input's type is swapped
  // between password/text; re-focusing and restoring the caret resets it.
  // Only do this when the field was already focused: imposing focus/selection
  // on an inactive field makes iOS WebKit select the entire value on the next
  // tap. Restore a collapsed caret (not a range) for the same reason.
  const handleToggle = () => {
    const input = inputRef.current;
    const wasFocused = !!input && document.activeElement === input;
    const caret = input?.selectionEnd ?? null;
    onToggleShow();
    if (!wasFocused) return;
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus({ preventScroll: true });
      if (caret !== null) {
        el.setSelectionRange(caret, caret);
      }
    });
  };

  return (
    <div>
      <label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </label>
      <div className="relative flex items-center">
        <input
          ref={inputRef}
          id={id}
          type={show ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          className={`${INPUT_CLASS} pr-10`}
        />
        <button
          type="button"
          onMouseDown={(event) => event.preventDefault()}
          onClick={handleToggle}
          aria-label={show ? "Hide password" : "Show password"}
          className="absolute right-3 text-slate-500 hover:text-slate-300"
        >
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}

function SecuritySection() {
  const { user } = useUser();
  const clerk = useClerk();
  const [passwordEditing, setPasswordEditing] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [passwordSaveStatus, setPasswordSaveStatus] = useState<SaveStatus>("idle");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Awaited<ReturnType<NonNullable<typeof user>["getSessions"]>> | null>(null);

  useEffect(() => {
    user
      ?.getSessions?.()
      .then((s) => setSessions(s))
      .catch(() => setSessions([]));
  }, [user]);

  useEffect(() => {
    if (passwordSaveStatus !== "success") return;
    const timer = setTimeout(() => setPasswordSaveStatus("idle"), 2500);
    return () => clearTimeout(timer);
  }, [passwordSaveStatus]);

  const resetPasswordForm = () => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setShowCurrent(false);
    setShowNew(false);
    setShowConfirm(false);
  };

  const cancelPasswordEditing = () => {
    resetPasswordForm();
    setPasswordEditing(false);
    setPasswordError(null);
    setPasswordSaveStatus("idle");
  };

  // Latest current-password value for the reverification handler below, which
  // may run from a closure created on an earlier render.
  const currentPasswordRef = useRef("");
  currentPasswordRef.current = currentPassword;

  // Password changes are a Clerk "sensitive action": when the session's last
  // verification is too old, the API returns session_reverification_required.
  // Instead of letting useReverification pop its modal (which would ask for the
  // current password the user just typed), satisfy the step-up check silently
  // by verifying the session with that same password.
  const updatePassword = useReverification(
    (params: { currentPassword: string; newPassword: string }) => {
      if (!user) throw new Error("Not signed in.");
      return user.updatePassword(params);
    },
    {
      onNeedsReverification: async ({ complete, cancel, level }) => {
        const session = clerk.session;
        const password = currentPasswordRef.current;
        if (!session || !password) {
          setPasswordError("Additional verification is required. Please sign out and back in, then try again.");
          cancel();
          return;
        }
        try {
          await session.startVerification({ level: level ?? "first_factor" });
          const verification = await session.attemptFirstFactorVerification({ strategy: "password", password });
          if (verification.status === "complete") {
            complete();
            return;
          }
          setPasswordError(
            verification.status === "needs_second_factor"
              ? "This change requires two-factor verification. Please sign out and back in, then try again."
              : "Additional verification is required. Please sign out and back in, then try again."
          );
          cancel();
        } catch (err) {
          setPasswordError((err as Error).message || "Current password is incorrect.");
          cancel();
        }
      },
    }
  );

  const handlePasswordSave = async () => {
    if (!user) return;
    setPasswordError(null);
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters");
      return;
    }
    setPasswordSaveStatus("saving");
    try {
      await updatePassword({ currentPassword, newPassword });
      resetPasswordForm();
      setPasswordEditing(false);
      setPasswordSaveStatus("success");
    } catch (err) {
      if (isReverificationCancelledError(err)) {
        setPasswordSaveStatus("idle");
        return;
      }
      setPasswordSaveStatus("error");
      setPasswordError((err as Error).message || "Unable to update password.");
    }
  };

  return (
    <section>
      <h2 className="text-lg font-semibold text-white mb-6">Security</h2>
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] divide-y divide-white/[0.06]">
        <div className="p-5">
          <div className="text-xs font-medium uppercase tracking-widest text-slate-500 mb-3">Password</div>
          {user?.passwordEnabled !== true ? (
            <p className="text-sm text-slate-500">Password sign-in is not set up for your account.</p>
          ) : passwordEditing ? (
            <div className="flex flex-col gap-4">
              <PasswordField
                id="security-current-password"
                label="Current password"
                value={currentPassword}
                onChange={setCurrentPassword}
                show={showCurrent}
                onToggleShow={() => setShowCurrent((v) => !v)}
                autoComplete="current-password"
              />
              <PasswordField
                id="security-new-password"
                label="New password"
                value={newPassword}
                onChange={setNewPassword}
                show={showNew}
                onToggleShow={() => setShowNew((v) => !v)}
                autoComplete="new-password"
              />
              <PasswordField
                id="security-confirm-password"
                label="Confirm new password"
                value={confirmPassword}
                onChange={setConfirmPassword}
                show={showConfirm}
                onToggleShow={() => setShowConfirm((v) => !v)}
                autoComplete="new-password"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handlePasswordSave()}
                  disabled={passwordSaveStatus === "saving"}
                  className={SAVE_BUTTON_CLASS}
                >
                  {passwordSaveStatus === "saving" ? <RefreshCw className="h-4 w-4 animate-spin" /> : null}
                  Save
                </button>
                <button type="button" onClick={cancelPasswordEditing} className={CANCEL_BUTTON_CLASS}>
                  Cancel
                </button>
              </div>
              {passwordError ? (
                <div className="flex items-start gap-2 text-sm text-rose-100">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{passwordError}</span>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center">
              <span className="text-slate-400 text-sm tracking-widest">••••••••</span>
              <button
                type="button"
                onClick={() => setPasswordEditing(true)}
                className="text-sm text-cyan-300 hover:text-cyan-200 ml-auto"
              >
                Update password
              </button>
            </div>
          )}
          {passwordSaveStatus === "success" ? (
            <div className="mt-3 flex items-center gap-2 text-sm text-emerald-100">
              <CheckCircle2 className="h-4 w-4" />
              <span>Password updated</span>
            </div>
          ) : null}
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
                    {session.id === clerk.session?.id ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-200">
                        This device
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-400">Manage active sessions in your profile</p>
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
        <TwfConnectedAccountReturn />

            {/* Page header */}
            <div className="mb-8">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-200/70">CartoSky</p>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight text-white md:text-3xl">Account</h1>
              <p className="mt-2 text-sm text-slate-400">Manage your profile, security, and connected services.</p>
            </div>

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
      </div>
    </div>
  );
}
