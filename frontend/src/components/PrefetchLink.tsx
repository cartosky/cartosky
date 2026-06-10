import type { LinkHTMLAttributes } from "react";
import { Link, NavLink, type LinkProps, type NavLinkProps } from "react-router-dom";

import { prefetchRouteForPath } from "@/lib/route-prefetch";

function routePrefetchIntentHandlers(to: LinkProps["to"]) {
  if (typeof to !== "string") {
    return {};
  }

  const prefetch = () => prefetchRouteForPath(to);

  return {
    onMouseEnter: prefetch,
    onFocus: prefetch,
    onTouchStart: prefetch,
  } satisfies LinkHTMLAttributes<HTMLElement>;
}

export function PrefetchLink({ to, ...props }: LinkProps) {
  return <Link to={to} {...props} {...routePrefetchIntentHandlers(to)} />;
}

export function PrefetchNavLink({ to, ...props }: NavLinkProps) {
  return <NavLink to={to} {...props} {...routePrefetchIntentHandlers(to)} />;
}

export { routePrefetchIntentHandlers };
