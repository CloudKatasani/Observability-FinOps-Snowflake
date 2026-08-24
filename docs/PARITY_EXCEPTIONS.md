# Parity exceptions

**Both operating modes must produce identical numbers for identical inputs.** That is
the hardest requirement in this build (BUILD_PROMPT §1.4) and the dual-engine parity
suite enforces it: every metric is compiled for both dialects from the same YAML
definition, executed against the same fixture data, and compared row for row. Counts,
sums, and every credit or currency figure must match **exactly** — they are `Decimal`
end to end, never float.

This document records the *only* places where an exact match is not required, why, and
what the tolerance is. A tolerance that is not listed here does not exist: the harness
reads `PARITY_EXCEPTIONS` in `packages/engines/parity.py`, and a test asserts that every
entry names a real metric and carries a written justification.

## How parity is verified

| Check | What it proves | When it runs |
|---|---|---|
| **Executed parity** | The Snowflake-dialect SQL the platform would send produces the same numbers as the DuckDB-dialect SQL, on the same rows | Every commit |
| **Golden SQL snapshots** | Neither rendering changed unintentionally (216 files: 108 metrics × 2 dialects) | Every commit |
| **Live comparison** | The Snowflake SQL behaves identically on a real account | Nightly, `pytest -m snowflake` |

Executed parity runs the real Snowflake-dialect SQL against the fixture data. The few
Snowflake-specific functions the compiler emits are satisfied by DuckDB macros
(`packages/engines/snowflake_compat.py`), each a documented equivalence. That module is
a test facility only — the OFFLINE engine executes DuckDB-dialect SQL compiled from the
same definitions and never loads it.

## The exceptions

### 1. Percentile metrics — approximate vs exact

| Metric | Tolerance | Compared on |
|---|---|---|
| `q.p50_elapsed_ms` | 5% relative | Whole population |
| `q.p95_elapsed_ms` | 10% relative | Whole population |
| `q.p99_elapsed_ms` | 15% relative | Whole population |

**Why.** Snowflake's `APPROX_PERCENTILE` estimates from a t-digest sketch; DuckDB's
`quantile_cont` computes the exact value. The two therefore differ by construction, and
the difference grows toward the tail where fewer observations support the estimate.

**Why not use an exact percentile on Snowflake.** `PERCENTILE_CONT` on Snowflake is an
ordered-set aggregate that cannot be combined with the grouping the metric layer emits
without materialising the full distribution per group — on a real account's
`QUERY_HISTORY` that is an expensive query to run on a dashboard tile. The approximate
form is the right engineering choice; the honest response is to declare the tolerance,
not to hide it.

**Why these metrics are compared on the whole population, not per slice.** A t-digest
estimate is unstable on a small group: a warehouse with a handful of queries in the
window can differ from the exact percentile by far more than any tolerance worth having,
without the metric being wrong. Comparing per-slice would measure sample size rather than
parity. The whole-population comparison is the meaningful one, and it runs on every
commit. Consumers see this in the UI as well: percentile tiles state that the figure is
an estimate.

**Revisit trigger.** If a client requires exact percentiles for an SLA, add a
`PERCENTILE_EXACT` shim with its own metrics rather than changing these — the cost
characteristics are different enough that they are genuinely different metrics.

## Everything else matches exactly

All other metrics — every cost, credit, currency, count, byte, and ratio — are asserted
equal to the last digit, with no tolerance. In particular:

- **Money never has a tolerance.** Credits and currency are `DECIMAL(38,9)` in both
  engines. DuckDB's `/` returns floating point, so the `SAFE_DIVIDE` shim casts the
  quotient back to fixed point; without that cast, ratio metrics would drift in the last
  places and this document would need an entry it does not need.
- **Safe division returns NULL, not zero,** on a zero or null denominator in both
  engines. An unknown ratio stays unknown (R3); a zero would be a wrong answer that
  looks like a right one.
- **Fan-out safety is exact.** When a request mixes metrics from facts at different
  grains, each fact is aggregated in its own CTE before joining. A test asserts that
  adding a second metric to a request cannot change the first metric's totals — the
  silent-doubling bug this design exists to prevent.

## Adding an exception

1. Establish that the divergence is a genuine engine difference, not a bug in a shim or
   a metric definition. Most apparent divergences are the latter.
2. Add the entry to `PARITY_EXCEPTIONS` in `packages/engines/parity.py` with a written
   justification (the harness rejects a one-word reason).
3. Add a section here explaining the cause, the tolerance, and the revisit trigger.
4. Make the tolerance visible to users wherever the figure is displayed.
