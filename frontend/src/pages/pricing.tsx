import { PricingTable } from "@clerk/react";
import { Link } from "react-router-dom";

import { billingEnabled, pricingPreviewEnabled } from "@/lib/entitlements";

const pricingAppearance = {
  variables: {
    colorBackground: "#13161f",
    colorInput: "#161922",
    colorInputForeground: "#f8fafc",
    colorForeground: "#f8fafc",
    colorMutedForeground: "#94a3b8",
    colorNeutral: "#ffffff",
    colorPrimary: "#22d3ee",
    colorDanger: "#fb7185",
    borderRadius: "0.5rem",
  },
};

export default function Pricing() {
  const pricingAvailable = billingEnabled || pricingPreviewEnabled;

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
    <section className="mx-auto max-w-6xl overflow-y-auto py-8">
      <div className="mb-7 space-y-2">
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-200/70">CartoSky Pro</p>
        <h1 className="text-3xl font-semibold tracking-normal text-white md:text-4xl">CartoSky Pro</h1>
      </div>
      <PricingTable
        appearance={pricingAppearance}
        checkoutProps={{ appearance: pricingAppearance }}
        newSubscriptionRedirectUrl="/viewer"
      />
    </section>
  );
}
