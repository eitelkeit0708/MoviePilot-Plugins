import { defineComponent, h, onMounted, ref } from 'vue'
import { render, v } from './render.mjs'

export default defineComponent({
  props: { api: Object, pluginId: String, show_switch: Boolean },
  emits: ['switch', 'close'],
  setup(props, { emit }) {
    const result = ref(null)
    const busy = ref(false)
    const error = ref('')
    const notice = ref('')
    const pageSize = ref(20)
    const period = ref('all')
    const start = ref('')
    const end = ref('')
    let applied = {}
    const base = `plugin/${props.pluginId || 'DoubanRankPlusOptimized'}/history-page`

    async function fetchPage(page, dates) {
      const response = await props.api.get(base, {
        params: { page, page_size: pageSize.value, ...dates }, feedback: 'silent',
      })
      if (!response?.success) throw new Error(response?.message || '历史记录读取失败')
      result.value = response.data
    }

    async function load(page = 1, dates = applied) {
      if (busy.value) return
      error.value = ''
      notice.value = ''
      busy.value = true
      try {
        await fetchPage(page, dates)
        applied = dates
      } catch {
        error.value = '历史记录读取失败，请重试'
      } finally {
        busy.value = false
      }
    }

    function filter(event) {
      event.preventDefault()
      if (period.value === 'all') return load(1, {})
      if (!start.value || (period.value === 'range' && !end.value)) {
        error.value = period.value === 'range' ? '请选择开始和结束日期' : '请选择日期'
        return
      }
      if (period.value === 'range' && start.value > end.value) {
        error.value = '结束日期不能早于开始日期'
        return
      }
      return load(1, { start: start.value, ...(period.value === 'range' ? { end: end.value } : {}) })
    }

    async function runAction(action) {
      if (busy.value) return
      busy.value = true
      error.value = ''
      notice.value = ''
      try {
        const response = action.method === 'get'
          ? await props.api.get(action.api, { params: action.params, feedback: 'silent' })
          : await props.api.post(action.api, action.params, { feedback: 'silent' })
        if (!response?.success) {
          error.value = response?.message || '操作未完成'
          return
        }
        notice.value = response.message
        await fetchPage(result.value.page, applied)
      } catch {
        error.value = '请求未完成，请刷新历史核对结果'
      } finally {
        busy.value = false
      }
    }

    const select = (label, value, items, onChange) => h('label', { class: 'douban-history-field' }, [
      h('span', label),
      h('select', { value, 'aria-label': label, onChange: event => onChange(event.target.value), disabled: busy.value },
        items.map(item => h('option', { value: item.value ?? item }, item.title ?? String(item)))),
    ])
    const dateInput = (label, model) => h('label', { class: 'douban-history-field' }, [
      h('span', label),
      h('input', {
        type: 'date', value: model.value, disabled: busy.value,
        onInput: event => { model.value = event.target.value },
      }),
    ])
    const pagination = () => result.value.pages > 1 ? v('VPagination', {
      modelValue: result.value.page, length: result.value.pages, totalVisible: 5,
      density: 'compact', disabled: busy.value, 'onUpdate:modelValue': page => load(page),
      'aria-label': '历史记录分页',
    }) : null

    onMounted(() => load())
    return () => h('section', { class: 'douban-history pa-4 pa-sm-6', 'aria-busy': busy.value }, [
      h('style', '.douban-history .douban-history-filters{display:flex;align-items:flex-end;flex-wrap:wrap;gap:10px 12px}.douban-history .douban-history-field{display:grid;gap:3px;font-size:12px}.douban-history .douban-history-field :is(input,select){min-height:36px;border:1px solid rgba(var(--v-border-color),.45);border-radius:5px;padding:0 8px;font-size:14px;color:inherit;background:transparent}.douban-history .douban-history-field select{min-width:110px;appearance:auto}.v-theme--dark .douban-history .douban-history-field :is(input,select){color-scheme:dark}.douban-history .douban-history-field :is(input,select):focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:2px}'),
      h('header', { class: 'd-flex align-center ga-2 mb-5' }, [
        h('h2', { class: 'text-h6 flex-grow-1' }, '豆瓣榜单订阅Plus优化版'),
        props.show_switch ? v('VBtn', { variant: 'text', prependIcon: 'mdi-cog-outline', onClick: () => emit('switch') }, '设置') : null,
        v('VBtn', { variant: 'text', icon: 'mdi-close', 'aria-label': '关闭', onClick: () => emit('close') }),
      ]),
      result.value ? render(result.value.statistics) : null,
      h('section', { class: 'mt-5', 'aria-label': '历史记录' }, [
        h('h3', { class: 'text-subtitle-1 mb-3' }, result.value?.title || '历史记录'),
        h('form', { class: 'douban-history-filters mb-3', onSubmit: filter }, [
          select(
            '每页条数', pageSize.value, result.value?.page_sizes || [20, 50, 100],
            value => { pageSize.value = Number(value); load() },
          ),
          select('日期', period.value, [
            { title: '全部日期', value: 'all' },
            { title: '指定日期', value: 'day' },
            { title: '日期范围', value: 'range' },
          ], value => {
            period.value = value
            if (value === 'all') {
              start.value = ''
              end.value = ''
              load(1, {})
            }
          }),
          period.value !== 'all' ? dateInput(period.value === 'day' ? '指定日期' : '开始日期', start) : null,
          period.value === 'range' ? dateInput('结束日期', end) : null,
          period.value !== 'all' ? v('VBtn', { type: 'submit', color: 'primary', variant: 'tonal', disabled: busy.value }, '筛选') : null,
        ]),
        error.value ? v('VAlert', { type: 'error', variant: 'tonal', class: 'mb-3' }, [
          error.value, error.value.includes('失败') || error.value.startsWith('请求') ? v('VBtn', {
            variant: 'text', disabled: busy.value, onClick: () => load(result.value?.page),
          }, '刷新') : null,
        ]) : null,
        notice.value ? h('p', { role: 'status', class: 'mb-2' }, notice.value) : null,
        !result.value && busy.value ? h('p', { role: 'status' }, '正在读取历史…') : null,
        result.value ? [
          h('div', { class: 'd-flex align-center justify-space-between flex-wrap ga-2 mb-2' }, [
            h('span', { class: 'text-body-2', role: 'status' },
              [applied.start && (applied.end ? `${applied.start} 至 ${applied.end}` : applied.start),
                `共 ${result.value.total} 条`].filter(Boolean).join(' · ')),
            pagination(),
          ]),
          h('div', { class: 'grid gap-3 grid-info-card' }, result.value.records.map(record =>
            h('div', { key: record.key, class: 'd-grid' }, [render(record.card, { onAction: runAction })]),
          )),
          !result.value.records.length ? h('p', { class: 'text-center py-8' }, '没有符合条件的历史记录') : null,
        ] : null,
      ]),
    ])
  },
})
