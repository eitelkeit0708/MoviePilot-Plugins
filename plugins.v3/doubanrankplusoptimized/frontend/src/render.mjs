import { h, resolveDynamicComponent } from 'vue'

export function v(name, props, children) {
  return h(resolveDynamicComponent(name), props, children == null ? undefined : () => children)
}

// Render the plugin's existing form and cards with the host's Vuetify components.
export function render(node, { model, onAction } = {}) {
  const attrs = { ...node.props }
  if (model && attrs.model) {
    const key = attrs.model
    delete attrs.model
    attrs.modelValue = model[key]
    attrs['onUpdate:modelValue'] = value => { model[key] = value }
  }
  if (node.events?.click && onAction) {
    attrs.onClick = () => onAction(node.events.click)
    if (node.component === 'VDialogCloseBtn') attrs['aria-label'] = '删除历史记录'
  }
  const children = [
    node.text,
    ...(node.content || []).map(child => render(child, { model, onAction })),
  ].filter(child => child != null)
  return node.component.startsWith('V')
    ? v(node.component, attrs, children.length ? children : undefined)
    : h(node.component, attrs, children)
}
