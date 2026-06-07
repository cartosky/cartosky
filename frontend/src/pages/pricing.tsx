import { useMemo, useState } from "react";
import { useUser } from "@clerk/react";
import { AlertTriangle, ArrowRight, Check, CreditCard, Minus } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { PROTECTED_PRODUCTS } from "@/config/proFeatures";
import { createCheckoutSession, createPortalSession } from "@/lib/billing";
import { planFromPublicMetadata } from "@/lib/entitlements";
import { billingEnabled, pricingPreviewEnabled } from "@/lib/entitlements";

const FREE_PLAN_PRODUCTS = ["GFS", "HRRR", "NAM", "MRMS", "NBM"];

function checkoutSuccessPath(): string {
  return "/checkout-success";
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
    <section className="mx-auto max-w-4xl py-16">
      <div className="space-y-8">
        {/* Hero Section */}
        <div className="mx-auto max-w-2xl space-y-4 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-200/70">CARTOSKY PRO</p>
          <h1 className="text-4xl font-semibold tracking-tight text-white md:text-5xl">
            Unlock the full forecast.
          </h1>
          <p className="text-base text-slate-300">
            Pro gives you access to premium models and products.
          </p>
          <div className="pt-2">
            <div className="text-3xl font-semibold text-white">Starting at $7.50 / month</div>
          </div>
        </div>

        {/* Checkout State Banners */}
        {checkoutState === "success" ? (
          <div className="mx-auto inline-flex items-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-2.5 text-sm text-emerald-100">
            <Check className="h-4 w-4" />
            You're on CartoSky Pro. Sign out and back in if access doesn't appear immediately.
          </div>
        ) : null}

        {checkoutState === "cancel" ? (
          <div className="mx-auto inline-flex items-center gap-2 rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-2.5 text-sm text-amber-100">
            <AlertTriangle className="h-4 w-4" />
            Checkout canceled. Your plan hasn't changed.
          </div>
        ) : null}

        {/* Primary CTA */}
        <div className="flex flex-col items-center gap-4">
          <button
            type="button"
            onClick={() => void handlePrimaryAction()}
            disabled={action !== null || !billingEnabled}
            className="inline-flex items-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-6 py-3.5 text-base font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.22)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {plan === "pro" ? <CreditCard className="h-5 w-5" /> : <ArrowRight className="h-5 w-5" />}
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

          {/* Current Plan Indicator */}
          <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-white/70">
            Current plan: {plan === "pro" ? "Pro" : "Free"}
          </div>

          {!billingEnabled ? (
            <p className="text-sm text-slate-400">Billing checkout is disabled in this environment.</p>
          ) : null}

          {error ? (
            <div className="flex items-start gap-2 rounded-lg border border-rose-300/15 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
        </div>

        {/* Feature Comparison Table */}
        <div className="mx-auto mt-16 max-w-2xl">
          <div className="overflow-hidden rounded-lg border border-white/10">
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="px-6 py-4 text-left text-sm font-semibold text-white/90">Feature</th>
                  <th className="px-6 py-4 text-center text-sm font-medium uppercase tracking-wider text-white/50">
                    Free
                  </th>
                  <th className="px-6 py-4 text-center text-sm font-medium uppercase tracking-wider text-cyan-200">
                    Pro
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">HRRR</td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">GFS</td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">NAM</td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">NBM</td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">MRMS Radar</td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/90">ECMWF</td>
                  <td className="px-6 py-4 text-center">
                    <Minus className="inline-block h-4 w-4 text-white/30" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
                <tr>
                  <td className="px-6 py-4 text-sm text-white/60">More coming soon</td>
                  <td className="px-6 py-4 text-center">
                    <Minus className="inline-block h-4 w-4 text-white/30" />
                  </td>
                  <td className="px-6 py-4 text-center">
                    <Check className="inline-block h-4 w-4 text-cyan-200" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
