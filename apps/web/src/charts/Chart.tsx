import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import type { EChartsOption } from "echarts";

// Only the pieces the product actually draws are registered, so the chart
// library does not dominate the bundle.
echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface ChartProps {
  option: EChartsOption;
  height?: number;
  /** Describes the chart for assistive technology; the data table below it carries the figures. */
  ariaLabel: string;
}

export default function Chart({ option, height = 240, ariaLabel }: ChartProps) {
  return (
    <div role="img" aria-label={ariaLabel}>
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        notMerge
        lazyUpdate
        style={{ height, width: "100%" }}
      />
    </div>
  );
}
