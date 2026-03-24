import React from "react";
import ReactDOM from "react-dom/client";
import { PostHogProvider } from "@posthog/react";
import { BrowserRouter } from "react-router-dom";
import posthog from "posthog-js";
import RouterApp from "./RouterApp";
import { PostHogBridge } from "./components/PostHogBridge";
import { initPostHogAnalytics } from "./lib/posthog";
import { initRumTelemetry } from "./lib/rum";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/globals.css";

initRumTelemetry();
initPostHogAnalytics();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <PostHogProvider client={posthog}>
      <BrowserRouter>
        <PostHogBridge />
        <RouterApp />
      </BrowserRouter>
    </PostHogProvider>
  </React.StrictMode>
);
