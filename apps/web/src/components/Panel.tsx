import type { ReactNode } from "react";

import { ProvisionalBadge } from "@/components/badges";

interface PanelProps {
  title: string;
  /** One line saying what the panel measures, or over what window. */
  subtitle?: string;
  provisional?: boolean;
  /** Controls, filters, or a grain toggle belonging to this panel. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export default function Panel({
  title,
  subtitle,
  provisional,
  actions,
  children,
  className = "",
}: PanelProps) {
  return (
    <section
      className={`flex flex-col rounded border border-slate-200 bg-white p-4 ${className}`}
      aria-label={title}
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm leading-tight font-semibold text-slate-900">
            {title}
            {provisional ? <ProvisionalBadge /> : null}
          </h2>
          {subtitle ? <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      <div className="flex-1">{children}</div>
    </section>
  );
}
