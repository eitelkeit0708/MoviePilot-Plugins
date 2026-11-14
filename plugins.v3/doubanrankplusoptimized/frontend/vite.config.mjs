import { defineConfig } from 'vite'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  plugins: [federation({
    name: 'DoubanRankPlusOptimized',
    filename: 'remoteEntry.js',
    exposes: { './Page': './src/Page.mjs', './Config': './src/Config.mjs' },
    shared: { vue: { requiredVersion: false, generate: false } },
  })],
  build: { target: 'esnext', outDir: '../dist', emptyOutDir: true, rollupOptions: { input: {} } },
})
