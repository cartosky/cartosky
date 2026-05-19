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
import { clerkAppearance } from "./lib/clerk-appearance";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/globals.css";

initRumTelemetry();
initPostHogAnalytics();

type EnvClerkProviderProps = Omit<ClerkProviderProps, "publishableKey">;
const EnvClerkProvider = ClerkProvider as React.ComponentType<EnvClerkProviderProps>;

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
