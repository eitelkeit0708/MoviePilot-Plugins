import {defineComponent, h, ref, resolveDynamicComponent} from 'vue';

// Render our existing presentation schema, but keep component/scroll state
// mounted. No eval, raw HTML, host monkeypatch or host-wide progress dialog.
const Node = defineComponent({
  name: 'SbbNode', props: ['config', 'run', 'busy'],
  setup(props) {
    const waiting = ref(false), feedback = ref('');
    return () => {
      const item = props.config, attrs = {...(item.props || {})};
      const request = item.events?.click;
      if (request) {
        attrs.loading = waiting.value;
        // Keep keyboard focus while suppressing duplicate actions.
        attrs['aria-disabled'] = Boolean(attrs.disabled || props.busy);
        attrs.onClick = async () => {
          if (waiting.value || props.busy) return;
          waiting.value = true; feedback.value = '';
          try {
            const result = await props.run(request.api.split('/').at(-1), request.params);
            feedback.value = result.message || '';
          } catch {
            feedback.value = '请求未完成；当前页面和记录已保留。可刷新核对结果，请勿重复确认删除。';
          } finally { waiting.value = false; }
        };
      }
      const children = [item.text, ...(item.content || []).map((config, i) =>
        h(Node, {config, run: props.run, busy: props.busy, key: config.props?.id || `${config.component}-${i}`}))].filter(x => x != null);
      const rendered = h(resolveDynamicComponent(item.component), attrs,
        children.length ? (item.component.startsWith('V') ? () => children : children) : undefined);
      return feedback.value ? h('div', {class: 'sbb-control-result'}, [rendered,
        h('div', {role: 'status', style: 'font-size:12px;white-space:normal;overflow-wrap:anywhere;max-width:32rem'}, feedback.value)]) : rendered;
    };
  },
});
export default Node;
