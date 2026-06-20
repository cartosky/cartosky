import { lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import MarketingLayout from "./layouts/MarketingLayout";
import AppLayout from "./layouts/AppLayout";
import AdminLayout from "./layouts/AdminLayout";
import Home from "./pages/home";
import { AdminProtectedRoute } from "./components/AdminProtectedRoute";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { CapabilitiesProvider } from "./lib/capabilities-context";

const CHUNK_RELOAD_SESSION_KEY = "cartosky:lazy-chunk-reload";

function isRecoverableChunkError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }

  const message = error.message.toLowerCase();
  return (
    message.includes("dynamically imported module")
    || message.includes("failed to fetch dynamically imported module")
    || message.includes("error loading dynamically imported module")
    || message.includes("importing a module script failed")
    || message.includes("chunkloaderror")
  );
}

function markChunkReloadAttempted(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    if (window.sessionStorage.getItem(CHUNK_RELOAD_SESSION_KEY) === "1") {
      return false;
    }
    window.sessionStorage.setItem(CHUNK_RELOAD_SESSION_KEY, "1");
    return true;
  } catch {
    return true;
  }
}

function clearChunkReloadAttempt(): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.removeItem(CHUNK_RELOAD_SESSION_KEY);
  } catch {
    // Ignore session storage failures and continue without persistence.
  }
}

function lazyRoute<T extends React.ComponentType<any>>(
  loader: () => Promise<{ default: T }>
) {
  return lazy(async () => {
    try {
      const module = await loader();
      clearChunkReloadAttempt();
      return module;
    } catch (error) {
      if (isRecoverableChunkError(error) && markChunkReloadAttempted()) {
        window.location.reload();
        return new Promise<never>(() => undefined);
      }
      throw error;
    }
  });
}

const Climate = lazyRoute(() => import("./pages/climate"));
const Forecast = lazyRoute(() => import("./pages/forecast"));
const Models = lazyRoute(() => import("./pages/models"));
const Variables = lazyRoute(() => import("./pages/variables"));
const Login = lazyRoute(() => import("./pages/login"));
const Account = lazyRoute(() => import("./pages/account"));
const Privacy = lazyRoute(() => import("./pages/privacy"));
const Pricing = lazyRoute(() => import("./pages/pricing"));
const CheckoutSuccess = lazyRoute(() => import("./pages/checkout-success"));
const AdminOverview = lazyRoute(() => import("./pages/admin/overview"));
const AdminAnalytics = lazyRoute(() => import("./pages/admin/analytics"));
const AdminObservability = lazyRoute(() => import("./pages/admin/observability"));
const AdminStatus = lazyRoute(() => import("./pages/admin/status"));
const AdminTraces = lazyRoute(() => import("./pages/admin/traces"));
const AdminFeedback = lazyRoute(() => import("./pages/admin/feedback"));
const AdminRoadmap = lazyRoute(() => import("./pages/admin/roadmap"));
const Viewer = lazyRoute(() => import("./pages/viewer"));
const Compare = lazyRoute(() => import("./pages/compare"));

function getPageTitle(pathname: string) {
  const pageTitles: Array<[prefix: string, title: string]> = [
    ["/admin/overview", "Admin Overview"],
    ["/admin/analytics", "Admin Analytics"],
    ["/admin/observability", "Admin Observability"],
    ["/admin/traces", "Admin Traces"],
    ["/admin/status", "Admin Status"],
    ["/admin/feedback", "Admin Feedback"],
    ["/roadmap", "Roadmap"],
    ["/admin", "Admin"],
    ["/viewer", "Viewer"],
    ["/compare", "Compare"],
    ["/forecast", "Forecast"],
    ["/climate", "Climate Indices"],
    ["/models", "Models"],
    ["/variables", "Variables"],
    ["/privacy", "Privacy Policy"],
    ["/pricing", "Pricing"],
    ["/checkout-success", "Checkout Success"],
    ["/login", "Login"],
    ["/account", "Account"],
    ["/", "Home"],
  ];

  const matchedTitle = pageTitles.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`))?.[1];

  return matchedTitle ? `CartoSky - ${matchedTitle}` : "CartoSky";
}

export default function RouterApp() {
  const location = useLocation();

  useEffect(() => {
    document.title = getPageTitle(location.pathname);
  }, [location.pathname]);

  return (
    <Routes>
      <Route element={<MarketingLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/forecast" element={<Forecast />} />
        <Route path="/climate" element={<Climate />} />
        <Route path="/models" element={<Models />} />
        <Route path="/variables" element={<Variables />} />
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/account/*" element={<Account />} />
        </Route>
        <Route path="/pricing" element={<Pricing />} />
        <Route path="/checkout-success" element={<CheckoutSuccess />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/privacy-policy" element={<Privacy />} />
      </Route>

      <Route element={<AppLayout />}>
        <Route path="/viewer" element={<Viewer />} />
        <Route path="/compare" element={<CapabilitiesProvider><Compare /></CapabilitiesProvider>} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AdminProtectedRoute />}>
            <Route path="/roadmap" element={<AdminRoadmap />} />
          </Route>
          <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/overview" replace />} />
          <Route path="overview" element={<AdminOverview />} />
          <Route path="analytics" element={<AdminAnalytics />} />
          <Route path="observability" element={<AdminObservability />} />
          <Route path="traces" element={<AdminTraces />} />
          <Route path="status" element={<AdminStatus />} />
          <Route path="feedback" element={<AdminFeedback />} />
          <Route path="legacy-performance" element={<Navigate to="/admin/overview" replace />} />
          <Route path="performance" element={<Navigate to="/admin/overview" replace />} />
          <Route path="usage" element={<Navigate to="/admin/analytics" replace />} />
          </Route>
        </Route>
      </Route>

      <Route path="/app" element={<Navigate to="/viewer" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
