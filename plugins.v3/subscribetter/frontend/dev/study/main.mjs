import {createApp} from 'vue';
import {createVuetify} from 'vuetify/framework';
import {VApp,VBtn,VDialog} from 'vuetify/components';
import 'vuetify/styles';
import App from './App.vue';
import './style.css';
const vuetify=createVuetify({components:{VApp,VBtn,VDialog},theme:{defaultTheme:'dark',themes:{dark:{dark:true,colors:{primary:'#aa8af6',surface:'#171d2a',background:'#10141e','on-surface':'#eceef5','on-primary':'#1d1238',success:'#86d6b0',warning:'#f0c17a',error:'#f39999'}},light:{dark:false,colors:{primary:'#7251bb',surface:'#ffffff',background:'#f4f5f8','on-surface':'#252633','on-primary':'#ffffff',success:'#247b59',warning:'#996011',error:'#b43e4c'}}}}});
createApp(App).use(vuetify).mount('#app');
