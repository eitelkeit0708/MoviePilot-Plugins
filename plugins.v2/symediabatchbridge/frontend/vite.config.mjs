import {defineConfig} from 'vite';
import federation from '@originjs/vite-plugin-federation';

export default defineConfig({
  plugins: [federation({
    name: 'SymediaBatchBridge', filename: 'remoteEntry.js',
    exposes: {'./Page': './src/Page.mjs', './Config': './src/Config.mjs'},
    shared: {vue: {requiredVersion: false, generate: false}},
  })],
  // Vue is shared with MP; no Vuetify, MDI or global theme CSS is bundled.
  build: {target: 'esnext', outDir: '../dist', emptyOutDir: true, minify: 'esbuild', rollupOptions: {input: {}}},
});
