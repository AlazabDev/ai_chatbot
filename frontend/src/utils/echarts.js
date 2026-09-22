// Copyright (c) 2026, Sanjay Kumar and contributors
// For license information, please see license.txt
// Tree-shaken ECharts runtime: only chart/component types emitted by the backend.
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { LabelLayout, UniversalTransition } from 'echarts/features'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
  LabelLayout,
  UniversalTransition,
])

export default echarts
