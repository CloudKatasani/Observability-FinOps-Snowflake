# Demo

A guided walkthrough of the platform on a synthetic Snowflake account. No
Snowflake account, no cloud credentials, and no LLM API key are required.

Every question, tile, and figure named below was run against the shipped fixture
(`--seed 42 --days 120 --scale small`) while this document was written. Where a
phenomenon does not appear on a dashboard, that is said rather than glossed.

---

## What this is

`make demo` generates a synthetic Snowflake account, ingests it **through the same
upload pipeline a customer's own extracts go through**, and serves the API and the
SPA from a single container on `http://localhost:8080`. Nothing is written straight
into the lake, so what the demo shows is what the real ingestion path produces.

The account is 120 days of history, 12 warehouses, 8 teams, at the `small` scale
(1,500 queries per day) — the defaults in `scripts/demo_seed.py` and
`fixtures/generator/src/snowobs_fixtures/config.py`. The window ends **today**, so
the dashboards' "Last 30 days" default lands inside the data.

Fourteen phenomena are planted deliberately, declared in
`fixtures/generator/src/snowobs_fixtures/ground_truth.py`. That file is the
contract between the generator and the analytics engines: tests assert detection
against it, so "the platform finds the spike" is a CI-enforced claim rather than a
demo trick.

The demo runs with `LLM__PROVIDER=none`. The agent console still works — questions
route to governed metrics deterministically, tools run, figures come back with
their provenance, and each answer says plainly that narrative generation is
disabled. `FINOPS__CREDIT_PRICE_USD=3.00` is set in `docker-compose.demo.yml` and
`scripts/demo.sh` so dollar figures appear; the app names the rate it applied, and
with the rate unset it shows credits only rather than inventing a price.

---

## Run it

```bash
git clone <repo> && cd <repo>
make doctor      # optional but worth 20 seconds: ports, Docker resources, config
make demo        # http://localhost:8080
```

`make demo` builds the all-in-one image, starts Postgres and Redis, runs the
seeder to completion, waits for the app's own healthcheck, then smoke-tests five
endpoints — `/healthz`, `/api/v1/meta`, `/api/v1/datasets/coverage`,
`/api/v1/metrics/cost.total_credits/tile`, `/api/v1/chargeback/allocation` — before
printing the banner. If the banner appears, the app really answers.

No Docker? `make demo-native` does the same with host processes (`uv` and Node 22
required). Logs: `docker compose -f docker-compose.demo.yml logs -f app`.

| Surface | URL |
|---|---|
| App | <http://localhost:8080/> |
| OpenAPI | <http://localhost:8080/docs> |
| Coverage (raw) | <http://localhost:8080/api/v1/datasets/coverage> |

---

## The ten-minute walkthrough

Six pages, in this order. The global time-range picker in the header applies to
every page and is mirrored into the URL, so any view here can be shared as a link.

### 1 · Executive (`/`) — two minutes

Open with the six tiles: **Total credits**, **Billed credits**, **Spend**,
**Unattributed spend**, **Idle credit share**, **Cost per query**.

Then do the thing that separates this from a dashboard screenshot: **expand the
provenance strip under any tile.** Every tile carries "as of …", its freshness
floor, the source views behind it, and a **Show the SQL** disclosure with the
compiled statement. That is principle R5, and it is a reading affordance rather
than a developer flag.

Point at two tiles specifically:

- **Unattributed spend.** On the shipped fixture this sits inside the 12–25% band
  the ground truth declares for the planted untagged-spend phenomenon
  (`ph-untagged-spend`). Roughly a fifth of the bill cannot be attributed to a team
  — the number that starts most FinOps engagements.
- **Idle credit share.** Credits metered but attributable to no query. It is the
  second component of the chargeback split, and it is where right-sizing money
  lives.

Scroll on: the **13-month trend**, **cost by service type** (the AI/Cortex slice
appears part-way through the window and grows — `ph-ai-spend-growth`), **cost by
warehouse**, and **top offender fingerprints** — where the planted query regression
`ph-fingerprint-regression` sits at the top.

**Conclusion for a viewer:** every figure states where it came from, how stale it
may be, and the exact SQL behind it. Nothing here is a number you have to take on
trust.

### 2 · Platform health (`/health`) — one and a half minutes

Tiles: **Query failure rate**, **Query volume**, **Queue overload share**.

- **Source freshness** lists every landed source against its documented latency.
  This is R7 in operation: the platform states the freshness floor of its slowest
  input rather than implying the dashboard is live.
- **Queue overload by warehouse** puts `WH_QUEUED` at the top — the planted
  saturation phenomenon (`ph-queueing`).
- **Warehouse utilisation** charts attributed against idle credits per warehouse.
  `WH_OVERSIZED` shows a low attributed share with no queueing — the signature of a
  warehouse that can be safely resized (`ph-oversized-wh`). `WH_ZOMBIE` shows
  credits with essentially no attributed usage (`ph-zombie-warehouse`).

**Conclusion:** the two look identical on a cost chart and mean completely
different things. Utilisation plus queueing separates "too big" from "busy".

### 3 · Chargeback (`/chargeback`) — two and a half minutes. The centrepiece.

The **reconciliation gate banner** is at the top, before any figure. On the shipped
fixture it passes, and reads in this form — your credit figures will differ,
because the generated window ends today:

> Reconciled: allocated 19002.52 credits vs metered 19002.52 (+0.000%), within ±0.5%.

Say why that banner exists: allocated cost reconciles to the metered bill or the
chargeback figures **do not publish** (R6). When the gate fails, the team table is
empty and the page says so — not a table with a warning beside it.

Then the **allocated cost by team** table and the **component mix** chart. Each
team's cost is three components:

| Component | Where it comes from |
|---|---|
| Direct | The team's own attributed query credits |
| Idle share | The warehouse's metered-minus-attributed credits, shared pro-rata among the teams that actually used it |
| Cloud-services share | The account's billed cloud services, shared pro-rata to compute |

Two things to point out:

- `UNATTRIBUTED` appears in the table as a first-class line, not hidden. Unowned
  cost is a finding, not a rounding error.
- Expand the SQL disclosure: the allocation is four compiled statements and **all
  four are shown**, each labelled with what it contributes.

**Conclusion:** this is the difference between a chargeback report and a chargeback
argument. Every line reconciles to the bill, and the arithmetic is inspectable.

### 4 · Coverage & sources (`/coverage`) — one and a half minutes

The R3 page. Sources grouped by domain, each with status, row count, window,
freshness against its documented latency, and copy-pastable remediation.

On the demo the generator lands **16 of the 55 registered sources**, and **96 of
the 108 KPIs are enabled** — the fixture covers every account-scoped domain. The
12 that are not are the organization ones (D10) that need a fleet-wide export from
the organization account: they render as "Unavailable — requires
`ORGANIZATION_USAGE.…`" with the remediation beside them, which is R3 working
rather than a gap. The 39 sources shown as missing carry an upload instruction
each; in LIVE mode the same column carries the exact `GRANT DATABASE ROLE …`
statement instead.

To show the unavailable path properly, take a source away — see
[Showing R3 for real](#showing-r3-for-real) below.

**Conclusion:** the platform tells you what it can and cannot answer, and exactly
what would fix each gap. It never renders a missing source as a zero.

### 5 · Ask (`/ask`) — two minutes

The agent console. Ask these, in order; each was verified to route to the metric
named:

| Question | Routes to | Agent |
|---|---|---|
| "What were our billed credits?" | `cost.billed_credits` | finops |
| "Which warehouse costs the most?" | `cost.by_warehouse_credits` | finops |
| "How much spend is untagged?" | `cost.unattributed_share` | finops |
| "Which team spends the most?" | `cost.by_team_credits` | finops |
| "Which warehouses are queueing?" | `wh.queue_overload_pct` | sre |
| "How many dormant users are there?" | `sec.dormant_users` | governance |
| "Which source views are loaded?" | coverage (no metric) | onboarding |

Watch the **streamed trace**: tool calls appear as they happen, then the answer,
then the metrics used, the sources used, and the SQL behind each figure. Same
statement, same governed metric, same number as the dashboard — because the agent
picks metrics, never writes SQL.

Then ask something out of scope: *"What is the weather in Dublin?"* The console
declines and says why:

> No LLM provider is configured, so I answer by matching your question to the
> governed metric catalogue — and nothing matched this one.

**Conclusion:** the agent quotes tool results or declines. It has no path to invent
a figure — the runtime withholds any answer containing a number no tool returned
(R12), and `make eval` gates merges on zero fabrications and zero injections obeyed
across 76 golden questions.

*Honest note for the demo:* with no LLM configured, both out-of-scope questions and
the governance refusals ("which of our engineers is least productive?") come back
as "nothing matched" rather than as a policy refusal. The policy refusal rules live
in `packages/agents/src/snowobs_agents/prompts/_shared.md` and take effect when a
provider is configured.

### 6 · System status (`/status`) — thirty seconds

Liveness, version, and each backing service from `/readyz`. Close on the point that
the tool that preaches observability is itself observable, and that liveness and
readiness are deliberately different questions —
[`RUNBOOK.md`](RUNBOOK.md#health-and-readiness) explains why.

---

## The planted phenomena

All fourteen, from `ground_truth.py`, with where each one surfaces. "Metric" means
the figure is in the catalogue and reachable from the **Ask** page or
`GET /api/v1/metrics/{id}/tile`, but is not on one of the six shipped dashboards.

| Id | What is planted | Where it surfaces |
|---|---|---|
| `ph-oversized-wh` | `WH_OVERSIZED`: low utilisation, no queueing, safe to resize two steps down | Platform health → warehouse utilisation; `wh.utilisation_pct` |
| `ph-queueing` | `WH_QUEUED`: sustained queueing at `max_clusters` | Platform health → queue overload by warehouse; `wh.queue_overload_pct` tile |
| `ph-fingerprint-regression` | A fingerprint's pruning collapses and it becomes the top cost offender | Executive → top offender fingerprints; `q.offender_credits`, `q.pruning_efficiency` |
| `ph-remote-spill` | An ELT job spills to remote storage on every run | Metric: `q.spill_remote_bytes` |
| `ph-spend-spike` | A single-day 4× spend spike on one warehouse, one team | Executive → cost by warehouse and the daily trend; Chargeback → the team's line |
| `ph-task-root-failure` | One root task failure fans out to 12 downstream failures | Metrics: `pipe.root_failures` (returns 1 — one alert at the root, not twelve), `pipe.task_failures` |
| `ph-dt-lag` | A dynamic table misses `TARGET_LAG` for three consecutive days | Metric: `pipe.dt_lag_breaches` |
| `ph-untagged-spend` | ~18% of spend untagged, concentrated in two warehouses | Executive → **Unattributed spend** tile; Chargeback → `UNATTRIBUTED` line and unattributed share |
| `ph-dormant-users` | Six contractor accounts with no login in 90 days | Metric: `sec.dormant_users` (returns exactly 6, the declared cohort size) |
| `ph-privilege-drift` | A new `ACCOUNTADMIN`-adjacent grant appears | Metrics: `sec.privileged_grants`, `sec.new_grants` |
| `ph-clone-growth` | Storage retained for an un-dropped clone group | Metric: `storage.clone_retained_bytes` |
| `ph-time-travel-excess` | Excessive Time Travel retention in non-prod databases | Metric: `storage.time_travel_ratio` |
| `ph-ai-spend-growth` | Cortex/AI spend appears in week 10 and grows | Executive → cost by service type (the `AI_SERVICES` slice); `ai.total_credits` |
| `ph-zombie-warehouse` | A warehouse burning credits with no queries for 30 days | Platform health → warehouse utilisation; `wh.zombie_credits` |

The metric-only rows are not a gap in detection — the analytics engines
(`packages/analytics/`) detect and attribute all fourteen, and
`packages/analytics/tests/test_analytics.py` asserts it against the ground truth.
They are a gap in **dashboards**: six pages ship, and BUILD_PROMPT §16.1 sketches
eleven. See [`ARCHITECTURE.md`](ARCHITECTURE.md) §11.

Use the full window for the slower phenomena: switch the range picker to **Last 90
days** or **Last 13 months** for storage growth, AI spend growth, and the dormant
cohort.

---

## Showing R3 for real

The demo lake covers every KPI, so to show the "Unavailable — requires …" path,
take an input away. On a native demo (or any local `.data` lake):

```bash
rm -rf .data/default/query_attribution_history
```

Reload `/coverage`. Verified result: 15 sources available, 40 missing, and **17
KPIs turn unavailable**, each naming its blocker:

> `chargeback.allocated_credits` — Unavailable — requires `query_attribution_history`

The chargeback page degrades honestly too, because the allocation's direct
component has lost its input. Nothing renders as zero, and nothing renders as an
empty chart with no explanation.

Restore with `make seed` (or `uv run python scripts/demo_seed.py --root .data
--force`).

---

## Reset

| Goal | Command |
|---|---|
| Stop the demo, keep the data | `docker compose -f docker-compose.demo.yml down` |
| Stop and delete everything, including the fixture lake | `make demo-down` |
| Fresh dataset, same stack | `make demo-down && make demo` |
| Re-seed a local lake in place | `uv run python scripts/demo_seed.py --root .data --force` |
| Pin the window to a fixed date (for a repeatable screenshot) | `uv run python scripts/demo_seed.py --root .data --end-date 2026-08-24 --force` |

Re-running `make demo` without `--force` is cheap: the seeder sees the landed data
and returns immediately.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `make demo` fails immediately on Docker | Docker not installed or the daemon is not running | `make doctor` says which; or use `make demo-native` |
| A port is already bound | Something else on 8080 (or 8000/5173/5432/6379/9000 for `make dev`) | `make doctor` lists every port and what wants it. `SNOWOBS_DEMO_PORT=8081 make demo` moves the demo |
| Build is killed part-way | Docker memory below the 4 GB the SPA build and the fixture generation need | Raise Docker Desktop's memory limit; `make doctor` checks it |
| The smoke test prints `FAIL /api/v1/...` | The app came up but an endpoint did not answer | `docker compose -f docker-compose.demo.yml logs app`; the settings error, if any, is the first line |
| Dashboards are empty | The seeder did not land data | `docker compose -f docker-compose.demo.yml logs demo-seed` — it prints rows generated, files landed, and anything rejected. Then `make demo-down && make demo` |
| Tiles say "Unavailable — requires …" | A source is genuinely missing from the lake | That is the correct behaviour (R3). Re-seed with `--force` |
| Charts show data but the range looks empty | The picker is outside the generated window | The fixture is the 120 days ending today; pick **Last 30 days** |
| The agent declines everything | Expected without an LLM key for anything the catalogue cannot match | Use the verified questions above, or configure `LLM__PROVIDER` |
| First run takes several minutes | Image build and 120 days of fixture generation | Subsequent runs are cached and start in seconds |
