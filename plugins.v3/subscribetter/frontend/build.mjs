import {defineConfig} from 'vite';
import vue from '@vitejs/plugin-vue';
import federation from '@originjs/vite-plugin-federation';
import {readFileSync} from 'node:fs';
const pluginVersion=JSON.parse(readFileSync(new URL('../../../package.v3.json',import.meta.url),'utf8')).SubscriBetter.version;
export default defineConfig({
  plugins:[vue(),federation({name:'subscribetter',filename:'remoteEntry.js',exposes:{'./Page':'./src/Page.vue','./Config':'./src/Config.vue'},shared:{vue:{requiredVersion:'3.5.13',generate:false},vuetify:{requiredVersion:'3.7.3',generate:false}}})],
  build:{target:'esnext',outDir:'../dist',assetsDir:`assets-v${pluginVersion}`,emptyOutDir:true,minify:'esbuild',cssCodeSplit:true},
});
