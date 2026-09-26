<script setup>
defineProps({status:Object});
</script>
<template>
  <div class="sb-status" role="status" aria-live="polite">
    <span v-if="status.loading.value">读取当前有效配置…</span>
    <template v-else-if="status.current.value"><strong>{{status.health.value?.ordinary_work_active?'正在运行':status.health.value?.dry_run?'演练模式':'追踪未运行'}}</strong><span class="sb-muted">{{status.health.value?.dry_run?'不执行普通下载和交付':status.health.value?.ordinary_work_active?'按已保存策略处理':'等待启用或解决运行问题'}}</span><details><summary>运行详情</summary><p>任务保护{{status.health.value?.safety_active?'已运行':'未运行'}}，与普通追踪独立。</p><p>配置版本 {{status.current.value.revision}} · 运行代次 {{status.health.value?.generation}}</p><p class="sb-muted sb-value">{{status.current.value.digest}}</p><p v-if="status.receipt.value">配置回执 {{status.receipt.value.receipt_id}} · {{status.receipt.value.state}}</p></details></template>
    <p v-if="status.error.value" class="sb-error">保存结果未验证 · {{status.error.value}}</p>
    <p v-if="status.saveState?.value" role="status">{{status.saveState.value}}</p>
    <p v-for="reason in status.health.value?.errors||[]" :key="reason" class="sb-error">{{reason}}</p>
  </div>
</template>
