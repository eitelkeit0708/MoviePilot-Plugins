import {defineComponent, h, reactive, ref, onMounted, resolveDynamicComponent} from 'vue';
import {removeRoute} from './client.mjs';

const v = (name, props, children) => h(resolveDynamicComponent(name), props, children === undefined ? undefined : () => children);
export default defineComponent({
  name: 'SbbConfig', props: {api: Object, pluginId: String, initialConfig: Object}, emits: ['save', 'switch', 'close'],
  setup(props, {emit}) {
    const model = reactive({}), ready = ref(false), error = ref('');
    async function load() {
      error.value = '';
      try {
        const response = await props.api.get(`plugin/form/${props.pluginId || 'SymediaBatchBridge'}`);
        if (!response?.model) throw new Error('No configuration');
        Object.assign(model, response.model, props.initialConfig || {});
        ready.value = true;
      } catch { error.value = '配置读取失败，请重试；不会使用空配置覆盖原设置。'; }
    }
    onMounted(load);
    const field = (key, label, extra = {}) => v('VTextField', {label, 'aria-label': label, modelValue: model[key],
      'onUpdate:modelValue': value => {model[key] = value;}, ...extra});
    const toggle = (key, label, hint = '') => v('VSwitch', {label, hint, persistentHint: Boolean(hint), modelValue: Boolean(model[key]),
      'onUpdate:modelValue': value => {model[key] = value;}});
    return () => h('section', {class: 'sbb-config'}, [
      h('style', '.sbb-config{padding:24px}.sbb-config header{display:flex;align-items:center;gap:12px;margin-bottom:20px}.sbb-config h2{font-size:20px;flex:1;margin:0}.sbb-config .sbb-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0}.sbb-config .sbb-route{padding:18px;border:1px solid rgba(var(--v-border-color),.2);border-radius:6px;margin:16px 0}.sbb-config .sbb-config-actions{display:flex;justify-content:space-between;margin-top:24px}@media(max-width:650px){.sbb-config .sbb-grid{grid-template-columns:1fr}}'),
      h('header', [h('h2', '115秒传助手 · 设置'), v('VBtn', {variant: 'text', icon: 'mdi-close', 'aria-label': '关闭', onClick: () => emit('close')})]),
      error.value ? v('VAlert', {type: 'error'}, [error.value, v('VBtn', {onClick: load}, '重试')]) : null,
      !ready.value ? h('p', '正在读取配置…') : h('div', [
        v('VAlert', {type: 'info', variant: 'tonal'}, '使用 MP 内置 115，与 CD2 保持同一账号。暂存目录不要加入 Symedia 监控。'),
        toggle('enabled', '启用批次移交'),
        h('div', {class: 'sbb-grid'}, [field('cd2_prefix', 'CD2 中的 115 根目录', {hint: '令牌根目录为 115 时填 /', persistentHint: true}),
          field('cd2_address', 'CD2 gRPC 地址'), field('cd2_token', 'CD2 API 令牌', {type: 'password', autocomplete: 'off'}),
          field('interval', '检查间隔（分钟）', {type: 'number', min: 1, max: 60})]),
        toggle('notify', 'MP 通知：移交成功、处理异常、转普通上传'),
        toggle('first_upload_delay', '首次 SHA 计算完成后延迟上传', '仅应用于之后首次计算 SHA 的文件；每个文件独立计时，已有上传记录与重试时间保留。'),
        field('first_upload_delay_minutes', '首次上传延迟（分钟）', {type: 'number', min: 1, max: 10080, disabled: !model.first_upload_delay}),
        toggle('cleanup_inbox', '定期清理已移交的空目录', '作品目录间隔至少 5 分钟两次核实为空后退役回收；旧批次遵循保留天数。含任何文件的目录保留。'),
        field('inbox_retention_days', '旧布局空目录保留天数', {type: 'number', min: 1, max: 365}),
        toggle('delete_local', '移交后删除本地整理副本', '仅应用于之后接管的批次；不操作下载目录，清理失败一小时后自动重试。'),
        toggle('scan_existing_once', '保存后处理一次现存文件', '接管有 MP 记录的现存整理文件。保存后排队，开关自动复位。'),
        h('h3', '目录映射'),
        ...Array.from({length: Math.min(16, Math.max(1, Number(model.route_count || 1)))}, (_, offset) => {
          const index = offset + 1, prefix = index === 1 ? '' : `route_${index}_`;
          return h('div', {class: 'sbb-route', key: index}, [
            h('div', {class: 'sbb-grid'}, [field(prefix + 'route_name', '路线名称'),
              v('VBtn', {variant: 'text', disabled: Number(model.route_count || 1) <= 1, onClick: () => removeRoute(model, index)}, '移除路线'),
              field(prefix + 'local_root', 'MP 本地整理目录'), field(prefix + 'staging', '115 暂存目录')]),
            field(prefix + 'inbox', 'Symedia 待归档目录（CD2 路径）')]);
        }),
        v('VBtn', {variant: 'outlined', prependIcon: 'mdi-plus', disabled: Number(model.route_count || 1) >= 16,
          onClick: () => {model.route_count = Math.min(16, Number(model.route_count || 1) + 1);}}, '添加路线'),
        h('p', {style: 'margin:16px 0'}, '修改或移除路线仅影响新批次，已有批次沿原路线完成。'),
        v('VAlert', {variant: 'tonal', type: 'warning'}, '同一个本地整理目录只交给本插件处理；不要同时运行其他上传或移动任务。请使用 MP 复制或硬链接整理。'),
        h('p', {style: 'margin-top:16px'}, '每个文件尝试秒传 24 次，间隔 1 小时；最后一次未命中再等 1 小时才普通上传。接口报错不计次数，重启保留进度。'),
        h('div', {class: 'sbb-config-actions'}, [v('VBtn', {variant: 'text', onClick: () => emit('switch')}, '查看记录'),
          v('VBtn', {color: 'primary', onClick: () => emit('save', {...model})}, '保存')]),
      ]),
    ]);
  },
});
