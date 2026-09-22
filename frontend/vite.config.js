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
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'icons': ['lucide-vue-next'],
        },
      },
    },
  },
})
