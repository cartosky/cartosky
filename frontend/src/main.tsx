import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider, type ClerkProviderProps } from "@clerk/react";
import { BrowserRouter } from "react-router-dom";
import RouterApp from "./RouterApp";
import { AnalyticsBridge } from "./components/AnalyticsBridge";
import { ClerkAuthTokenBridge } from "./components/ClerkAuthTokenBridge";
import { initAnalytics } from "./lib/analytics";
import { FeedbackProvider } from "./lib/feedback-context";
import { initRumTelemetry } from "./lib/rum";
import { SiteLoadingProvider } from "./lib/site-loading";
import { clerkAppearance } from "./lib/clerk-appearance";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/globals.css";

initRumTelemetry();
initAnalytics();

type EnvClerkProviderProps = Omit<ClerkProviderProps, "publishableKey">;
const EnvClerkProvider = ClerkProvider as React.ComponentType<EnvClerkProviderProps>;
const FeedbackWidget = lazy(() =>
  import("./components/FeedbackWidget").then((module) => ({ default: module.FeedbackWidget }))
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <EnvClerkProvider afterSignOutUrl="/" appearance={clerkAppearance}>
      <ClerkAuthTokenBridge />
      <BrowserRouter>
        <SiteLoadingProvider>
          <FeedbackProvider>
            <AnalyticsBridge />
            <RouterApp />
            <Suspense fallback={null}>
              <FeedbackWidget />
            </Suspense>
          </FeedbackProvider>
        </SiteLoadingProvider>
      </BrowserRouter>
    </EnvClerkProvider>
  </React.StrictMode>
);
