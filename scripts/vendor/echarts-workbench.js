// Input for esbuild 0.25.0; dependencies: echarts 6.0.0, zrender 6.0.0, tslib 2.3.0.
// Build as an IIFE named echarts, target es2020, minify, legal-comments external.
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { TooltipComponent, LegendComponent, LegendScrollComponent, GridComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
echarts.use([LineChart, TooltipComponent, LegendComponent, LegendScrollComponent, GridComponent, CanvasRenderer]);
export { init, getInstanceByDom } from 'echarts/core';
