import { Check, ArrowRight, User } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { PROTECTED_PRODUCTS } from "@/config/proFeatures";

export default function CheckoutSuccess() {
  const navigate = useNavigate();

  const protectedProductLabels = [
    ...new Set(Object.values(PROTECTED_PRODUCTS).map((product) => product.label)),
  ];

  return (
    <section className="mx-auto max-w-2xl py-16">
      <div className="space-y-8">
        {/* Header Row */}
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-emerald-400/15 ring-1 ring-emerald-400/30">
            <Check className="h-6 w-6 text-emerald-400" />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-emerald-300/70">
              SUBSCRIPTION CONFIRMED
            </p>
            <h1 className="text-xl font-medium text-white">Welcome to CartoSky Pro</h1>
          </div>
        </div>

        {/* Confirmation Card */}
        <div className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.02]">
          <div className="divide-y divide-white/5">
            <div className="flex items-center justify-between px-6 py-4">
              <span className="text-sm text-white/60">Plan</span>
              <span className="text-sm font-medium text-white">CartoSky Pro</span>
            </div>
            <div className="flex items-center justify-between px-6 py-4">
              <span className="text-sm text-white/60">Billing</span>
              <span className="text-sm font-medium text-white">$7.50 / month</span>
            </div>
            <div className="flex items-center justify-between px-6 py-4">
              <span className="text-sm text-white/60">Status</span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-400/15 px-2.5 py-1 text-xs font-medium text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
                Active
              </span>
            </div>
          </div>
        </div>

        {/* Access Section */}
        <div className="space-y-4 rounded-lg border border-white/10 bg-white/[0.02] px-6 py-5">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-white/50">
            ACCESS NOW INCLUDES
          </p>
          <div className="flex flex-wrap gap-2">
            {protectedProductLabels.map((label) => (
              <span
                key={label}
                className="inline-flex items-center rounded-full border border-cyan-200/25 bg-cyan-200/10 px-3 py-1.5 text-xs font-medium text-cyan-100"
              >
                {label}
              </span>
            ))}
            <span className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/40">
              + more coming soon
            </span>
          </div>
        </div>

        {/* Session Note */}
        <p className="text-sm leading-6 text-white/50">
          Sign out and back in if Pro access doesn't appear immediately. Manage your subscription
          anytime from your account.
        </p>

        {/* CTA Buttons */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/viewer")}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-6 py-3.5 text-base font-semibold text-slate-950 shadow-[0_18px_40px_rgba(35,196,255,0.22)] transition duration-200 hover:translate-y-[-1px] hover:brightness-105"
          >
            Open viewer
            <ArrowRight className="h-5 w-5" />
          </button>
          <button
            type="button"
            onClick={() => navigate("/account")}
            className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-5 py-3.5 text-base font-semibold text-white transition duration-200 hover:bg-white/10"
          >
            <User className="h-5 w-5" />
            Account
          </button>
        </div>
      </div>
    </section>
  );
}
