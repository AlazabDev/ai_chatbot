<!-- Copyright (c) 2026, Sanjay Kumar and contributors -->
<!-- For license information, please see license.txt -->
<template>
  <div ref="chartContainer" :style="{ width: '100%', height: height + 'px' }"></div>
</template>

<script setup>
import { ref, shallowRef, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  option: {
    type: Object,
    required: true,
  },
  height: {
    type: Number,
    default: 320,
  },
})

const chartContainer = ref(null)
const chartInstance = shallowRef(null)
let resizeObserver = null
let themeObserver = null

function isDarkMode() {
  return document.documentElement.classList.contains('dark')
}

/**
 * Compact number formatter for chart axes.
 * 350,000,000 → "350.0M", 1,500 → "1.5K"
 */
function compactNumber(value) {
  const abs = Math.abs(value)
  if (abs >= 1e9) return (value / 1e9).toFixed(1) + 'B'
  if (abs >= 1e6) return (value / 1e6).toFixed(1) + 'M'
  if (abs >= 1e3) return (value / 1e3).toFixed(1) + 'K'
  return value
}

/**
 * Inject compact axis formatter on value axes so large numbers
 * don't overflow the chart area. Mutates the option in place.
 */
function injectAxisFormatters(option) {
  const patch = (axis) => {
    if (!axis) return
    const axes = Array.isArray(axis) ? axis : [axis]
    for (const ax of axes) {
      if (ax.type === 'value' && !ax.axisLabel?.formatter) {
        ax.axisLabel = { ...ax.axisLabel, formatter: compactNumber }
      }
    }
  }
  patch(option.xAxis)
  patch(option.yAxis)
}

onMounted(async () => {
  if (!chartContainer.value) return

  // Lazy-load echarts to avoid blocking initial page load
  const echartsModule = await import('../../utils/echarts')
  const echarts = echartsModule.default
  const theme = isDarkMode() ? 'dark' : undefined
  const chart = echarts.init(chartContainer.value, theme)
  chartInstance.value = chart

  // For dark theme, set transparent background so it blends with the message bubble
  const option = { ...props.option }
  if (isDarkMode()) {
    option.backgroundColor = 'transparent'
  }
  injectAxisFormatters(option)
  chart.setOption(option)

  // Watch for container resize
  resizeObserver = new ResizeObserver(() => {
    chart.resize()
  })
  resizeObserver.observe(chartContainer.value)

  // Watch for dark mode toggle — reinitialize chart with correct theme
  themeObserver = new MutationObserver(() => {
    const nowDark = isDarkMode()
    const newTheme = nowDark ? 'dark' : undefined
    if (chartInstance.value) {
      chartInstance.value.dispose()
    }
    const newChart = echarts.init(chartContainer.value, newTheme)
    const newOption = { ...props.option }
    if (nowDark) {
      newOption.backgroundColor = 'transparent'
    }
    injectAxisFormatters(newOption)
    newChart.setOption(newOption)
    chartInstance.value = newChart
  })
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class'],
  })
})

// Re-render when option prop changes
watch(
  () => props.option,
  (newOption) => {
    if (chartInstance.value && newOption) {
      const option = { ...newOption }
      injectAxisFormatters(option)
      chartInstance.value.setOption(option, true)
    }
  },
  { deep: true }
)

onUnmounted(() => {
  if (themeObserver) {
    themeObserver.disconnect()
    themeObserver = null
  }
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chartInstance.value) {
    chartInstance.value.dispose()
    chartInstance.value = null
  }
})
</script>
