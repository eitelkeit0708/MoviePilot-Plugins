import {defineComponent, h, ref, onMounted, resolveDynamicComponent} from 'vue';
import Node from './Node.mjs';
import {createClient} from './client.mjs';

const v = (name, props, children) => h(resolveDynamicComponent(name), props, children === undefined ? undefined : () => children);
export default defineComponent({
  name: 'SbbPage', props: {api: Object, pluginId: String, show_switch: {type: Boolean, default: true}},
  emits: ['close', 'switch'],
  setup(props, {emit}) {
    const page = ref([]), busy = ref(false), error = ref(''), loaded = ref(false);
    const client = createClient(props.api, props.pluginId || 'SymediaBatchBridge', value => {page.value = value; loaded.value = true;});
    async function run(action, params) {
      if (busy.value) return {success: false, message: '上一项操作尚未完成，请稍候'};
      busy.value = true;
      try { return await client.run(action, params); }
      finally { busy.value = false; }
    }
    async function load() {
      error.value = '';
      try { await run(); } catch { error.value = '记录读取失败，请重试。'; }
    }
    onMounted(load);
    return () => h('section', {class: 'sbb-shell'}, [
      h('style', '.sbb-shell{padding:20px 24px;min-height:300px}.sbb-shell-header{display:flex;align-items:center;gap:12px;padding-bottom:18px}.sbb-shell-header h2{font-size:20px;flex:1;margin:0}.sbb-shell .sbb-page{padding-bottom:0}@media(max-width:600px){.sbb-shell{padding:16px}}'),
      h('header', {class: 'sbb-shell-header'}, [h('h2', '115秒传助手'),
        props.show_switch ? v('VBtn', {variant: 'text', prependIcon: 'mdi-cog-outline', onClick: () => emit('switch')}, '设置') : null,
        v('VBtn', {variant: 'text', icon: 'mdi-close', 'aria-label': '关闭', onClick: () => emit('close')})]),
      !loaded.value && !error.value ? h('p', {role: 'status'}, '正在读取处理记录…') : null,
      error.value ? v('VAlert', {type: 'error', variant: 'tonal'}, [error.value, v('VBtn', {onClick: load}, '重试')]) : null,
      ...page.value.map((config, i) => h(Node, {key: config.props?.id || i, config, run, busy: busy.value})),
    ]);
  },
});
