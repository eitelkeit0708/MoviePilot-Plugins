import { defineComponent, h, onMounted, reactive, ref } from 'vue'
import { render, v } from './render.mjs'

export default defineComponent({
  props: { api: Object, pluginId: String, initialConfig: Object },
  emits: ['save', 'switch', 'close'],
  setup(props, { emit }) {
    const model = reactive({})
    const form = ref(null)
    const error = ref('')
    async function load() {
      error.value = ''
      try {
        const response = await props.api.get(`plugin/form/${props.pluginId || 'DoubanRankPlusOptimized'}`)
        const result = response?.success ? response.data : response
        if (!Array.isArray(result?.conf) || !result.model) throw new Error('Missing form')
        Object.assign(model, result.model, props.initialConfig)
        form.value = result.conf
      } catch {
        error.value = '配置读取失败'
      }
    }
    onMounted(load)
    return () => h('div', { class: 'pa-5' }, [
      h('div', { class: 'd-flex align-center justify-space-between mb-4' }, [
        h('h2', { class: 'text-h6' }, '豆瓣榜单订阅Plus优化版 · 设置'),
        v('VBtn', { variant: 'text', icon: 'mdi-close', 'aria-label': '关闭', onClick: () => emit('close') }),
      ]),
      error.value ? v('VAlert', { type: 'error', variant: 'tonal' }, [
        error.value, v('VBtn', { variant: 'text', onClick: load }, '重试'),
      ]) : null,
      form.value ? [
        ...form.value.map(node => render(node, { model })),
        h('div', { class: 'd-flex justify-space-between mt-4' }, [
          v('VBtn', { variant: 'text', onClick: () => emit('switch') }, '查看历史'),
          v('VBtn', { color: 'primary', onClick: () => emit('save', { ...model }) }, '保存'),
        ]),
      ] : !error.value ? h('p', { role: 'status' }, '正在读取设置…') : null,
    ])
  },
})
