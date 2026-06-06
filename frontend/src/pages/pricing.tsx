import { useMemo, useState } from "react";
import { useUser } from "@clerk/react";
import { AlertTriangle, ArrowRight, Check, CreditCard, Sparkles } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { PROTECTED_PRODUCTS } from "@/config/proFeatures";
import { createCheckoutSession, createPortalSession } from "@/lib/billing";
import { planFromPublicMetadata } from "@/lib/entitlements";
import { billingEnabled, pricingPreviewEnabled } from "@/lib/entitlements";

const FREE_PLAN_PRODUCTS = ["GFS", "HRRR", "NAM", "MRMS", "NBM"];

function checkoutSuccessPath(): string {
  return "/pricing?checkout=success";
}

function checkoutCancelPath(): string {
  return "/pricing?checkout=cancel";
}

function subscriptionReturnPath(): string {
  return "/account#/subscription";
}

export default function Pricing() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { isLoaded, isSignedIn, user } = useUser();
  const [action, setAction] = useState<"checkout" | "portal" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pricingAvailable = billingEnabled || pricingPreviewEnabled;
  const plan = planFromPublicMetadata(user?.publicMetadata);
  const protectedProductLabels = useMemo(
    () => [...new Set(Object.values(PROTECTED_PRODUCTS).map((product) => product.label))],
    []
  );
  const proPlanProducts = useMemo(
    () => [...new Set([...FREE_PLAN_PRODUCTS, ...protectedProductLabels])],
    [protectedProductLabels]
  );
  const checkoutState = searchParams.get("checkout");

  async function handlePrimaryAction(): Promise<void> {
    setError(null);

    if (!billingEnabled) {
      setError("Billing is not active in this environment.");
      return;
    }

    if (!isLoaded) {
      setError("Checking CartoSky sign-in status.");
      return;
    }

    if (!isSignedIn) {
      navigate(`/login?redirect_url=${encodeURIComponent("/pricing")}`);
      return;
    }

    try {
      if (plan === "pro") {
        setAction("portal");
        const url = await createPortalSession(subscriptionReturnPath());
        window.location.assign(url);
        return;
      }

      setAction("checkout");
      const url = await createCheckoutSession(checkoutSuccessPath(), checkoutCancelPath());
      window.location.assign(url);
    } catch (err) {
      setError((err as Error).message || "Unable to start CartoSky billing.");
      setAction(null);
    }
  }

  if (!pricingAvailable) {
    return (
      <section className="mx-auto flex min-h-[calc(100svh-14rem)] max-w-3xl flex-col items-start justify-center gap-5 py-12">
        <div className="space-y-3">
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-200/70">CartoSky Pro</p>
          <h1 className="text-3xl font-semibold tracking-normal text-white md:text-4xl">Pricing not yet available</h1>
          <p className="max-w-2xl text-sm leading-6 text-white/62">
            Pro billing is disabled for this environment. The map viewer remains fully accessible.
          </p>
        </div>
        <Link
          to="/viewer"
          className="inline-flex rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-4 py-2 text-sm font-semibold text-slate-950 transition hover:brightness-105"
        >
          Open viewer
        </Link>
      </section>
    );
  }

  return (
    <section className="relative mx-auto max-w-6xl overflow-hidden py-10">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-[10%] top-0 h-72 w-72 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="absolute bottom-0 right-[6%] h-80 w-80 rounded-full bg-sky-500/10 blur-3xl" />
      </div>

      <div className="relative space-y-8">
        <div className="max-w-3xl space-y-3">
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-200/70">CartoSky Pro</p>
          <h1 className="text-3xl font-semibold tracking-normal text-white md:text-5xl">Forecast access that stays inside CartoSky.</h1>
          <p className="max-w-2xl text-sm leading-6 text-slate-300 md:text-base">
            Clerk still handles authentication. Stripe now handles Checkout, subscriptions, and the customer portal while CartoSky keeps backend product enforcement authoritative.
          </p>
        </div>

        {checkoutState === "success" ? (
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-4 py-2 text-sm text-emerald-100">
            <Check className="h-4 w-4" />
            Checkout completed. Refresh your session if Pro access does not appear immediately.
          </div>
        ) : null}

        {checkoutState === "cancel" ? (
          <div className="inline-flex items-center gap-2 rounded-full border border-amber-300/25 bg-amber-300/10 px-4 py-2 text-sm text-amber-100">
            <AlertTriangle className="h-4 w-4" />
            Checkout was canceled. Your current plan has not changed.
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-[1fr_1.1fr]">
          <article className="rounded-[28px] border border-white/10 bg-white/[0.045] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.36)] backdrop-blur-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.22em] text-white/45">Free</p>
                <h2 className="mt-2 text-2xl font-semibold text-white">CartoSky Free</h2>
                <p className="mt-2 text-sm text-slate-300">Core forecast and observed products stay available with no billing enabled.</p>
              </div>
              <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-sm font-medium text-white/80">$0</div>
            </div>

            <ul className="mt-6 space-y-3 text-sm text-slate-200">
              {FREE_PLAN_PRODUCTS.map((product) => (
                <li key={product} className="flex items-center gap-3">
                  <Check className="h-4 w-4 text-cyan-200" />
                  <span>{product}</span>
                </li>
              ))}
            </ul>
          </article>

          <article className="relative overflow-hidden rounded-[28px] border border-cyan-200/20 bg-[linear-gradient(180deg,rgba(8,27,43,0.96)_0%,rgba(4,16,30,0.98)_100%)] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.46)]">
            <div className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/55 to-transparent" />
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full border border-cyan-200/20 bg-cyan-300/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-100">
                  <Sparkles className="h-3.5 w-3.5" />
                  CartoSky Pro
                </div>
                <h2 className="mt-3 text-2xl font-semibold text-white">Full protected product access</h2>
                <p className="mt-2 text-sm text-slate-300">
                  Stripe manages the subscription. CartoSky reads your plan from Clerk public metadata and keeps backend 403 enforcement intact.
                </p>
              </div>
              <div className="text-right">
                <div className="text-3xl font-semibold text-white">$7.50</div>
                <div className="text-xs uppercase tracking-[0.18em] text-cyan-100/70">per month</div>
              </div>
            </div>

            <ul className="mt-6 grid gap-3 text-sm text-slate-100 sm:grid-cols-2">
              {proPlanProducts.map((product) => (
                <li key={product} className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] px-3 py-2">
                  <Check className="h-4 w-4 text-cyan-200" />
                  <span>{product}</span>
                </li>
              ))}
            </ul>

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => void handlePrimaryAction()}
                disabled={action !== null || !billingEnabled}
                className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-5 py-3 text-sm font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.22)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {plan === "pro" ? <CreditCard className="h-4 w-4" /> : <ArrowRight className="h-4 w-4" />}
                {billingEnabled
                  ? plan === "pro"
                    ? action === "portal"
                      ? "Opening portal..."
                      : "Manage Subscription"
                    : action === "checkout"
                      ? "Opening Checkout..."
                      : isSignedIn
                        ? "Upgrade to CartoSky Pro"
                        : "Sign in to upgrade"
                  : "Billing unavailable"}
              </button>

              <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-white/70">
                Current plan: {plan === "pro" ? "Pro" : "Free"}
              </div>
            </div>

            {!billingEnabled ? (
              <p className="mt-3 text-sm text-slate-400">Pricing preview is enabled, but billing checkout is disabled in this environment.</p>
            ) : null}

            {error ? (
              <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-300/15 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            ) : null}
          </article>
        </div>
      </div>
    </section>
  );
}
