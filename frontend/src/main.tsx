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
    modalContent: "border border-white/10 bg-[#0b0e15] text-white shadow-[0_24px_100px_rgba(0,0,0,0.72)]",
    modalCloseButton: "text-slate-500 hover:text-white",
    userProfileRoot: "bg-[#0b0e15] text-white",
    userProfileCard: "bg-[#0b0e15] text-white shadow-none",
    userProfilePage: "bg-[#0b0e15] text-white",
    pageScrollBox: "bg-[#0b0e15] text-white",
    navbar: "border-r border-white/10 bg-[#090c12] text-white",
    navbarHeader: "text-white",
    navbarTitle: "text-white",
    navbarSubtitle: "text-slate-400",
    navbarButton: "text-slate-400 hover:bg-cyan-300/[0.08] hover:text-white data-[active=true]:bg-cyan-300/[0.10] data-[active=true]:text-cyan-300",
    navbarButtonIcon: "text-slate-500 group-data-[active=true]:text-cyan-300 group-hover:text-cyan-200",
    navbarButtonText: "text-inherit",
    profileSectionTitleText: "text-slate-200",
    profileSectionContent: "text-slate-300",
    profileSectionPrimaryButton: "text-cyan-300 hover:text-cyan-200",
    profileSectionItem: "border-white/10 text-slate-300",
    profileSectionItemTitle: "text-white",
    profileSectionItemSubtitle: "text-slate-400",
    profileSectionItemValue: "text-slate-300",
    profileSectionItemValueText: "text-slate-300",
    profileSectionItemSecondaryIdentifier: "text-slate-400",
    accordionTriggerButton: "text-slate-200 hover:text-white",
    formFieldSuccessText: "text-emerald-300",
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
