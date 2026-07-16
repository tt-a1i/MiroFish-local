<template>
  <aside class="history-panel-compat" aria-hidden="true"></aside>
</template>

<script setup>
/**
 * 兼容旧开发会话中的 HMR 模块请求。
 *
 * 当前历史记录面板已经内联到 MainView.vue；保留这个空组件可以避免浏览器或 Vite
 * 缓存仍请求 /src/components/HistoryPanel.vue 时触发 ENOENT。
 */
</script>

<style scoped>
.history-panel-compat {
  display: none;
}
</style>
