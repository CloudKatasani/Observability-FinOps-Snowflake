import { useState } from "react";

import type { Provenance, ScopeContext, SourceSummary } from "@/api/client";
import { formatClockTime, formatMinutes, shortObjectName } from "@/lib/format";
import { partialWarning, scopeCaption, scopeContextLabel } from "@/lib/scope";

// R5 made visible. Every tile, chart, and table in the product ends with this
// strip: when the figure was computed, how stale its slowest source may be,
// whose it is, which views it came from, and the exact SQL that produced it. It
// is part of the reading experience, not a developer affordance hidden behind a
// flag.
//
// Scope belongs here rather than in a strip of its own. "$45,104" means nothing
// until you know whether it is one account or the fleet, so it sits beside the
// as-of stamp and the latency floor, in the same sentence, for the same reason.

function sourceLabel(id: string, registry?: Map<string, SourceSummary>): string {
  const definition = registry?.get(id);
  return definition ? shortObjectName(definition.snowflake_object) : id.toUpperCase();
}

interface ProvenanceBarProps {
  provenance: Provenance;
  sql: string;
  registry?: Map<string, SourceSummary>;
  /** Names the figure in the disclosure label, e.g. "Total credits consumed". */
  label: string;
  /** Shown when the endpoint returned no SQL — says why, and where to get it. */
  noSqlNote?: string;
  /** Where this figure was computed, when the response reported it. */
  scope?: ScopeContext | null;
}

export default function ProvenanceBar({
  provenance,
  sql,
  registry,
  label,
  noSqlNote = "This response carried no compiled SQL.",
  scope,
}: ProvenanceBarProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const sources = provenance.sources.map((id) => sourceLabel(id, registry));

  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700">
          {scope ? (
            <>
              <span>
                scope <span className="font-medium text-slate-700">{scopeCaption(scope)}</span>
              </span>
              {scope.scope_partial ? (
                <span
                  title={partialWarning(scope)}
                  className="rounded border border-amber-300 bg-amber-50 px-1 py-px font-medium text-amber-900"
                >
                  <span aria-hidden className="mr-0.5 font-bold">
                    !
                  </span>
                  partial
                </span>
              ) : null}
              <span aria-hidden>·</span>
            </>
          ) : null}
          <span className="tabular-nums">as of {formatClockTime(provenance.as_of)}</span>
          <span aria-hidden>·</span>
          <span className="tabular-nums">
            latency floor {formatMinutes(provenance.latency_floor_minutes)}
          </span>
          <span aria-hidden>·</span>
          <span className="font-medium text-slate-700 underline decoration-dotted underline-offset-2">
            <span aria-hidden className="mr-1 inline-block group-open:hidden">
              ▸
            </span>
            <span aria-hidden className="mr-1 hidden group-open:inline-block">
              ▾
            </span>
            Show the SQL
          </span>
          <span className="sr-only">for {label}</span>
        </summary>

        <div className="mt-2 rounded border border-slate-200 bg-slate-50 p-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
              Compiled SQL
            </p>
            <button
              type="button"
              onClick={copy}
              className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="mt-1 max-h-64 overflow-auto rounded border border-slate-200 bg-white p-2 font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap text-slate-800">
            {sql || noSqlNote}
          </pre>

          {scope ? (
            <>
              <p className="mt-2 text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
                Scope
              </p>
              <p className="mt-1 text-[11px] text-slate-700">
                Computed at <span className="font-medium">{scopeContextLabel(scope)}</span> scope
                {scope.contributing_accounts.length > 0
                  ? ` over ${scope.contributing_accounts.join(", ")}.`
                  : "."}
              </p>
              {scope.scope_partial ? (
                <p className="mt-1 rounded border border-amber-300 bg-amber-50 px-1.5 py-1 text-[11px] text-amber-900">
                  <span aria-hidden className="mr-1 font-bold">
                    !
                  </span>
                  {partialWarning(scope)}
                </p>
              ) : null}
            </>
          ) : null}

          <p className="mt-2 text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
            Sources
          </p>
          {sources.length > 0 ? (
            <ul className="mt-1 flex flex-wrap gap-1">
              {sources.map((source) => (
                <li
                  key={source}
                  className="rounded border border-slate-300 bg-white px-1.5 py-0.5 font-mono text-[11px] text-slate-700"
                >
                  {source}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[11px] text-slate-600">No source views were read.</p>
          )}
        </div>
      </details>
    </div>
  );
}
