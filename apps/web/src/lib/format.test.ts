import { describe, expect, it } from "vitest";

import { addAll, divideDecimals, parseDecimal, sumDecimals, toFixed } from "@/lib/decimal";
import {
  formatBucketLabel,
  formatDecimalPercent,
  formatFigure,
  formatMinutes,
  shortObjectName,
  unitLabel,
} from "@/lib/format";

describe("parseDecimal", () => {
  it("parses the plain decimal strings the API sends", () => {
    expect(toFixed(parseDecimal("15934.514768931")!, 3)).toBe("15934.515");
  });

  it("parses the scientific notation Python's Decimal emits for zero", () => {
    // `str(Decimal("0E-9"))` appears in reconciliation worst-day payloads.
    expect(toFixed(parseDecimal("0E-9")!, 2)).toBe("0.00");
  });

  it("parses negative values and keeps the sign off zero", () => {
    expect(toFixed(parseDecimal("-1875.42")!, 2)).toBe("-1875.42");
    expect(toFixed(parseDecimal("-0.000")!, 2)).toBe("0.00");
  });

  it("returns null for values that are not numbers, rather than zero", () => {
    expect(parseDecimal(null)).toBeNull();
    expect(parseDecimal(undefined)).toBeNull();
    expect(parseDecimal("")).toBeNull();
    expect(parseDecimal("n/a")).toBeNull();
    expect(parseDecimal(true)).toBeNull();
  });

  it("accepts the raw JSON numbers integer metrics return", () => {
    expect(toFixed(parseDecimal(13362)!, 0)).toBe("13362");
  });
});

describe("exact arithmetic", () => {
  it("sums decimal strings without float drift", () => {
    // 0.1 + 0.2 + 0.3 is 0.6, not 0.6000000000000001.
    expect(toFixed(sumDecimals(["0.1", "0.2", "0.3"]), 1)).toBe("0.6");
  });

  it("sums the many-digit credit strings the compiler emits", () => {
    const total = sumDecimals(["2685.254292594", "1797.985707406"]);
    expect(toFixed(total, 9)).toBe("4483.240000000");
  });

  it("adds already-parsed values", () => {
    const total = addAll([parseDecimal("1.005")!, parseDecimal("2.005")!]);
    expect(toFixed(total, 3)).toBe("3.010");
  });

  it("divides exactly, rounding half away from zero", () => {
    const ratio = divideDecimals(parseDecimal("2685.254292594")!, parseDecimal("4483.24")!, 6);
    expect(toFixed(ratio!, 6)).toBe("0.598954");
  });

  it("returns null rather than Infinity when dividing by zero", () => {
    expect(divideDecimals(parseDecimal("1")!, parseDecimal("0")!, 6)).toBeNull();
  });
});

describe("formatFigure", () => {
  it("groups and rounds a credit figure from its string", () => {
    expect(formatFigure("15934.514768931", "number", 1, "credits")).toBe("15,934.5");
  });

  it("keeps six-decimal unit costs intact", () => {
    expect(formatFigure("0.764455599289989", "number", 6, "credits")).toBe("0.764456");
  });

  it("renders a currency figure with its symbol", () => {
    expect(formatFigure("45104.310000000", "currency", 2, "USD")).toBe("$45,104.31");
  });

  it("falls back to the currency code when there is no symbol for it", () => {
    expect(formatFigure("1000", "currency", 2, "SEK")).toBe("1,000.00 SEK");
  });

  it("renders a stored fraction as a percentage by shifting the point", () => {
    expect(formatFigure("0.178354764397137", "percent", 1)).toBe("17.8%");
    expect(formatDecimalPercent(parseDecimal("0.425582229903333")!, 1)).toBe("42.6%");
  });

  it("renders integers without decimals", () => {
    expect(formatFigure(13362, "integer", 0)).toBe("13,362");
  });

  it("returns null for an unknown value so the caller can explain it", () => {
    expect(formatFigure(null, "number", 1, "credits")).toBeNull();
  });

  it("scales byte figures", () => {
    expect(formatFigure("1099511627776", "bytes", 2)).toBe("1.00 TiB");
  });

  it("scales millisecond durations", () => {
    expect(formatFigure("850", "duration_ms", 0)).toBe("850 ms");
    expect(formatFigure("12400", "duration_ms", 0)).toBe("12.40 s");
  });
});

describe("unitLabel", () => {
  it("captions plain numbers with their unit", () => {
    expect(unitLabel("number", "credits")).toBe("credits");
  });

  it("omits a caption where the figure already carries its unit", () => {
    expect(unitLabel("currency", "USD")).toBeNull();
    expect(unitLabel("percent", null)).toBeNull();
  });
});

describe("formatMinutes", () => {
  it("expresses latency floors the way the banner reads them", () => {
    expect(formatMinutes(45)).toBe("45m");
    expect(formatMinutes(180)).toBe("3h");
    expect(formatMinutes(480)).toBe("8h");
    expect(formatMinutes(4320)).toBe("3d");
  });
});

describe("formatBucketLabel", () => {
  it("labels buckets without shifting them through the reader's timezone", () => {
    expect(formatBucketLabel("2026-08-01T00:00:00", "month")).toBe("Aug 2026");
    expect(formatBucketLabel("2026-08-01T00:00:00", "day")).toBe("01 Aug");
    expect(formatBucketLabel("2026-08-01T13:00:00", "hour")).toBe("01 Aug 13:00");
  });
});

describe("shortObjectName", () => {
  it("reduces a qualified view to the name the banner names", () => {
    expect(shortObjectName("SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY")).toBe(
      "QUERY_ATTRIBUTION_HISTORY",
    );
  });
});
