// 实体类型/关系类型翻译工具
// 默认表用于接口加载失败时兜底；运行时会被后端持久化翻译表覆盖和扩展。

const fallbackEntityTypeMap = {
  PERSON: '人',
  ORGANIZATION: '组织',
  LOCATION: '地点',
  EVENT: '事件',
  CONCEPT: '概念',
  OBJECT: '对象',
  PRODUCT: '产品',
  WORK: '作品',
  DATE: '日期',
  TIME: '时间',
  ENTITY: '实体',
  PLACE: '地方',
  GROUP: '群体',
  PROJECT: '项目',
  DOCUMENT: '文档',
  TOPIC: '主题',
  ROLE: '角色',
  OCCUPATION: '职业',
  AWARD: '奖项',
  LAW: '法律',
  MONEY: '货币',
  NUMBER: '数字',
  FACILITY: '设施',
  CREATIVEWORK: '创意作品',
  INDUSTRYASSOCIATION: '行业协会',
  MEDIAOUTLET: '媒体机构',
  GOVERNMENTAGENCY: '政府机构',
  LAWENFORCEMENTOFFICER: '执法人员',
  LAWENFORCEMENT: '执法机构',
  NEWSMEDIA: '新闻媒体',
  GOVERNMENTOFFICIAL: '政府官员',
  INDUSTRYEXPERT: '行业专家',
  PUBLICFIGURE: '公众人物',
  INTERNETINFLUENCER: '网络红人',
  CITIZENORGANIZATION: '公民组织',
  THINKTANK: '智库机构',
  JOURNALIST: '记者',
  POLITICIAN: '政治家',
  ACTIVIST: '活动人士',
  INDUSTRYLEADER: '行业领袖',
  GOVERNMENT: '政府',
  MEDIA: '媒体',
  INDUSTRY: '行业',
  COMPANY: '公司',
  NGO: '非政府组织',
  ACADEMIC: '学术机构',
  UNIVERSITY: '大学',
  SCHOOL: '学校',
  HOSPITAL: '医院',
  COURT: '法院',
  POLITICALPARTY: '政党',
  SOCIALMEDIAPLATFORM: '社交媒体平台',
  ONLINECOMMUNITY: '在线社区',
  FUTURESIMULATIONMEMORY: '未来推演记忆'
}

const fallbackRelationTypeMap = {
  RELATED: '关联于',
  RELATED_TO: '关联于',
  HAS_ROLE: '担任角色',
  LOCATED_IN: '位于',
  WORKS_AT: '工作于',
  WORKS_FOR: '为...工作',
  PART_OF: '属于',
  INVOLVED_IN: '参与',
  PARTICIPATED_IN: '参与了',
  CREATED_BY: '由...创建',
  OWNS: '拥有',
  KNOWS: '认识',
  LIVES_IN: '居住于',
  BORN_IN: '出生于',
  HAS_EMPLOYEE: '雇佣',
  SUPERVISES: '监督',
  MANAGES: '管理',
  REPORTS_TO: '汇报给',
  FOUNDED: '创立',
  LOCATED_AT: '位于',
  OCCURRED_ON: '发生于',
  CAUSED: '导致',
  AFFECTS: '影响',
  IS_A: '是一种',
  HAS_PART: '包含',
  ATTENDED: '参加',
  SPOKE_AT: '发言于',
  WROTE: '撰写',
  PUBLISHED: '发布',
  OWNS_SHARES: '持股',
  SUBSIDIARY_OF: '子公司',
  MEMBER_OF: '成员',
  HEADQUARTERED_IN: '总部位于',
  MARRIED_TO: '结婚',
  PARENT_OF: '子女',
  CHILD_OF: '父母',
  SIBLING_OF: '兄弟姐妹',
  EDUCATED_AT: '毕业于',
  GRADUATED_FROM: '毕业于',
  STUDIED_AT: '就读于',
  STUDIES_AT: '就读于',
  COLLABORATED_WITH: '合作',
  COLLABORATES_WITH: '合作',
  COMPETES_WITH: '竞争',
  REGULATES: '监管',
  FUNDED_BY: '由...资助',
  SPONSORED_BY: '由...赞助',
  REPRESENTS: '代表',
  NEGOTIATED_WITH: '协商',
  INVESTIGATED: '调查',
  ANNOUNCED: '宣布',
  CRITICIZED: '批评',
  SUPPORTED: '支持',
  SUPPORTS: '支持',
  OPPOSED: '反对',
  OPPOSES: '反对',
  INFLUENCED: '影响',
  RESPONDED_TO: '回应',
  RESPONDS_TO: '回应',
  COMMENTED_ON: '评论',
  COMMENTS_ON: '评论',
  REPORTS_ON: '报道'
}

let entityTypeMap = { ...fallbackEntityTypeMap }
let relationTypeMap = { ...fallbackRelationTypeMap }
let attributeKeyMap = {}
let translationVersion = 0

const normalizeEntityKey = (str) => {
  if (!str) return ''
  return String(str).toUpperCase().replace(/[\s_-]/g, '')
}

const normalizeRelationKey = (str) => {
  if (!str) return ''
  return String(str).trim().toUpperCase().replace(/[\s-]/g, '_')
}

const normalizeIncomingMap = (map, kind = 'entity') => {
  const normalized = {}
  Object.entries(map || {}).forEach(([key, value]) => {
    if (!key || !value) return
    const normalizedKey = kind === 'relation' ? normalizeRelationKey(key) : normalizeEntityKey(key)
    normalized[normalizedKey] = value
  })
  return normalized
}

export const setGraphTypeTranslations = (payload = {}) => {
  entityTypeMap = {
    ...fallbackEntityTypeMap,
    ...normalizeIncomingMap(payload.entity_types, 'entity')
  }
  relationTypeMap = {
    ...fallbackRelationTypeMap,
    ...normalizeIncomingMap(payload.relation_types, 'relation')
  }
  attributeKeyMap = normalizeIncomingMap(payload.attribute_keys, 'entity')
  translationVersion = payload.version || Date.now()
}

export const getGraphTypeTranslationVersion = () => translationVersion

export const translateEntityType = (type) => {
  if (!type) return '未知'
  const key = normalizeEntityKey(type)
  return entityTypeMap[key] || String(type)
}

export const translateRelationType = (type) => {
  if (!type) return '关联'
  const key = normalizeRelationKey(type)
  const keyNoUnderscore = normalizeEntityKey(type)
  return relationTypeMap[key] || relationTypeMap[keyNoUnderscore] || String(type)
}

export const translateAttributeKey = (key) => {
  if (!key) return '属性'
  const normalizedKey = normalizeEntityKey(key)
  return attributeKeyMap[normalizedKey] || ''
}

export const translateType = (type, kind = 'entity') => {
  if (kind === 'relation') return translateRelationType(type)
  return translateEntityType(type)
}
