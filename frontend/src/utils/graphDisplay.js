import { translateAttributeKey, translateEntityType, translateRelationType } from './entityTranslations.js'

const hiddenAttributeKeys = new Set([
  'name_embedding',
  'embedding',
  'embeddings'
])

const propertyLabelMap = {
  uuid: '唯一标识',
  name: '名称',
  labels: '标签',
  label: '标签',
  lable: '标签',
  summary: '摘要',
  created_at: '创建时间',
  updated_at: '更新时间',
  group_id: '图谱分组',
  entity_type: '实体类型',
  fact_type: '关系类型',
  source_node_uuid: '源节点标识',
  target_node_uuid: '目标节点标识',
  source_node_name: '源节点',
  target_node_name: '目标节点',
  valid_at: '有效起始',
  invalid_at: '失效时间',
  expired_at: '过期时间',
  fact: '事实',
  episodes: '事件',
  description: '描述',
  content: '内容',
  title: '标题',
  type: '类型',
  status: '状态',
  source: '来源',
  target: '目标',
  custom_label: '自定义标签',
  source_citations: '来源引用',
  real_identity_summary: '真实身份摘要',
  verified_facts: '已验证事实',
  info_confidence: '信息置信度',
  info_sources: '信息来源',
  skip_reason: '跳过原因',
  raw_query: '原始查询',
  provenance: '来源说明',
  runtime_traits: '运行特征',
  role: '角色',
  persona: '人设',
  bio: '简介',
  orgname: '组织名称',
  orgtype: '组织类型',
  gender: '性别',
  age: '年龄',
  country: '国家/地区',
  profession: '职业',
  mbti: 'MBTI',
  interested_topics: '关注话题',
  confidence: '置信度'
}

const propertyWordMap = {
  custom: '自定义',
  label: '标签',
  lable: '标签',
  labels: '标签',
  source: '来源',
  target: '目标',
  node: '节点',
  edge: '关系',
  relation: '关系',
  type: '类型',
  fact: '事实',
  facts: '事实',
  info: '信息',
  confidence: '置信度',
  verified: '已验证',
  real: '真实',
  identity: '身份',
  summary: '摘要',
  query: '查询',
  raw: '原始',
  reason: '原因',
  skip: '跳过',
  citations: '引用',
  created: '创建',
  updated: '更新',
  valid: '有效',
  invalid: '失效',
  expired: '过期',
  at: '时间',
  id: '标识'
}

const hasCjk = (value) => /[\u4e00-\u9fff]/.test(String(value || ''))

const statusValueMap = {
  active: '有效',
  inactive: '无效',
  pending: '待处理',
  completed: '已完成',
  failed: '失败',
  true: '是',
  false: '否'
}

const timeKeyPattern = /(^|_)(created|updated|valid|invalid|expired)(_at)?$|_at$|time|date/i
const camelCaseTimeKeyPattern = /(?:created|updated|valid|invalid|expired)At$|(?:time|date)$/i
const isoLikeDatePattern = /^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/

export const isGraphTimeKey = (key) => {
  const normalized = String(key || '')
  return timeKeyPattern.test(normalized) || camelCaseTimeKeyPattern.test(normalized)
}

export const formatGraphDateTime = (value, fallback = '') => {
  if (!value) return fallback

  const normalizedValue = typeof value === 'number' && value > 946684800 && value < 1000000000000
    ? value * 1000
    : value
  const date = normalizedValue instanceof Date ? normalizedValue : new Date(normalizedValue)
  if (Number.isNaN(date.getTime())) return fallback || String(value)

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date)
}

const isDateLikeValue = (value) => {
  if (value instanceof Date) return true
  if (typeof value === 'number') return value > 946684800
  if (typeof value !== 'string') return false
  const normalized = value.trim()
  return isoLikeDatePattern.test(normalized)
}

const humanizeKey = (key) => {
  return String(key || '')
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .trim()
}

const translateKeyByWords = (key) => {
  const words = humanizeKey(key).toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return ''

  const translated = words.map(word => propertyWordMap[word] || word)
  return translated.join('')
}

export const shouldShowGraphAttribute = (key, value) => {
  const normalizedKey = String(key || '').trim().toLowerCase()
  if (!normalizedKey || hiddenAttributeKeys.has(normalizedKey)) return false
  if (Array.isArray(value) && hiddenAttributeKeys.has(normalizedKey)) return false
  return true
}

export const translateGraphPropertyKey = (key) => {
  if (!key) return '属性'
  const normalized = String(key).trim()
  const backendTranslation = translateAttributeKey(normalized)
  if (backendTranslation) return backendTranslation
  if (hasCjk(normalized)) return normalized
  const lower = normalized.toLowerCase()
  return propertyLabelMap[lower] || translateKeyByWords(normalized) || humanizeKey(normalized)
}

export const translateGraphLabel = (label) => {
  if (!label) return '未知'
  return translateEntityType(label)
}

export const formatGraphPropertyValue = (key, value) => {
  if (value === null || value === undefined || value === '') return '无'

  const lowerKey = String(key || '').toLowerCase()
  if (isGraphTimeKey(key) && isDateLikeValue(value)) {
    return formatGraphDateTime(value, String(value))
  }

  if (lowerKey === 'entity_type_display_name' || lowerKey === 'display_name') {
    return value
  }

  if (lowerKey === 'labels' || lowerKey === 'label' || lowerKey === 'lable') {
    const labels = Array.isArray(value) ? value : [value]
    return labels.map(translateGraphLabel).join('、')
  }

  if (lowerKey === 'fact_type' || lowerKey === 'relation_type' || lowerKey === 'edge_type') {
    return translateRelationType(value)
  }

  if (lowerKey === 'entity_type' || lowerKey.endsWith('entity_type')) {
    return translateEntityType(value)
  }

  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }

  if (typeof value === 'string') {
    const normalized = value.trim()
    return statusValueMap[normalized.toLowerCase()] || translateRelationType(normalized)
  }

  if (Array.isArray(value)) {
    return value
      .map(item => {
        if (typeof item === 'string') return translateRelationType(item)
        if (item && typeof item === 'object') return JSON.stringify(item)
        return String(item)
      })
      .join('、')
  }

  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2)
  }

  return String(value)
}

export const getDisplayAttributes = (attributes = {}) => {
  return Object.entries(attributes || {})
    .filter(([key, value]) => shouldShowGraphAttribute(key, value))
    .map(([key, value]) => ({
      key,
      label: translateGraphPropertyKey(key),
      value: formatGraphPropertyValue(key, value)
    }))
}
