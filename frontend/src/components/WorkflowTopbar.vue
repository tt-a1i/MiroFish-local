<template>
  <header class="workflow-topbar">
    <button type="button" class="back-brand-btn" @click="startNewSession" title="返回传播推演">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <path d="M15 18l-6-6 6-6"/>
      </svg>
      <span>传播推演</span>
    </button>

    <nav class="workflow-nav" aria-label="推演流程">
      <template v-for="(step, idx) in steps" :key="step.index">
        <button
          type="button"
          class="workflow-nav-item"
          :class="{
            active: currentStep === step.index,
            completed: currentStep > step.index,
            disabled: !canNavigate(step.index)
          }"
          :disabled="!canNavigate(step.index)"
          @click="navigateToStep(step.index)"
        >
          {{ step.label }}
        </button>
        <span v-if="idx < steps.length - 1" class="workflow-nav-arrow">»</span>
      </template>
    </nav>

    <div class="topbar-actions" aria-hidden="true"></div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  currentStep: { type: Number, required: true },
  projectId: { type: String, default: '' },
  simulationId: { type: String, default: '' },
  reportId: { type: String, default: '' }
})

const emit = defineEmits(['missing-report'])
const router = useRouter()

const steps = [
  { index: 1, label: '群体构建' },
  { index: 2, label: '环境搭建' },
  { index: 3, label: '双平台推演' },
  { index: 4, label: '生成报告' },
  { index: 5, label: '深入对话' }
]

const availableProjectId = computed(() => props.projectId)
const availableSimulationId = computed(() => props.simulationId)
const availableReportId = computed(() => props.reportId)

const startNewSession = () => {
  router.push('/process/new')
}

const canNavigate = (step) => {
  if (step === props.currentStep) return true
  if (step === 1) return !!availableProjectId.value
  if (step === 2 || step === 3) return !!availableSimulationId.value
  if (step === 4 || step === 5) return !!availableReportId.value || !!availableSimulationId.value
  return false
}

const navigateToStep = (step) => {
  if (!canNavigate(step)) return

  if (step === 1 && availableProjectId.value) {
    router.push({ name: 'Process', params: { projectId: availableProjectId.value } })
    return
  }

  if (step === 2 && availableSimulationId.value) {
    router.push({ name: 'Simulation', params: { simulationId: availableSimulationId.value } })
    return
  }

  if (step === 3 && availableSimulationId.value) {
    router.push({ name: 'SimulationRun', params: { simulationId: availableSimulationId.value } })
    return
  }

  if (step === 4) {
    if (availableReportId.value) {
      router.push({ name: 'Report', params: { reportId: availableReportId.value } })
    } else {
      emit('missing-report', step)
    }
    return
  }

  if (step === 5) {
    if (availableReportId.value) {
      router.push({ name: 'Interaction', params: { reportId: availableReportId.value } })
    } else {
      emit('missing-report', step)
    }
  }
}
</script>

<style scoped>
.workflow-topbar {
  height: 56px;
  border-bottom: 1px solid #EAEAEA;
  background: #FFFFFF;
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto minmax(180px, 1fr);
  align-items: center;
  padding: 0 24px;
  flex-shrink: 0;
  z-index: 110;
}

.back-brand-btn {
  justify-self: start;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  color: #111827;
  font-size: 18px;
  font-weight: 800;
  cursor: pointer;
  padding: 8px 4px;
  transition: color 0.2s;
  font-family: inherit;
}

.back-brand-btn:hover {
  color: #1677FF;
}

.workflow-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-width: 0;
  white-space: nowrap;
}

.workflow-nav-item {
  border: none;
  background: transparent;
  display: inline-flex;
  align-items: center;
  color: #9CA3AF;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  border-radius: 999px;
  padding: 8px 0;
  cursor: pointer;
  transition: color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
  font-family: inherit;
}

.workflow-nav-item:hover:not(:disabled) {
  color: #1677FF;
}

.workflow-nav-item.active {
  color: #1677FF;
  background: #EFF6FF;
  box-shadow: 0 8px 20px rgba(22, 119, 255, 0.12);
  padding: 8px 18px;
}

.workflow-nav-item.completed {
  color: #4B5563;
}

.workflow-nav-item.disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.workflow-nav-arrow {
  color: #C7CDD8;
  font-weight: 500;
}

.topbar-actions {
  justify-self: end;
}

@media (max-width: 960px) {
  .workflow-topbar {
    grid-template-columns: 1fr;
    height: auto;
    gap: 10px;
    padding: 12px 16px;
  }

  .back-brand-btn,
  .topbar-actions {
    display: none;
  }

  .workflow-nav {
    justify-content: flex-start;
    overflow-x: auto;
    padding-bottom: 2px;
  }
}
</style>
