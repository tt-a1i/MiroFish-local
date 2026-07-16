<template>
  <div class="step1-root">

    <!-- 阶段1：输入阶段 -->
    <div v-if="!seedResult && !ontologyGenerating && currentPhase < 0" class="phase-input">
      <div class="input-center">
        <!-- 标题区 -->
        <div class="hero-section">
          <h1 class="hero-title"><span class="title-news">News</span><span class="title-power">Power.</span><span class="title-suffix">传播推演</span></h1>
          <p class="hero-subtitle">输入任意事件，即刻推演未来</p>
          <div class="step-indicator landing-step-indicator">
            <span class="step-dot active">01 事件背景</span>
            <span class="step-line">———</span>
            <span class="step-dot">02 推演方向</span>
          </div>
        </div>

        <!-- 输入卡片 -->
        <div class="input-card">
          <!-- 文字输入区 -->
          <div v-if="shouldShowTextInput" class="text-input-section" @click="keywordTextarea?.focus()">
            <textarea
              ref="keywordTextarea"
              v-model="searchQuery"
              class="keyword-textarea"
              aria-label="推演主题输入框"
              placeholder=""
              :disabled="isBusy"
              @input="onTextInput"
              @keydown.tab="handleRecommendationTab"
            ></textarea>
            <div
              v-if="showRecommendationHint"
              class="recommendation-ghost"
              aria-hidden="true"
            >
              <Transition name="recommendation-roll" mode="out-in">
                <span :key="activeRecommendationIndex" class="recommendation-text">
                  {{ activeRecommendation.text }}
                </span>
              </Transition>
              <span class="tab-hint">tab键直接填入</span>
            </div>
          </div>

          <!-- 分隔线 -->
          <div class="input-divider"></div>

          <!-- 文件上传区 -->
          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".pdf,.md,.txt"
            class="hidden-input"
            :disabled="isBusy || searchQuery.trim().length > 0"
            @change="handleFileSelect"
          />
          <div v-if="shouldShowUploadSection" class="file-upload-section">
            <div
              class="upload-zone"
              :class="{
                'drag-over': isDragOver,
                'has-files': files.length > 0 && !showAnalysisLog,
                streaming: showAnalysisLog
              }"
              @click="!searchQuery.trim() && !showAnalysisLog && triggerFileInput()"
              @dragover.prevent="handleDragOver"
              @dragleave.prevent="handleDragLeave"
              @drop.prevent="handleDrop"
            >
              <!-- 分析日志 -->
              <div v-if="showAnalysisLog" class="upload-stream-content">
                <div v-if="files.length > 0" class="file-list streaming-file-list">
                  <div v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-card">
                    <div class="file-info">
                      <span class="file-type-tag">{{ file.name.split('.').pop().toUpperCase() }}</span>
                      <span class="file-name">{{ file.name }}</span>
                      <span class="file-size">{{ (file.size / 1024).toFixed(1) }} KB</span>
                    </div>
                    <button type="button" class="file-remove-btn" :disabled="isBusy" @click.stop="removeFile(index)">×</button>
                  </div>
                </div>
                <div ref="analysisLogPanel" class="analysis-log-panel">
                  <div class="analysis-log-header">
                    <div class="analysis-title-group">
                      <span class="analysis-live-dot"></span>
                      <span class="analysis-title">{{ inputMode === 'web_search' ? '联网查询与总结流程' : '文件解析与总结流程' }}</span>
                    </div>
                    <span class="analysis-progress">{{ analysisProgress }}%</span>
                  </div>
                  <div class="analysis-progress-bar">
                    <span :style="{ width: `${analysisProgress}%` }"></span>
                  </div>
                  <div class="analysis-log-list">
                    <div
                      v-for="log in executionLogs"
                      :key="log.id"
                      class="analysis-log-item"
                      :class="log.level"
                    >
                      <span class="log-status-dot"></span>
                      <div class="log-body">
                        <div class="log-line">
                          <span class="log-time">{{ log.time }}</span>
                          <span class="log-message">{{ log.message }}</span>
                        </div>
                        <div v-if="log.sources?.length" class="log-source-list">
                          <a
                            v-for="source in log.sources"
                            :key="source.url || source.title"
                            class="log-source"
                            :href="source.url"
                            target="_blank"
                            rel="noopener noreferrer nofollow"
                            @click.stop
                          >
                            <span class="log-source-title">{{ source.title }}</span>
                            <span class="log-source-site">{{ source.site_name || source.date_published || '来源返回' }}</span>
                          </a>
                        </div>
                        <div v-if="log.suggestions?.length" class="log-suggestion-list">
                          <span
                            v-for="suggestion in log.suggestions"
                            :key="suggestion"
                            class="log-suggestion"
                          >{{ formatSuggestion(suggestion) }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <!-- 空状态 -->
              <div v-else-if="files.length === 0" class="upload-placeholder">
                <div class="upload-icon">↑</div>
                <div class="upload-title">拖拽 PDF / MD / TXT 到这里</div>
                <div class="upload-hint">或点击选择文件</div>
              </div>
              <!-- 文件列表 -->
              <div v-else class="file-list">
                <div v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-card">
                  <div class="file-info">
                    <span class="file-type-tag">{{ file.name.split('.').pop().toUpperCase() }}</span>
                    <span class="file-name">{{ file.name }}</span>
                    <span class="file-size">{{ (file.size / 1024).toFixed(1) }} KB</span>
                  </div>
                  <button type="button" class="file-remove-btn" :disabled="isBusy" @click.stop="removeFile(index)">×</button>
                </div>
              </div>
            </div>
          </div>

          <!-- 补充上下文（隐藏，仅在有内容时显示） -->
          <div v-if="additionalContext.trim()" class="context-row">
            <textarea
              v-model="additionalContext"
              class="context-textarea"
              placeholder="补充上下文（可选）：关注的时间线、角色、机构或传播平台"
              :disabled="isBusy"
              rows="2"
            ></textarea>
          </div>

          <!-- 提交按钮 -->
          <div class="submit-row">
            <button
              type="button"
              class="primary-btn submit-btn"
              :disabled="!canAnalyze"
              @click="analyzeSeed"
            >
              <span v-if="seedAnalyzing" class="spinner-sm"></span>
              {{ seedAnalyzing ? '正在分析...' : '生成推演方向' }}
            </button>
          </div>

          <p v-if="localError" class="error-text">{{ localError }}</p>
        </div>

        <SimulationAdvantagesSection />
      </div>
    </div>

    <!-- 阶段2：结果阶段 -->
    <div v-else-if="seedResult && currentPhase < 0" class="phase-result">
      <div class="result-center">
        <!-- 步骤指示器 -->
        <div class="step-indicator result-indicator">
          <span class="step-dot">01 事件背景</span>
          <span class="step-line">———</span>
          <span class="step-dot active">02 推演方向</span>
        </div>

        <!-- 事件摘要卡片 -->
        <div class="result-grid">
          <div class="result-left-column">
            <div class="result-card summary-card">
              <div class="card-header-row" @click="showSummary = !showSummary">
                <span class="card-section-title">完整事件内容</span>
                <button type="button" class="collapse-btn">{{ showSummary ? '▲' : '▼' }}</button>
              </div>
              <div v-show="showSummary" class="summary-content">
                <div class="summary-text markdown-body" v-html="renderedSummary"></div>
              </div>
            </div>
          </div>

          <div class="result-right-column">
            <!-- 推演方向区域 -->
            <div class="result-card direction-card">
              <div class="card-section-title">选择推演方向</div>
              <div class="suggestions-list">
                <div
                  v-for="suggestion in suggestions"
                  :key="suggestion"
                  class="suggestion-card"
                  :class="{ selected: selectedSuggestion === suggestion }"
                  @click="applySuggestion(suggestion)"
                >
                  <span class="suggestion-check">{{ selectedSuggestion === suggestion ? '◉' : '○' }}</span>
                  <span class="suggestion-text">{{ formatSuggestion(suggestion) }}</span>
                </div>
              </div>
              <!-- 自定义方向 -->
              <div class="custom-direction">
                <div class="custom-direction-label">自定义方向（如不需要推荐的建议，可以自行输入您想要推演的任意方向/主题）</div>
                <textarea
                  v-model="simulationRequirement"
                  class="direction-textarea"
                  placeholder="选择一条建议，或手动输入你要推演的问题"
                  :disabled="isBusy"
                  rows="4"
                ></textarea>
              </div>
            </div>

            <!-- 参考信息源 -->
            <div v-if="sources.length" class="result-card sources-card">
              <button
                type="button"
                class="card-header-row sources-header-row"
                :aria-expanded="showSources"
                @click="showSources = !showSources"
              >
                <span class="card-section-title">参考 {{ sources.length }} 条信息源</span>
                <span class="source-toggle-btn">
                  <span>{{ showSources ? '收起' : '展开' }}</span>
                  <svg
                    class="source-toggle-icon"
                    :class="{ expanded: showSources }"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </span>
              </button>
              <div v-show="showSources" class="sources-list">
                <a
                  v-for="(source, index) in sources"
                  :key="source.url || `${source.title}-${index}`"
                  class="source-item"
                  :href="source.url"
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                >
                  <span class="source-title">{{ source.title || source.name || source.url || '未命名来源' }}</span>
                  <span class="source-meta">{{ source.publisher || source.site_name || source.siteName || source.published_at || source.date_published || source.datePublished || '来源记录' }}</span>
                </a>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部按钮区 -->
        <div class="result-actions">
          <button type="button" class="back-btn" @click="resetToInput">返回修改</button>
          <button
            type="button"
            class="primary-btn start-btn"
            :disabled="!canGenerateOntology"
            @click="handleGenerateOntology"
          >
            <span v-if="ontologyGenerating" class="spinner-sm"></span>
            {{ ontologyGenerating ? '正在生成事件...' : '开始推演' }}
          </button>
        </div>

        <p v-if="localError" class="error-text">{{ localError }}</p>
      </div>
    </div>

    <!-- 阶段3：图谱构建工作台 -->
    <div v-else-if="currentPhase >= 0" class="phase-progress workbench-phase">
      <div class="scroll-container">
        <div class="step-card" :class="{ active: currentPhase === 0, completed: currentPhase > 0 }">
          <div class="step-card-header">
            <div class="step-info">
              <span class="step-num">01</span>
              <span class="step-title">事件生成</span>
            </div>
            <div class="step-status">
              <span v-if="currentPhase > 0" class="badge success">已完成</span>
              <span v-else class="badge processing">生成中</span>
            </div>
          </div>

          <div class="step-card-content">
            <p class="description">
              LLM 分析文档内容与模拟需求，提取出现实事件，自动生成合适的事件结构。
            </p>

            <div v-if="ontologyProgress && currentPhase === 0" class="progress-section">
              <div class="spinner-sm dark"></div>
              <span>{{ formatEventGenerationMessage(ontologyProgress.message) }}</span>
            </div>

            <div v-if="projectData?.ontology" class="ontology-preview">
              <div class="tags-container" :class="{ dimmed: selectedOntologyItem }">
                <span class="tag-label">生成的智能体类型</span>
                <div class="tags-list">
                  <span
                    v-for="entity in projectData.ontology.entity_types"
                    :key="entity.name"
                    class="entity-tag clickable"
                    @click="selectOntologyItem(entity, 'entity')"
                  >{{ ontologyEntityLabel(entity) }}</span>
                </div>
              </div>
              <div class="tags-container" :class="{ dimmed: selectedOntologyItem }">
                <span class="tag-label">生成的关系类型</span>
                <div class="tags-list">
                  <span
                    v-for="rel in projectData.ontology.edge_types"
                    :key="rel.name"
                    class="entity-tag clickable"
                    @click="selectOntologyItem(rel, 'relation')"
                  >{{ ontologyRelationLabel(rel) }}</span>
                </div>
              </div>
            </div>

            <div v-if="selectedOntologyItem" class="ontology-detail-overlay">
              <div class="detail-header">
                <div class="detail-title-group">
                  <span class="detail-type-badge">{{ selectedOntologyItem.itemType === 'entity' ? '实体' : '关系' }}</span>
                  <span class="detail-name">{{ selectedOntologyItem.itemType === 'entity' ? ontologyEntityLabel(selectedOntologyItem) : ontologyRelationLabel(selectedOntologyItem) }}</span>
                </div>
                <button class="close-btn" @click="selectedOntologyItem = null">×</button>
              </div>
              <div class="detail-body">
                <div class="detail-desc">{{ selectedOntologyItem.description }}</div>
                <div class="detail-section" v-if="selectedOntologyItem.attributes?.length">
                  <span class="section-label">属性</span>
                  <div class="attr-list">
                    <div v-for="attr in selectedOntologyItem.attributes" :key="attr.name" class="attr-item">
                      <span class="attr-name">{{ attr.name }}</span>
                      <span class="attr-type">({{ attr.type }})</span>
                      <span class="attr-desc">{{ attr.description }}</span>
                    </div>
                  </div>
                </div>
                <div class="detail-section" v-if="selectedOntologyItem.examples?.length">
                  <span class="section-label">示例</span>
                  <div class="example-list">
                    <span v-for="ex in selectedOntologyItem.examples" :key="ex" class="example-tag">{{ ex }}</span>
                  </div>
                </div>
                <div class="detail-section" v-if="selectedOntologyItem.source_targets?.length">
                  <span class="section-label">连接关系</span>
                  <div class="conn-list">
                    <div v-for="(conn, idx) in selectedOntologyItem.source_targets" :key="idx" class="conn-item">
                      <span class="conn-node">{{ ontologyConnectionLabel(conn, 'source') }}</span>
                      <span class="conn-arrow">→</span>
                      <span class="conn-node">{{ ontologyConnectionLabel(conn, 'target') }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="step-card" :class="{ active: currentPhase === 1, completed: currentPhase > 1 }">
          <div class="step-card-header">
            <div class="step-info">
              <span class="step-num">02</span>
              <span class="step-title">图谱构建</span>
            </div>
            <div class="step-status">
              <span v-if="currentPhase > 1" class="badge success">已完成</span>
              <span v-else-if="isGraphBuildFailed" class="badge error">失败</span>
              <span v-else-if="currentPhase === 1" class="badge processing">{{ buildProgress?.progress || 0 }}%</span>
              <span v-else class="badge pending">等待</span>
            </div>
          </div>

          <div class="step-card-content">
            <p class="description">
              基于生成的事件，将文档自动分块后调用 Zep 构建知识图谱，提取事件和关系，并形成时序记忆与社区摘要。
            </p>
            <p v-if="isGraphBuildFailed" class="progress-message error">{{ graphBuildFailureMessage }}</p>
            <p v-else-if="buildProgress?.message" class="progress-message inline">{{ buildProgress.message }}</p>

            <div class="stats-grid">
              <div class="stat-card">
                <span class="stat-value">{{ graphStats.nodes }}</span>
                <span class="stat-label">智能体节点</span>
              </div>
              <div class="stat-card">
                <span class="stat-value">{{ graphStats.edges }}</span>
                <span class="stat-label">关系边</span>
              </div>
              <div class="stat-card">
                <span class="stat-value">{{ graphStats.types }}</span>
                <span class="stat-label">智能体类型数</span>
              </div>
            </div>
          </div>
        </div>

        <div class="step-card" :class="{ active: currentPhase === 2, completed: currentPhase >= 2 }">
          <div class="step-card-header">
            <div class="step-info">
              <span class="step-num">03</span>
              <span class="step-title">构建完成</span>
            </div>
            <div class="step-status">
              <span v-if="currentPhase >= 2" class="badge accent">进行中</span>
              <span v-else class="badge pending">等待</span>
            </div>
          </div>

          <div class="step-card-content">
            <p class="description">{{ completionDescription }}</p>
            <button type="button" class="action-btn" :disabled="currentPhase < 2" @click="emit('next-step')">
              进入环境搭建 →
            </button>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { marked } from 'marked'
import SimulationAdvantagesSection from './SimulationAdvantagesSection.vue'
import {
  analyzeUploadedSeed,
  searchSeedByKeyword,
  streamAnalyzeUploadedSeed,
  streamSearchSeedByKeyword
} from '../api/graph'
import { translateEntityType, translateRelationType } from '../utils/entityTranslations.js'
import { formatSimulationRequirement } from '../utils/projectTitle.js'

const props = defineProps({
  currentPhase: { type: Number, default: -1 },
  projectData: Object,
  projectStatus: { type: String, default: '' },
  projectError: { type: String, default: '' },
  pendingUpload: Object,
  ontologyProgress: Object,
  buildProgress: Object,
  graphData: Object,
  systemLogs: { type: Array, default: () => [] }
})

const emit = defineEmits(['workspace-started', 'next-step', 'add-log'])

const inputMode = ref('web_search')
const searchQuery = ref('')
const files = ref([])
const additionalContext = ref('')
const seedResult = ref(null)
const selectedSuggestion = ref('')
const simulationRequirement = ref('')
const seedAnalyzing = ref(false)
const ontologyGenerating = ref(false)
const localError = ref('')
const isDragOver = ref(false)
const fileInput = ref(null)
const keywordTextarea = ref(null)
const analysisLogPanel = ref(null)
const selectedOntologyItem = ref(null)
const showSources = ref(false)
const showSummary = ref(true)
const executionLogs = ref([])
const analysisProgress = ref(0)
let analysisHeartbeatTimer = null
let recommendationTimer = null

const hotTopicRecommendations = [
  {
    text: '围绕张雪机车夺冠后的品牌声量、赛事规则讨论与国产品牌叙事，推演舆情热度和风险拐点。'
  },
  {
    text: '以福建漳州“泡药杨梅”食品安全整治为背景，推演地方回应、消费者信任与产区品牌修复路径。'
  },
  {
    text: '围绕湖南石门救灾女干部“金耳环”网暴事件，推演基层干部形象、平台治理和公众情绪的后续演化。'
  },
  {
    text: '模拟一次突发公共事件从短视频平台发酵到主流媒体介入后的传播链路和治理窗口。'
  }
]
const activeRecommendationIndex = ref(0)

const activeRecommendation = computed(() => hotTopicRecommendations[activeRecommendationIndex.value])
const showRecommendationHint = computed(() => {
  return shouldShowTextInput.value && !isBusy.value && !searchQuery.value.trim()
})

const rotateRecommendation = () => {
  activeRecommendationIndex.value = (activeRecommendationIndex.value + 1) % hotTopicRecommendations.length
}

const stopRecommendationRotation = () => {
  if (recommendationTimer) {
    clearInterval(recommendationTimer)
    recommendationTimer = null
  }
}

const startRecommendationRotation = () => {
  stopRecommendationRotation()
  recommendationTimer = setInterval(() => {
    if (showRecommendationHint.value) rotateRecommendation()
  }, 3200)
}

const resetToInput = () => {
  stopAnalysisHeartbeat()
  seedResult.value = null
  selectedSuggestion.value = ''
  simulationRequirement.value = ''
  localError.value = ''
  executionLogs.value = []
  analysisProgress.value = 0
}

const isBusy = computed(() => seedAnalyzing.value || ontologyGenerating.value || props.currentPhase >= 1)
const showAnalysisLog = computed(() => seedAnalyzing.value || executionLogs.value.length > 0)
const hasTextInput = computed(() => searchQuery.value.trim().length > 0)
const hasFileInput = computed(() => files.value.length > 0)
const shouldShowTextInput = computed(() => !hasFileInput.value)
const shouldShowUploadSection = computed(() => showAnalysisLog.value || !hasTextInput.value)

const suggestions = computed(() => {
  const value = seedResult.value?.simulation_suggestions || seedResult.value?.suggestions || []
  return value.filter(Boolean).slice(0, 3)
})

const sources = computed(() => {
  const value = seedResult.value?.seed_sources || seedResult.value?.sources || []
  return Array.isArray(value) ? value : []
})

const seedSummary = computed(() => {
  const result = seedResult.value || {}
  if (result.seed_input_mode === 'file_upload') {
    return result.seed_full_content_md || result.seed_summary_md || result.analysis_summary || '暂无内容。'
  }
  return result.seed_summary_md || result.seed_full_content_md || result.analysis_summary || '暂无内容。'
})

const renderedSummary = computed(() => {
  const md = seedSummary.value
  if (!md) return ''
  return marked(md)
})

const formatSuggestion = (text) => {
  return formatSimulationRequirement(text)
}

const ontologyEntityLabel = (entity = {}) => {
  return entity.display_name || translateEntityType(entity.name)
}

const ontologyRelationLabel = (relation = {}) => {
  return relation.display_name || translateRelationType(relation.name)
}

const ontologyConnectionLabel = (connection = {}, side) => {
  const displayKey = `${side}_display_name`
  return connection[displayKey] || translateEntityType(connection[side])
}

const formatEventGenerationMessage = (message) => {
  return (message || '正在生成事件...').replace(/本体/g, '事件').replace(/种子/g, '事件')
}

const currentProjectId = computed(() => seedResult.value?.project_id || props.projectData?.project_id || '')

const canAnalyze = computed(() => {
  if (isBusy.value) return false
  return searchQuery.value.trim().length > 0 || files.value.length > 0
})

const canGenerateOntology = computed(() => {
  return !!seedResult.value && !!currentProjectId.value && simulationRequirement.value.trim().length > 0 && !isBusy.value
})

const graphStats = computed(() => {
  const nodes = props.graphData?.node_count || props.graphData?.nodes?.length || 0
  const edges = props.graphData?.edge_count || props.graphData?.edges?.length || 0
  const types = props.projectData?.ontology?.entity_types?.length || 0
  return { nodes, edges, types }
})

const isGraphBuildFailed = computed(() => {
  return props.currentPhase === 1 && (
    props.projectStatus === 'failed' ||
    props.buildProgress?.status === 'failed' ||
    !!props.buildProgress?.error
  )
})

const graphBuildFailureMessage = computed(() => {
  return props.projectError ||
    props.buildProgress?.error ||
    props.buildProgress?.message ||
    '图谱构建失败，请检查配置或重新构建。'
})

const completionDescription = computed(() => {
  if (isGraphBuildFailed.value) return '图谱构建尚未成功，请先处理上一步失败原因后再进入环境搭建。'
  return '图谱构建已完成，请进入下一步进行模拟环境搭建。'
})

const resetAnalysisResult = () => {
  stopAnalysisHeartbeat()
  seedResult.value = null
  selectedSuggestion.value = ''
  localError.value = ''
  executionLogs.value = []
  analysisProgress.value = 0
}

const switchInputMode = (mode) => {
  if (inputMode.value === mode || isBusy.value) return
  inputMode.value = mode
  resetAnalysisResult()
  simulationRequirement.value = ''
  if (mode === 'web_search') {
    files.value = []
    if (fileInput.value) fileInput.value.value = ''
  } else {
    searchQuery.value = ''
  }
  emit('add-log', `Step1 input mode switched to ${mode}.`)
}

const onTextInput = () => {
  if (searchQuery.value.trim()) {
    inputMode.value = 'web_search'
  }
}

const getVisibleRecommendationText = () => {
  const visibleText = keywordTextarea.value
    ?.parentElement
    ?.querySelector('.recommendation-text')
    ?.textContent
    ?.trim()
  return visibleText || activeRecommendation.value?.text || ''
}

const fillActiveRecommendation = async () => {
  const recommendationText = getVisibleRecommendationText()
  if (!showRecommendationHint.value || !recommendationText) return
  searchQuery.value = recommendationText
  inputMode.value = 'web_search'
  resetAnalysisResult()
  await nextTick()
  keywordTextarea.value?.focus()
}

const handleRecommendationTab = (event) => {
  if (!showRecommendationHint.value) return
  event.preventDefault()
  fillActiveRecommendation()
}

const triggerFileInput = () => {
  if (!isBusy.value && !hasTextInput.value) fileInput.value?.click()
}

const addFiles = (newFiles) => {
  if (hasTextInput.value || showAnalysisLog.value) return
  resetAnalysisResult()
  const validFiles = newFiles.filter(file => {
    const ext = file.name.split('.').pop().toLowerCase()
    return ['pdf', 'md', 'txt'].includes(ext)
  })
  files.value.push(...validFiles)
  if (files.value.length > 0) {
    inputMode.value = 'file_upload'
  }
  if (validFiles.length !== newFiles.length) {
    localError.value = '仅支持 PDF、MD、TXT 文件。'
  } else {
    localError.value = ''
  }
}

const handleFileSelect = (event) => {
  addFiles(Array.from(event.target.files || []))
}

const handleDragOver = () => {
  if (!isBusy.value && !hasTextInput.value && !showAnalysisLog.value) isDragOver.value = true
}

const handleDragLeave = () => {
  isDragOver.value = false
}

const handleDrop = (event) => {
  isDragOver.value = false
  if (isBusy.value || hasTextInput.value || showAnalysisLog.value) return
  addFiles(Array.from(event.dataTransfer.files || []))
}

const removeFile = (index) => {
  files.value.splice(index, 1)
  if (files.value.length === 0) {
    inputMode.value = 'web_search'
  }
  resetAnalysisResult()
}

const formatClock = () => {
  const now = new Date()
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
}

const scrollAnalysisLogToBottom = async () => {
  await nextTick()
  const list = analysisLogPanel.value?.querySelector?.('.analysis-log-list')
  if (list) list.scrollTop = list.scrollHeight
}

const appendExecutionLog = (message, options = {}) => {
  const log = {
    id: `${Date.now()}-${executionLogs.value.length}`,
    time: formatClock(),
    level: options.level || 'info',
    message,
    sources: options.sources || [],
    suggestions: options.suggestions || []
  }
  executionLogs.value.push(log)
  if (typeof options.progress === 'number') {
    analysisProgress.value = Math.max(analysisProgress.value, Math.min(100, options.progress))
  }
  scrollAnalysisLogToBottom()
}

const stopAnalysisHeartbeat = () => {
  if (analysisHeartbeatTimer) {
    clearInterval(analysisHeartbeatTimer)
    analysisHeartbeatTimer = null
  }
}

const startAnalysisHeartbeat = () => {
  stopAnalysisHeartbeat()
  analysisHeartbeatTimer = setInterval(() => {
    if (!seedAnalyzing.value) {
      stopAnalysisHeartbeat()
      return
    }
    const waitingMessage = analysisProgress.value >= 55
      ? '大模型仍在生成完整事件 Markdown 与推演建议，请继续等待。'
      : '后台仍在处理资料，正在等待下一步结果返回。'
    appendExecutionLog(waitingMessage)
  }, 12000)
}

const handleAnalysisStreamEvent = (event) => {
  if (!event) return
  if (typeof event.progress === 'number') {
    analysisProgress.value = Math.max(analysisProgress.value, Math.min(100, event.progress))
  }
  if (event.event === 'sources') {
    appendExecutionLog(event.message || '联网查询返回来源', {
      progress: event.progress,
      sources: event.sources || []
    })
    return
  }
  if (event.event === 'progress') {
    appendExecutionLog(event.message || '后台处理中', {
      progress: event.progress,
      suggestions: event.suggestions || []
    })
    return
  }
  if (event.event === 'complete') {
    appendExecutionLog(event.message || '处理完成', {
      level: 'success',
      progress: 100
    })
    return
  }
  if (event.event === 'error') {
    appendExecutionLog(event.message || '处理失败', {
      level: 'error'
    })
  }
}

const analyzeSeedWithFallback = async () => {
  if (inputMode.value === 'web_search') {
    try {
      return await streamSearchSeedByKeyword({
        search_query: searchQuery.value.trim(),
        additional_context: additionalContext.value.trim()
      }, { onEvent: handleAnalysisStreamEvent })
    } catch (err) {
      if (executionLogs.value.some(log => log.level === 'error')) throw err
      appendExecutionLog('当前环境未能建立流式连接，切换为兼容分析流程。', { progress: 15 })
      const res = await searchSeedByKeyword({
        search_query: searchQuery.value.trim(),
        additional_context: additionalContext.value.trim()
      })
      appendExecutionLog('兼容流程已完成联网查询、总结和方向提炼。', { level: 'success', progress: 100 })
      return res.data
    }
  }

  try {
    return await streamAnalyzeUploadedSeed(buildUploadFormData(), { onEvent: handleAnalysisStreamEvent })
  } catch (err) {
    if (executionLogs.value.some(log => log.level === 'error')) throw err
    appendExecutionLog('当前环境未能建立流式连接，切换为兼容文件分析流程。', { progress: 15 })
    const res = await analyzeUploadedSeed(buildUploadFormData())
    appendExecutionLog('兼容流程已完成文件解析、总结和方向提炼。', { level: 'success', progress: 100 })
    return res.data
  }
}

const normalizeSeedResult = (data) => {
  const normalized = data || {}
  return {
    ...normalized,
    seed_summary_md: normalized.seed_summary_md || normalized.analysis_summary || '',
    seed_full_content_md: normalized.seed_full_content_md || normalized.full_content_md || '',
    simulation_suggestions: normalized.simulation_suggestions || normalized.suggestions || [],
    seed_sources: normalized.seed_sources || normalized.sources || []
  }
}

const analyzeSeed = async () => {
  localError.value = ''
  if (inputMode.value === 'web_search' && files.value.length > 0) {
    localError.value = '联网搜索模式不能携带文件。'
    return
  }
  if (inputMode.value === 'file_upload' && searchQuery.value.trim()) {
    localError.value = '文件上传模式不能携带搜索关键词。'
    return
  }
  seedAnalyzing.value = true
  executionLogs.value = []
  analysisProgress.value = 0
  appendExecutionLog(inputMode.value === 'web_search' ? '任务已提交，准备进行联网查询。' : '任务已提交，准备解析上传文件。', { progress: 3 })
  startAnalysisHeartbeat()
  emit('add-log', inputMode.value === 'web_search' ? 'Analyzing seed by web search...' : 'Analyzing uploaded seed files...')
  try {
    const data = await analyzeSeedWithFallback()
    seedResult.value = normalizeSeedResult(data)
    showSources.value = false
    if (suggestions.value.length > 0 && !simulationRequirement.value.trim()) {
      applySuggestion(suggestions.value[0])
    }
    emit('add-log', `Seed analyzed for project ${seedResult.value.project_id || 'unknown'}.`)
  } catch (err) {
    localError.value = err.message || '现实事件分析失败'
    emit('add-log', `Seed analysis failed: ${localError.value}`)
  } finally {
    seedAnalyzing.value = false
    stopAnalysisHeartbeat()
  }
}

const buildUploadFormData = () => {
  const formData = new FormData()
  files.value.forEach(file => formData.append('files', file))
  if (additionalContext.value.trim()) {
    formData.append('additional_context', additionalContext.value.trim())
  }
  return formData
}

const applySuggestion = (suggestion) => {
  const cleanSuggestion = formatSuggestion(suggestion)
  selectedSuggestion.value = suggestion
  simulationRequirement.value = cleanSuggestion
}

const handleGenerateOntology = async () => {
  if (!canGenerateOntology.value) return
  ontologyGenerating.value = true
  localError.value = ''
  emit('add-log', 'Generating ontology from confirmed simulation requirement...')
  const payload = {
    project_id: currentProjectId.value,
    simulation_requirement: simulationRequirement.value.trim()
  }
  if (additionalContext.value.trim()) {
    payload.additional_context = additionalContext.value.trim()
  }
  emit('workspace-started', {
    ...seedResult.value,
    project_id: payload.project_id,
    simulation_requirement: payload.simulation_requirement,
    ontology_payload: payload
  })
  ontologyGenerating.value = false
}

const selectOntologyItem = (item, type) => {
  selectedOntologyItem.value = { ...item, itemType: type }
}

watch(() => props.pendingUpload, (pending) => {
  if (!pending?.isPending) return
  if (pending.files?.length) {
    inputMode.value = 'file_upload'
    files.value = [...pending.files]
    searchQuery.value = ''
  }
  if (pending.simulationRequirement) {
    simulationRequirement.value = pending.simulationRequirement
  }
}, { immediate: true })

onMounted(() => {
  startRecommendationRotation()
  nextTick(() => {
    if (showRecommendationHint.value) keywordTextarea.value?.focus()
  })
})

onUnmounted(() => {
  stopAnalysisHeartbeat()
  stopRecommendationRotation()
})

</script>

<style scoped>
/* ===== 根容器 ===== */
.step1-root {
  height: 100%;
  background: #F5F7FA;
  overflow-y: auto;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

/* ===== 阶段1：输入 ===== */
.phase-input {
  min-height: 100%;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 56px 24px 72px;
}

.input-center {
  width: 100%;
  max-width: 1000px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* 标题区 */
.hero-section {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  margin-bottom: 28px;
  width: 100%;
}

.hero-title {
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  font-family: 'Inter', 'Noto Sans SC', system-ui, sans-serif;
  font-size: 48px;
  font-weight: 800;
  margin: 0;
  line-height: 1;
  letter-spacing: 0;
}

.title-news {
  color: #09090D;
}

.title-power {
  background: linear-gradient(90deg, #2244E8 0%, #278DF2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.title-suffix {
  color: #09090D;
  margin-left: 18px;
}

.hero-subtitle {
  font-size: 16px;
  line-height: 16px;
  color: #495770;
  margin: 0;
}

.step-indicator.landing-step-indicator {
  display: none;
}

.step-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.step-dot {
  font-size: 13px;
  font-weight: 600;
  color: #9CA3AF;
}

.step-dot.active {
  color: #1677FF;
}

.step-line {
  color: #D1D5DB;
  font-size: 13px;
  letter-spacing: 2px;
}

/* 输入卡片 */
.input-card {
  width: 100%;
  max-width: 800px;
  min-height: 340px;
  box-sizing: border-box;
  background: #FFFFFF;
  border: 1px solid #DDE6F5;
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(136, 166, 230, 0.12);
  padding: 20px 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}

.input-divider {
  display: none;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.text-input-section {
  position: relative;
  min-height: 132px;
}

.keyword-textarea {
  width: 100%;
  min-height: 132px;
  border: none;
  outline: none;
  resize: none;
  font-size: 16px;
  color: #1A1A2E;
  line-height: 24px;
  font-family: inherit;
  background: transparent;
  box-sizing: border-box;
  padding: 8px 8px 0;
}

.keyword-textarea::placeholder {
  color: #9CA3AF;
}

.recommendation-ghost {
  position: absolute;
  top: 8px;
  left: 8px;
  right: 8px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 0;
  border: none;
  background: transparent;
  color: #8A99B8;
  font-family: inherit;
  font-size: 16px;
  line-height: 24px;
  text-align: left;
  pointer-events: none;
}

.recommendation-text {
  min-width: 0;
  flex: 1;
  display: block;
}

.tab-hint {
  flex-shrink: 0;
  margin-top: 1px;
  padding: 2px 9px;
  border: 1px solid #DDE4EF;
  border-radius: 6px;
  background: #FFFFFF;
  color: #8A99B8;
  font-size: 13px;
  line-height: 18px;
  white-space: nowrap;
}

.recommendation-roll-enter-active,
.recommendation-roll-leave-active {
  transition: opacity 0.24s ease, transform 0.24s ease;
}

.recommendation-roll-enter-from {
  opacity: 0;
  transform: translateY(14px);
}

.recommendation-roll-leave-to {
  opacity: 0;
  transform: translateY(-14px);
}

.context-textarea {
  width: 100%;
  border: 1px solid #E8ECF0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  color: #1A1A2E;
  line-height: 1.5;
  resize: none;
  outline: none;
  font-family: inherit;
  background: #F9FAFB;
  box-sizing: border-box;
  transition: border-color 0.2s;
}

.context-textarea:focus {
  border-color: #1677FF;
  background: #FFFFFF;
}

.context-textarea::placeholder {
  color: #9CA3AF;
}

.context-row {
  margin-top: 4px;
}

/* 文件上传 */
.hidden-input {
  display: none;
}

.upload-zone {
  min-height: 112px;
  border: 1px dashed #C6D4EA;
  border-radius: 12px;
  background: #FFFFFF;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.upload-zone.drag-over {
  border-color: #1677FF;
  background: #EFF6FF;
}

.upload-zone.has-files {
  align-items: stretch;
  justify-content: flex-start;
}

.upload-zone.streaming {
  min-height: 224px;
  align-items: stretch;
  justify-content: stretch;
  cursor: default;
  border-style: solid;
  border-color: #C6D4EA;
  background: #FFFFFF;
}

.upload-stream-content {
  width: 100%;
  padding: 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.streaming-file-list {
  padding: 0;
}

.upload-stream-content .file-card {
  background: #F8FAFF;
}

.analysis-log-panel {
  width: 100%;
  min-height: 156px;
  max-height: 220px;
  padding: 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px solid #B8CAF2;
  border-radius: 12px;
  background: #F8FBFF;
}

.analysis-log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.analysis-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.analysis-live-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #1677FF;
  box-shadow: 0 0 0 4px rgba(22, 119, 255, 0.12);
  flex-shrink: 0;
}

.analysis-title {
  font-size: 13px;
  font-weight: 700;
  color: #1F2A44;
}

.analysis-progress {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: #1677FF;
  flex-shrink: 0;
}

.analysis-progress-bar {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: #E6EDF8;
}

.analysis-progress-bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #1942FF 0%, #60B0FF 100%);
  transition: width 0.25s ease;
}

.analysis-log-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 2px;
}

.analysis-log-item {
  display: grid;
  grid-template-columns: 8px 1fr;
  gap: 8px;
  align-items: start;
}

.log-status-dot {
  width: 7px;
  height: 7px;
  margin-top: 8px;
  border-radius: 999px;
  background: #8AA4D6;
}

.analysis-log-item.success .log-status-dot {
  background: #10B981;
}

.analysis-log-item.error .log-status-dot {
  background: #EF4444;
}

.log-body {
  min-width: 0;
}

.log-line {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.log-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #8A96AD;
  flex-shrink: 0;
}

.log-message {
  font-size: 12px;
  line-height: 1.55;
  color: #31405C;
}

.log-source-list,
.log-suggestion-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}

.log-source {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 9px;
  border: 1px solid #DDE7F6;
  border-radius: 8px;
  background: #FFFFFF;
  color: inherit;
  text-decoration: none;
}

.log-source-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
  color: #1F2A44;
}

.log-source-site {
  max-width: 108px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  color: #8A96AD;
  flex-shrink: 0;
}

.log-suggestion {
  padding: 7px 9px;
  border-radius: 8px;
  background: #EEF6FF;
  color: #2356B8;
  font-size: 12px;
  line-height: 1.45;
}

.upload-placeholder {
  text-align: center;
  color: #6B7280;
}

.upload-icon {
  font-size: 26px;
  color: #4E63B6;
  margin-bottom: 4px;
}

.upload-title {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}

.upload-hint {
  font-size: 12px;
  color: #9CA3AF;
  margin-top: 4px;
}

.file-list {
  width: 100%;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #FFFFFF;
  border: 1px solid #E8ECF0;
  border-radius: 8px;
  padding: 10px 12px;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.file-type-tag {
  font-size: 10px;
  font-weight: 700;
  color: #1677FF;
  background: #EFF6FF;
  padding: 2px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.file-name {
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-size: 11px;
  color: #9CA3AF;
  flex-shrink: 0;
}

.file-remove-btn {
  background: none;
  border: none;
  color: #9CA3AF;
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
  padding: 0 4px;
  flex-shrink: 0;
}

.file-remove-btn:hover {
  color: #EF4444;
}

/* 主按钮 */
.submit-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  min-height: 40px;
  margin-top: auto;
}

.primary-btn {
  background: linear-gradient(135deg, #1677FF, #6366F1);
  color: #FFFFFF;
  border: none;
  border-radius: 8px;
  padding: 12px 24px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-family: inherit;
}

.primary-btn:hover:not(:disabled) {
  opacity: 0.88;
}

.primary-btn:disabled {
  background: #D1D5DB;
  cursor: not-allowed;
}

.submit-btn {
  min-width: 132px;
  height: 36px;
  padding: 0 20px;
  border-radius: 8px;
  background: linear-gradient(90deg, #1942FF 0%, #60B0FF 100%);
  box-shadow: none;
}

.submit-btn:disabled {
  background: #EEF2F9;
  color: #9AA8BF;
  cursor: not-allowed;
  opacity: 1;
}

.error-text {
  color: #EF4444;
  font-size: 13px;
  margin: 0;
}

/* ===== 阶段2：结果 ===== */
.phase-result {
  min-height: 100%;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 40px 24px 60px;
}

.result-center {
  width: 100%;
  max-width: 1240px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.result-indicator {
  justify-content: center;
  margin-bottom: 8px;
}

.result-card {
  background: #FFFFFF;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  padding: 20px;
  border: 1px solid #E8ECF0;
}

.result-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(360px, 0.85fr);
  gap: 18px;
  align-items: start;
}

.result-left-column,
.result-right-column {
  min-width: 0;
}

.result-right-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-card,
.direction-card,
.sources-card {
  min-width: 0;
}

.card-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.card-section-title {
  font-size: 14px;
  font-weight: 700;
  color: #1A1A2E;
}

.collapse-btn {
  background: none;
  border: none;
  color: #9CA3AF;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.sources-header-row {
  width: 100%;
  min-height: 28px;
  gap: 16px;
  padding: 0;
  border: none;
  background: transparent;
  font-family: inherit;
  text-align: left;
}

.source-toggle-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 72px;
  height: 30px;
  padding: 0 8px 0 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #8FA4C4;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  transition: color 0.2s, background 0.2s;
}

.sources-header-row:hover .source-toggle-btn {
  background: #F3F7FF;
  color: #6F86AA;
}

.sources-header-row:focus-visible {
  outline: 2px solid rgba(22, 119, 255, 0.35);
  outline-offset: 2px;
}

.source-toggle-icon {
  transition: transform 0.2s ease;
}

.source-toggle-icon.expanded {
  transform: rotate(180deg);
}

.summary-content {
  margin-top: 12px;
}

.summary-text {
  background: #F9FAFB;
  border: 1px solid #E8ECF0;
  border-radius: 8px;
  padding: 14px;
  color: #374151;
  font-size: 13px;
  line-height: 1.7;
  max-height: min(58vh, 620px);
  overflow-y: auto;
  font-family: inherit;
}

.summary-text :deep(h1),
.summary-text :deep(h2),
.summary-text :deep(h3) {
  font-size: 14px;
  font-weight: 700;
  margin: 12px 0 6px;
  color: #1A1A2E;
}

.summary-text :deep(h1) { font-size: 16px; }

.summary-text :deep(p) { margin: 6px 0; }

.summary-text :deep(ul),
.summary-text :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.summary-text :deep(li) { margin: 3px 0; }

/* 推演方向 */
.suggestions-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}

.suggestion-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px;
  border: 1px solid #E5E7EB;
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.suggestion-card.selected {
  border: 2px solid #1677FF;
  background: #EFF6FF;
}

.suggestion-card:hover:not(.selected) {
  border-color: #93C5FD;
  background: #F8FAFF;
}

.suggestion-check {
  font-size: 16px;
  color: #1677FF;
  flex-shrink: 0;
  line-height: 1.4;
}

.suggestion-text {
  font-size: 13px;
  color: #374151;
  line-height: 1.6;
}

.custom-direction {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.custom-direction-label {
  font-size: 12px;
  font-weight: 600;
  color: #6B7280;
}

.direction-textarea {
  width: 100%;
  border: 1px solid #E8ECF0;
  border-radius: 8px;
  padding: 12px;
  font-size: 13px;
  color: #1A1A2E;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  font-family: inherit;
  background: #F9FAFB;
  box-sizing: border-box;
  transition: border-color 0.2s;
}

.direction-textarea:focus {
  border-color: #1677FF;
  background: #FFFFFF;
}

/* 信息源 */
.sources-card {
  border: 1px solid #E8ECF0;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.source-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid #E8ECF0;
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
  background: #F9FAFB;
  transition: border-color 0.2s, background 0.2s;
}

.source-item:hover {
  border-color: #1677FF;
  background: #EFF6FF;
}

.source-title {
  font-size: 13px;
  color: #1A1A2E;
  font-weight: 600;
}

.source-meta {
  font-size: 11px;
  color: #9CA3AF;
}

/* 底部按钮 */
.result-actions {
  display: flex;
  gap: 12px;
}

.back-btn {
  flex: 1;
  background: #FFFFFF;
  color: #374151;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 14px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
  font-family: inherit;
}

.back-btn:hover {
  background: #F3F4F6;
  border-color: #D1D5DB;
}

.start-btn {
  flex: 2;
}

/* ===== 阶段3：图谱构建工作台 ===== */
.workbench-phase {
  height: 100%;
  background: #FAFAFA;
  overflow: hidden;
}

.scroll-container {
  height: 100%;
  overflow-y: auto;
  padding: 16px 24px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.step-card {
  background: #FFFFFF;
  border-radius: 8px;
  padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #EAEAEA;
  transition: border-color 0.2s, box-shadow 0.2s;
  position: relative;
}

.step-card.active {
  border-color: #FF5722;
  box-shadow: 0 4px 12px rgba(255, 87, 34, 0.08);
}

.step-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 20px;
  font-weight: 800;
  color: #D1D5DB;
}

.step-card.active .step-num,
.step-card.completed .step-num {
  color: #111827;
}

.step-title {
  font-weight: 700;
  font-size: 14px;
  color: #111827;
  letter-spacing: 0.2px;
}

.badge {
  font-size: 10px;
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: 700;
  white-space: nowrap;
}

.badge.success {
  background: #E8F5E9;
  color: #2E7D32;
}

.badge.processing,
.badge.accent {
  background: #FF5722;
  color: #FFFFFF;
}

.badge.pending {
  background: #F5F5F5;
  color: #999999;
}

.badge.error {
  background: #FEE2E2;
  color: #B91C1C;
}

.step-card-content {
  position: relative;
}

.api-note {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #999999;
  margin: 0 0 8px;
}

.description {
  font-size: 12px;
  color: #666666;
  line-height: 1.6;
  margin: 0 0 12px;
}

.progress-section {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #FF5722;
  margin-bottom: 14px;
}

.progress-message.inline {
  text-align: left;
  color: #6B7280;
  font-size: 12px;
  margin: -4px 0 14px;
}

.progress-message.error {
  text-align: left;
  color: #B91C1C;
  background: #FEF2F2;
  border: 1px solid #FECACA;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  margin: -4px 0 14px;
  padding: 8px 10px;
}

/* 本体预览 */
.ontology-preview {
  width: 100%;
  background: #FFFFFF;
  border-radius: 8px;
  padding: 0;
  border: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tags-container {
  transition: opacity 0.3s;
  margin-top: 4px;
}

.tags-container.dimmed {
  opacity: 0.3;
  pointer-events: none;
}

.tag-label {
  display: block;
  font-size: 10px;
  color: #AAAAAA;
  margin-bottom: 8px;
  font-weight: 700;
  letter-spacing: 0.4px;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.entity-tag {
  background: #F5F5F5;
  border: 1px solid #EEEEEE;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 10px;
  color: #333333;
  font-family: 'JetBrains Mono', monospace;
  transition: all 0.2s;
}

.entity-tag.clickable {
  cursor: pointer;
}

.entity-tag.clickable:hover {
  background: #E0E0E0;
  border-color: #CCCCCC;
  color: #111827;
}

/* 图谱统计 */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  width: 100%;
  background: #F9F9F9;
  padding: 14px 16px;
  border-radius: 6px;
}

.stat-card {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 800;
  color: #000000;
  font-family: 'JetBrains Mono', monospace;
}

.stat-label {
  font-size: 9px;
  color: #999999;
  text-transform: uppercase;
  margin-top: 4px;
  display: block;
  letter-spacing: 0.5px;
}

.action-btn {
  width: 100%;
  background: #111111;
  color: #FFFFFF;
  border: none;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  transition: opacity 0.2s, background 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-family: inherit;
}

.action-btn:hover:not(:disabled) {
  opacity: 0.82;
}

.action-btn:disabled {
  background: #CCCCCC;
  cursor: not-allowed;
}

/* 本体详情 overlay */
.ontology-detail-overlay {
  position: fixed;
  top: 80px;
  left: 50%;
  transform: translateX(-50%);
  width: 90%;
  max-width: 560px;
  max-height: 70vh;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(8px);
  z-index: 100;
  border: 1px solid #E8ECF0;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.12);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid #E8ECF0;
  background: #F9FAFB;
}

.detail-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-type-badge {
  font-size: 10px;
  font-weight: 700;
  color: #FFFFFF;
  background: #1677FF;
  padding: 2px 8px;
  border-radius: 4px;
  text-transform: uppercase;
}

.detail-name {
  font-size: 15px;
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
  color: #1A1A2E;
}

.close-btn {
  background: none;
  border: none;
  color: #9CA3AF;
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
  padding: 0;
}

.close-btn:hover {
  color: #374151;
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px;
}

.detail-desc {
  font-size: 13px;
  color: #374151;
  line-height: 1.6;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px dashed #E8ECF0;
}

.detail-section {
  margin-bottom: 16px;
}

.section-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: #9CA3AF;
  margin-bottom: 8px;
  letter-spacing: 0.5px;
}

.attr-list,
.conn-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.attr-item {
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  padding: 6px 8px;
  background: #F9FAFB;
  border-radius: 6px;
}

.attr-name {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  color: #1A1A2E;
}

.attr-type {
  color: #9CA3AF;
  font-size: 11px;
}

.attr-desc {
  color: #6B7280;
  flex: 1;
  min-width: 150px;
}

.example-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.example-tag {
  font-size: 12px;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  padding: 3px 10px;
  border-radius: 12px;
  color: #6B7280;
}

.conn-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 6px 8px;
  background: #F3F4F6;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
}

.conn-node {
  font-weight: 600;
  color: #374151;
}

.conn-arrow {
  color: #9CA3AF;
}

/* spinner */
.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #FFFFFF;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  flex-shrink: 0;
}

.spinner-sm.dark {
  border-color: #FFCCBC;
  border-top-color: #FF5722;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 响应式 */
@media (max-width: 1080px) {
  .result-center {
    max-width: 760px;
  }

  .result-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .phase-input {
    padding: 52px 24px 64px;
  }

  .scroll-container {
    padding: 16px;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }

  .result-actions {
    flex-direction: column;
  }

  .result-grid {
    grid-template-columns: 1fr;
  }

  .summary-text {
    max-height: 420px;
  }

  .analysis-log-panel {
    max-height: 300px;
  }

  .hero-title {
    flex-direction: column;
    align-items: center;
    gap: 6px;
    font-size: 1.8rem;
    line-height: 1;
  }

  .title-suffix {
    margin-left: 0;
  }

  .recommendation-ghost {
    flex-direction: column;
    gap: 8px;
    font-size: 14px;
    line-height: 22px;
  }

  .tab-hint {
    font-size: 12px;
  }
}
</style>
