import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

export function AdminPage({ children }: { children: ReactNode }) {
  return <div className="space-y-5">{children}</div>;
}

export function AdminHero(props: {
  eyebrow: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const { eyebrow, title, description, actions, children } = props;
  return (
    <section className="overflow-hidden rounded-[1.75rem] border border-white/10 bg-[#0b1526]/92 px-5 py-4 text-white shadow-[0_20px_60px_rgba(0,0,0,0.24)] md:px-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/72">
            <span className="h-px w-5 bg-cyan-300/45" />
            <span>{eyebrow}</span>
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white">{title}</h1>
          {description ? <div className="mt-1 max-w-3xl text-sm text-white/60">{description}</div> : null}
        </div>
        {actions ? <div className="flex flex-shrink-0 flex-wrap items-center gap-3">{actions}</div> : null}
      </div>
      {children ? <div className="mt-4 border-t border-white/8 pt-4">{children}</div> : null}
    </section>
  );
}

export function AdminSurface(props: {
  title?: string;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  headerRight?: ReactNode;
}) {
  const { title, description, children, className, headerRight } = props;
  return (
    <section className={cn("rounded-[1.5rem] border border-white/10 bg-[#0d182b]/88 p-4 text-white shadow-[0_16px_48px_rgba(0,0,0,0.2)] md:p-5", className)}>
      {(title || description || headerRight) ? (
        <div className="mb-4 flex flex-col gap-3 border-b border-white/8 pb-4 md:flex-row md:items-end md:justify-between">
          <div>
            {title ? <h2 className="text-lg font-semibold tracking-tight text-white">{title}</h2> : null}
            {description ? <div className="mt-1 text-sm leading-6 text-white/60">{description}</div> : null}
          </div>
          {headerRight ? <div>{headerRight}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function AdminStat(props: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  accentClassName?: string;
  icon?: ReactNode;
  className?: string;
  status?: ReactNode;
  onClick?: () => void;
  topAccentClass?: string;
}) {
  const { label, value, hint, accentClassName = "text-white", icon, className, status, onClick, topAccentClass } = props;
  const content = (
    <div className={cn("rounded-[1.15rem] border border-white/8 bg-white/[0.025] px-4 py-4 transition-colors", topAccentClass ? `border-t-2 ${topAccentClass}` : "", onClick ? "hover:bg-white/[0.045]" : "", className)}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-white">{label}</div>
            {status}
          </div>
          <div className={cn("mt-3 text-[1.85rem] font-semibold tracking-tight", accentClassName)}>{value}</div>
          {hint ? <div className="mt-2 text-xs uppercase tracking-[0.16em] text-white/40">{hint}</div> : null}
        </div>
        {icon ? <div className="text-white/54">{icon}</div> : null}
      </div>
    </div>
  );
  if (!onClick) return content;
  return (
    <button type="button" onClick={onClick} className="w-full text-left">
      {content}
    </button>
  );
}

export function AdminEmpty({ children }: { children: ReactNode }) {
  return (
    <section className="rounded-[1.5rem] border border-white/10 bg-[#0d182b]/88 px-5 py-6 text-white/72 shadow-[0_16px_48px_rgba(0,0,0,0.2)]">
      {children}
    </section>
  );
}

export function AdminTextLink(props: { to: string; children: ReactNode }) {
  return (
    <Link
      to={props.to}
      className="inline-flex items-center gap-2 rounded-xl border border-white/12 bg-white/[0.03] px-4 py-2 text-sm font-semibold text-white/88 transition duration-150 hover:border-white/20 hover:bg-white/[0.06]"
    >
      {props.children}
    </Link>
  );
}
