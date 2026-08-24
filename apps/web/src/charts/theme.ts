// One place for chart styling, so every chart in the product reads as the same
// instrument. Brand colours arrive from `GET /api/v1/meta`; semantic status
// colours are deliberately kept out of this palette (§16.3) — a chart series
// must never be mistaken for a red/amber/green verdict.

import type { EChartsOption } from "echarts";

export interface BrandPalette {
  navy: string;
  primary: string;
  sky: string;
  coral: string;
}

/** Neutral chrome used until branding loads. Deliberately unbranded. */
export const NEUTRAL_PALETTE: BrandPalette = {
  navy: "#334155",
  primary: "#475569",
  sky: "#94A3B8",
  coral: "#64748B",
};

const INK = "#0F172A";
const MUTED = "#64748B";
const HAIRLINE = "#E2E8F0";

export const CHART_FONT =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

/** Series colours, most prominent first. */
export function seriesColors(palette: BrandPalette): string[] {
  return [palette.primary, palette.navy, palette.sky, palette.coral, "#7C8DA6", "#A8B6C6"];
}

interface AxisLabelFormatter {
  (value: string): string;
}

interface CategoryChartInput {
  palette: BrandPalette;
  categories: string[];
  series: { name: string; values: (number | null)[] }[];
  /** Stack every series into one bar per category, e.g. cost components. */
  stacked?: boolean;
  /** Renders the tooltip value from the exact API string, never from the plot number. */
  valueLabel: (seriesName: string, index: number) => string;
  categoryLabel?: AxisLabelFormatter;
  horizontal?: boolean;
  kind?: "bar" | "line";
}

function baseTextStyle() {
  return { fontFamily: CHART_FONT, fontSize: 12, color: MUTED };
}

/**
 * A category chart — bars or a line over time. Values are plotted as doubles
 * (pixels do not need cent precision); every value the reader actually reads,
 * in the tooltip or the panel's data table, comes from `valueLabel`, which is
 * fed the API's own decimal string.
 */
export function categoryChartOption(input: CategoryChartInput): EChartsOption {
  const { palette, categories, series, valueLabel, categoryLabel, horizontal, kind, stacked } =
    input;
  const isBar = kind !== "line";

  const valueAxis = {
    type: "value" as const,
    axisLabel: { ...baseTextStyle() },
    splitLine: { lineStyle: { color: HAIRLINE, type: "dashed" as const } },
    axisLine: { show: false },
    axisTick: { show: false },
  };

  const categoryAxis = {
    type: "category" as const,
    data: categories,
    axisLabel: {
      ...baseTextStyle(),
      formatter: categoryLabel,
      hideOverlap: true,
    },
    axisLine: { lineStyle: { color: HAIRLINE } },
    axisTick: { show: false },
  };

  return {
    color: seriesColors(palette),
    animationDuration: 240,
    textStyle: baseTextStyle(),
    grid: {
      top: series.length > 1 ? 32 : 12,
      left: 8,
      right: 12,
      bottom: 4,
      containLabel: true,
    },
    legend:
      series.length > 1
        ? {
            top: 0,
            left: 0,
            itemHeight: 8,
            itemWidth: 14,
            textStyle: { ...baseTextStyle(), color: INK },
          }
        : undefined,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: isBar ? "shadow" : "line" },
      backgroundColor: "#FFFFFF",
      borderColor: HAIRLINE,
      textStyle: { ...baseTextStyle(), color: INK },
      formatter: (params: unknown) => {
        const entries = Array.isArray(params) ? params : [params];
        const first = entries[0] as { axisValueLabel?: string; name?: string } | undefined;
        const heading = first?.axisValueLabel ?? first?.name ?? "";
        const lines = entries.map((entry) => {
          const item = entry as { seriesName?: string; dataIndex?: number; marker?: string };
          const label = valueLabel(item.seriesName ?? "", item.dataIndex ?? 0);
          return `${item.marker ?? ""}${item.seriesName ?? ""} <b>${label}</b>`;
        });
        return [heading, ...lines].join("<br/>");
      },
    },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series: series.map((entry) => ({
      name: entry.name,
      type: isBar ? ("bar" as const) : ("line" as const),
      data: entry.values,
      stack: stacked && isBar ? "total" : undefined,
      barMaxWidth: 28,
      smooth: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { width: 2 },
      // Gaps in the data stay gaps — a missing bucket is not a zero (R3).
      connectNulls: false,
    })),
  };
}
