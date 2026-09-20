import {defineConfig} from 'vite';
import vue from '@vitejs/plugin-vue';
import federation from '@originjs/vite-plugin-federation';
export default defineConfig({
  plugins:[vue(),federation({name:'subscribetter',filename:'remoteEntry.js',exposes:{'./Page':'./src/Page.vue','./Config':'./src/Config.vue'},shared:{vue:{requiredVersion:'3.5.13',generate:false},vuetify:{requiredVersion:'3.7.3',generate:false}}})],
  build:{target:'esnext',outDir:'../dist',emptyOutDir:true,minify:'esbuild',cssCodeSplit:true},
});
