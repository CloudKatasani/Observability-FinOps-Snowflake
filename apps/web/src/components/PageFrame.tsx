import type { ReactNode } from "react";

import type { Provenance, SourceSummary } from "@/api/client";
import FreshnessBanner from "@/components/FreshnessBanner";

interface PageFrameProps {
  title: string;
  /** One sentence on what decision this page supports. */
  description: string;
  /** Provenance of everything on the page — drives the freshness banner (R7). */
  contributions: readonly (Provenance | undefined | null)[];
  sources?: readonly SourceSummary[];
  children: ReactNode;
}

export default function PageFrame({
  title,
  description,
  contributions,
  sources,
  children,
}: PageFrameProps) {
  return (
    <>
      <FreshnessBanner contributions={contributions} sources={sources} />
      <div className="space-y-4 p-4">
        <header className="border-l-2 border-[var(--brand-primary,#475569)] pl-3">
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          <p className="mt-0.5 text-sm text-slate-600">{description}</p>
        </header>
        {children}
      </div>
    </>
  );
}
