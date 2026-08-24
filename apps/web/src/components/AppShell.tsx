import { useEffect, useRef } from "react";
import { NavLink, Outlet, useSearchParams } from "react-router-dom";

import ScopePicker from "@/components/ScopePicker";
import TimeRangePicker from "@/components/TimeRangePicker";
import { useMeta } from "@/hooks/useApi";
import { parseScopeSelection, sameScope, useScopeStore } from "@/store/scope";
import type { PresetId } from "@/store/timeRange";
import { PRESETS, isIsoDate, useTimeRangeStore } from "@/store/timeRange";

const NAV = [
  { to: "/", label: "Executive", hint: "Cost and spend overview" },
  { to: "/health", label: "Platform health", hint: "Freshness, failures, contention" },
  { to: "/chargeback", label: "Chargeback", hint: "Allocated cost by team" },
  { to: "/coverage", label: "Coverage & sources", hint: "What landed and what is missing" },
  { to: "/ask", label: "Ask", hint: "Question the governed metrics directly" },
  { to: "/status", label: "System status", hint: "API and backing services" },
] as const;

function isPreset(value: string | null): value is PresetId {
  return value === "custom" || PRESETS.some((preset) => preset.id === value);
}

/**
 * Keeps the global filters and the URL query string in step, in both
 * directions.
 *
 * Range and scope share one hook deliberately: two effects each rebuilding the
 * query string from the same `params` snapshot would race, and the loser's
 * parameter would be dropped from a link the user was about to share.
 */
function useFilterUrlSync() {
  const [params, setParams] = useSearchParams();
  const preset = useTimeRangeStore((state) => state.preset);
  const start = useTimeRangeStore((state) => state.start);
  const end = useTimeRangeStore((state) => state.end);
  const hydrate = useTimeRangeStore((state) => state.hydrate);
  const selectPreset = useTimeRangeStore((state) => state.selectPreset);
  const scope = useScopeStore((state) => state.scope);
  const account = useScopeStore((state) => state.account);
  const selectScope = useScopeStore((state) => state.select);
  const hydrated = useRef(false);

  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const urlPreset = params.get("range");
    const urlStart = params.get("start");
    const urlEnd = params.get("end");
    if (isIsoDate(urlStart) && isIsoDate(urlEnd) && urlEnd >= urlStart) {
      hydrate(isPreset(urlPreset) ? urlPreset : "custom", { start: urlStart, end: urlEnd });
    } else if (isPreset(urlPreset)) {
      selectPreset(urlPreset);
    }
    // A malformed scope is ignored rather than guessed at, exactly as a
    // malformed range is: the default organization scope is the honest
    // fallback, and it is the widest one the deployment has.
    const urlScope = parseScopeSelection(params.get("scope"), params.get("account"));
    if (urlScope) selectScope(urlScope);
  }, [params, hydrate, selectPreset, selectScope]);

  useEffect(() => {
    if (!hydrated.current) return;
    const urlScope = parseScopeSelection(params.get("scope"), params.get("account"));
    if (
      params.get("range") === preset &&
      params.get("start") === start &&
      params.get("end") === end &&
      urlScope !== null &&
      sameScope(urlScope, { scope, account })
    )
      return;
    const next = new URLSearchParams(params);
    next.set("range", preset);
    next.set("start", start);
    next.set("end", end);
    next.set("scope", scope);
    if (scope === "account" && account) {
      next.set("account", account);
    } else {
      next.delete("account");
    }
    setParams(next, { replace: true });
  }, [preset, start, end, scope, account, params, setParams]);
}

export default function AppShell() {
  const meta = useMeta();
  useFilterUrlSync();

  const palette = meta.data?.branding.palette;
  const brandStyle = palette
    ? ({
        "--brand-navy": palette.navy,
        "--brand-primary": palette.primary,
        "--brand-sky": palette.sky,
        "--brand-coral": palette.coral,
      } as React.CSSProperties)
    : undefined;

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900" style={brandStyle}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Skip to content
      </a>

      <div className="mx-auto flex min-h-screen w-full max-w-[1600px] flex-col md:flex-row">
        <nav
          aria-label="Sections"
          className="shrink-0 border-b border-slate-800 bg-[var(--brand-navy,#1E293B)] md:w-56 md:border-r md:border-b-0"
        >
          <div className="px-4 py-4">
            {meta.data ? (
              <p className="text-sm leading-snug font-semibold text-white">
                {meta.data.branding.display_name}
              </p>
            ) : (
              <div className="h-5 w-40 animate-pulse rounded bg-white/20" aria-hidden />
            )}
            {meta.data ? (
              <p className="mt-1 text-[11px] tracking-wide text-white/60 uppercase">
                {meta.data.branding.short_name} · {meta.data.mode} mode · v{meta.data.version}
              </p>
            ) : null}
          </div>

          <ul className="flex flex-row flex-wrap gap-1 px-2 pb-3 md:flex-col md:gap-0.5">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  title={item.hint}
                  className={({ isActive }) =>
                    `block rounded px-2.5 py-1.5 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${
                      isActive
                        ? "bg-white/15 font-semibold text-white"
                        : "text-white/75 hover:bg-white/10 hover:text-white"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <TimeRangePicker />
              <ScopePicker />
            </div>
            <p className="text-[11px] text-slate-500">
              Read-only. Figures are traceable to their compiled SQL.
            </p>
          </header>
          <main id="main" className="min-w-0 flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
