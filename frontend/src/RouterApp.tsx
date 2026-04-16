import { Suspense, lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import MarketingLayout from "./layouts/MarketingLayout";
import AppLayout from "./layouts/AppLayout";
import AdminLayout from "./layouts/AdminLayout";

const Home = lazy(() => import("./pages/home"));
const Forecast = lazy(() => import("./pages/forecast"));
const Models = lazy(() => import("./pages/models"));
const Variables = lazy(() => import("./pages/variables"));
const Login = lazy(() => import("./pages/login"));
const AdminOverview = lazy(() => import("./pages/admin/overview"));
const AdminAnalytics = lazy(() => import("./pages/admin/analytics"));
const AdminObservability = lazy(() => import("./pages/admin/observability"));
const AdminStatus = lazy(() => import("./pages/admin/status"));
const AdminTraces = lazy(() => import("./pages/admin/traces"));
const Viewer = lazy(() => import("./pages/viewer"));

function withSuspense(node: React.ReactNode) {
  return <Suspense fallback={null}>{node}</Suspense>;
}

function getPageTitle(pathname: string) {
  const pageTitles: Array<[prefix: string, title: string]> = [
    ["/admin/overview", "Admin Overview"],
    ["/admin/analytics", "Admin Analytics"],
    ["/admin/observability", "Admin Observability"],
    ["/admin/traces", "Admin Traces"],
    ["/admin/status", "Admin Status"],
    ["/admin", "Admin"],
    ["/viewer", "Viewer"],
    ["/forecast", "Forecast"],
    ["/models", "Models"],
    ["/variables", "Variables"],
    ["/login", "Login"],
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
        <Route path="/" element={withSuspense(<Home />)} />
        <Route path="/forecast" element={withSuspense(<Forecast />)} />
        <Route path="/models" element={withSuspense(<Models />)} />
        <Route path="/variables" element={withSuspense(<Variables />)} />
        <Route path="/login" element={withSuspense(<Login />)} />
      </Route>

      <Route element={<AppLayout />}>
        <Route path="/viewer" element={withSuspense(<Viewer />)} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/overview" replace />} />
          <Route path="overview" element={withSuspense(<AdminOverview />)} />
          <Route path="analytics" element={withSuspense(<AdminAnalytics />)} />
          <Route path="observability" element={withSuspense(<AdminObservability />)} />
          <Route path="traces" element={withSuspense(<AdminTraces />)} />
          <Route path="status" element={withSuspense(<AdminStatus />)} />
          <Route path="legacy-performance" element={<Navigate to="/admin/overview" replace />} />
          <Route path="performance" element={<Navigate to="/admin/overview" replace />} />
          <Route path="usage" element={<Navigate to="/admin/analytics" replace />} />
        </Route>
      </Route>

      <Route path="/app" element={<Navigate to="/viewer" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
