<script setup>
defineProps({status:Object});
</script>
<template>
  <div class="sb-status" role="status" aria-live="polite">
    <span v-if="status.loading.value">读取当前有效配置…</span>
    <template v-else-if="status.current.value"><strong>当前有效配置</strong> · 版本 {{status.current.value.revision}} · 运行代次 {{status.health.value?.generation}}<p class="sb-muted sb-value">{{status.current.value.digest}}</p><p>普通工作：{{status.health.value?.ordinary_work_active?'运行':'关闭 / 受阻'}} · dry-run：{{status.health.value?.dry_run?'是':'否'}} · 已有任务安全保护：{{status.health.value?.safety_active?'运行':'未运行'}}</p><p v-if="status.receipt.value">当前引用回执 {{status.receipt.value.receipt_id}} · {{status.receipt.value.state}}</p><p class="sb-muted">当前读取不代表某次旧 Save 已成功；宿主关闭配置窗口后，可重新打开或显式刷新核对。</p></template>
    <p v-if="status.error.value" class="sb-error">保存结果未验证 · {{status.error.value}}</p>
    <p v-for="reason in status.health.value?.errors||[]" :key="reason" class="sb-error">{{reason}}</p>
  </div>
</template>
