import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider, type ClerkProviderProps } from "@clerk/react";
import { BrowserRouter } from "react-router-dom";
import RouterApp from "./RouterApp";
import { RoutePrefetchBridge } from "./components/RoutePrefetchBridge";
import { AnalyticsBridge } from "./components/AnalyticsBridge";
import { ClerkAuthTokenBridge } from "./components/ClerkAuthTokenBridge";
import { ClerkLoadFailureReporter } from "./components/ClerkLoadFailureReporter";
import { initAnalytics } from "./lib/analytics";
import { FeedbackProvider } from "./lib/feedback-context";
import { initRumTelemetry } from "./lib/rum";
import { BootstrapProvider } from "./lib/bootstrap-loading";
import { SiteLoadingProvider } from "./lib/site-loading";
import { clerkAppearance } from "./lib/clerk-appearance";
import { markChunkReloadAttempted } from "./lib/chunk-reload";
import "./styles/globals.css";

initRumTelemetry();
initAnalytics();

window.addEventListener("vite:preloadError", (event) => {
  if (markChunkReloadAttempted()) {
    event.preventDefault();
    window.location.reload();
  }
});

type EnvClerkProviderProps = Omit<ClerkProviderProps, "publishableKey">;
const EnvClerkProvider = ClerkProvider as React.ComponentType<EnvClerkProviderProps>;
const FeedbackWidget = lazy(() =>
  import("./components/FeedbackWidget").then((module) => ({ default: module.FeedbackWidget }))
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <EnvClerkProvider afterSignOutUrl="/" appearance={clerkAppearance}>
      <ClerkAuthTokenBridge />
      <ClerkLoadFailureReporter />
      <BrowserRouter>
        <BootstrapProvider>
          <RoutePrefetchBridge />
          <SiteLoadingProvider>
            <FeedbackProvider>
              <AnalyticsBridge />
              <RouterApp />
              <Suspense fallback={null}>
                <FeedbackWidget />
              </Suspense>
            </FeedbackProvider>
          </SiteLoadingProvider>
        </BootstrapProvider>
      </BrowserRouter>
    </EnvClerkProvider>
  </React.StrictMode>
);
