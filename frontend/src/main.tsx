import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider, type ClerkProviderProps } from "@clerk/react";
import { BrowserRouter } from "react-router-dom";
import RouterApp from "./RouterApp";
import { FeedbackWidget } from "./components/FeedbackWidget";
import { PostHogBridge } from "./components/PostHogBridge";
import { FeedbackProvider } from "./lib/feedback-context";
import { initPostHogAnalytics } from "./lib/posthog";
import { initRumTelemetry } from "./lib/rum";
import { SiteLoadingProvider } from "./lib/site-loading";
import { BRAND_LOGO_SRC } from "./lib/branding";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/globals.css";

initRumTelemetry();
initPostHogAnalytics();

type EnvClerkProviderProps = Omit<ClerkProviderProps, "publishableKey">;
const EnvClerkProvider = ClerkProvider as React.ComponentType<EnvClerkProviderProps>;

const clerkAppearance: NonNullable<ClerkProviderProps["appearance"]> = {
  layout: {
    logoImageUrl: BRAND_LOGO_SRC,
  },
  variables: {
    colorBackground: "#101218",
    colorInputBackground: "#161922",
    colorInputText: "#f8fafc",
    colorText: "#f8fafc",
    colorTextSecondary: "#94a3b8",
    colorPrimary: "#22d3ee",
    colorDanger: "#fb7185",
    borderRadius: "0.5rem",
  },
  elements: {
    card: "border border-white/10 bg-transparent shadow-xl backdrop-blur-xl",
    logoBox: "mb-6 justify-center",
    logoImage: "!h-12 !max-h-12 !w-auto !max-w-[12rem]",
    headerTitle: "text-white",
    headerSubtitle: "text-slate-400",
    formFieldLabel: "text-slate-200",
    formFieldInput: "border-white/10 bg-[#161922] text-white placeholder:text-slate-500 focus:border-cyan-300/50 focus:ring-cyan-300/20",
    formButtonPrimary: "bg-cyan-500 text-slate-950 hover:bg-cyan-400 focus:ring-cyan-300/30",
    footer: "border-t border-white/10 bg-[#0b0e15]/80",
    footerAction: "text-slate-300",
    footerActionText: "text-slate-300",
    footerActionLink: "text-cyan-300 hover:text-cyan-200",
    footerPagesLink: "text-slate-500 hover:text-slate-300",
    dividerLine: "bg-white/10",
    dividerText: "text-slate-400",
    badge: "border border-cyan-300/20 bg-cyan-300/12 text-cyan-100 shadow-none",
    lastAuthenticationStrategyBadge: "border border-cyan-300/20 bg-cyan-300/12 text-cyan-100 shadow-none",
    socialButtonsBlockButton: "border-white/10 bg-white/[0.04] text-white hover:bg-cyan-300/[0.08]",
    alternativeMethodsBlockButton: "border-white/10 bg-white/[0.04] text-white hover:bg-cyan-300/[0.08]",
    otpCodeFieldInput: "border-white/10 bg-[#161922] text-white focus:border-cyan-300/50 focus:ring-cyan-300/20",
    userButtonPopoverCard: "border border-white/10 bg-[#0b0e15]/95 text-white shadow-[0_24px_80px_rgba(0,0,0,0.55)] backdrop-blur-xl",
    userButtonPopoverMain: "bg-transparent",
    userButtonPopoverActions: "bg-transparent",
    userButtonPopoverActionButton: "text-slate-200 hover:bg-cyan-300/[0.08] hover:text-white",
    userButtonPopoverActionButtonText: "text-slate-200 group-hover:text-white",
    userButtonPopoverActionButtonIcon: "text-slate-400 group-hover:text-cyan-200",
    userButtonPopoverFooter: "border-t border-white/10 bg-transparent text-slate-500",
    userPreviewMainIdentifier: "text-white",
    userPreviewSecondaryIdentifier: "text-slate-400",
    userPreviewTextContainer: "text-white",
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <EnvClerkProvider afterSignOutUrl="/" appearance={clerkAppearance}>
      <BrowserRouter>
        <SiteLoadingProvider>
          <FeedbackProvider>
            <PostHogBridge />
            <RouterApp />
            <FeedbackWidget />
          </FeedbackProvider>
        </SiteLoadingProvider>
      </BrowserRouter>
    </EnvClerkProvider>
  </React.StrictMode>
);
