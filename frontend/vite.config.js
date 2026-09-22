// Copyright (c) 2026, Sanjay Kumar and contributors
// For license information, please see license.txt
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    include: ['vue', 'vue-router', 'lucide-vue-next'],
  },
  server: {
    fs: {
      // Allow reading common_site_config.json from sites/ (for socketio_port)
      allow: [path.resolve(__dirname, '..'), path.resolve(__dirname, '../../../sites')],
    },
  },
  build: {
    outDir: '../ai_chatbot/public/frontend',
    emptyOutDir: true,
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
      },
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/zrender/')) return 'zrender'
          if (id.includes('/node_modules/echarts/lib/chart/')) return 'echarts-charts'
          if (id.includes('/node_modules/echarts/lib/component/')) return 'echarts-components'
          if (id.includes('/node_modules/echarts/')) return 'echarts-core'
          if (id.includes('/node_modules/vue/') || id.includes('/node_modules/vue-router/')) return 'vue-vendor'
          if (id.includes('/node_modules/lucide-vue-next/')) return 'icons'
        },
      },
    },
  },
})
