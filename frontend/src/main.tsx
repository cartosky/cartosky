import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import RouterApp from "./RouterApp";
import { FeedbackWidget } from "./components/FeedbackWidget";
import { PostHogBridge } from "./components/PostHogBridge";
import { FeedbackProvider } from "./lib/feedback-context";
import { initPostHogAnalytics } from "./lib/posthog";
import { initRumTelemetry } from "./lib/rum";
import { SiteLoadingProvider } from "./lib/site-loading";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/globals.css";

initRumTelemetry();
initPostHogAnalytics();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <SiteLoadingProvider>
        <FeedbackProvider>
          <PostHogBridge />
          <RouterApp />
          <FeedbackWidget />
        </FeedbackProvider>
      </SiteLoadingProvider>
    </BrowserRouter>
  </React.StrictMode>
);
