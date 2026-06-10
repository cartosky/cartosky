type PrefetchRouteKey = "viewer" | "account" | "forecast";

const prefetchedRoutes = new Set<PrefetchRouteKey>();

const ROUTE_LOADERS: Record<PrefetchRouteKey, () => Promise<unknown>> = {
  viewer: () => import("@/pages/viewer"),
  account: () => import("@/pages/account"),
  forecast: () => import("@/pages/forecast"),
};

export function prefetchRouteKeyFromPath(path: string): PrefetchRouteKey | undefined {
  const normalized = path.split("?")[0]?.split("#")[0] ?? path;
  if (normalized === "/viewer" || normalized === "/app") {
    return "viewer";
  }
  if (normalized === "/account" || normalized.startsWith("/account/")) {
    return "account";
  }
  if (normalized === "/forecast") {
    return "forecast";
  }
  return undefined;
}

export function prefetchRoute(route: PrefetchRouteKey): void {
  if (prefetchedRoutes.has(route)) {
    return;
  }
  prefetchedRoutes.add(route);
  void ROUTE_LOADERS[route]();
}

export function prefetchRoutes(routes: PrefetchRouteKey[]): void {
  for (const route of routes) {
    prefetchRoute(route);
  }
}

export function prefetchRouteForPath(path: string): void {
  const route = prefetchRouteKeyFromPath(path);
  if (route) {
    prefetchRoute(route);
  }
}

export function scheduleIdleRoutePrefetch(routes: PrefetchRouteKey[]): () => void {
  const run = () => prefetchRoutes(routes);

  if (typeof window !== "undefined" && "requestIdleCallback" in window) {
    const idleId = window.requestIdleCallback(run, { timeout: 4_000 });
    return () => window.cancelIdleCallback(idleId);
  }

  const timeoutId = globalThis.setTimeout(run, 1_500);
  return () => globalThis.clearTimeout(timeoutId);
}
