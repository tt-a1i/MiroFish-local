import service, { requestWithRetry } from './index'
import { setGraphTypeTranslations } from '../utils/entityTranslations.js'
import { streamNdjson } from './stream'

/**
 * 联网搜索现实事件
 * @param {Object} data - 包含 search_query, project_name, additional_context
 * @returns {Promise}
 */
export function searchSeedByKeyword(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/seed/web-search',
      method: 'post',
      data
    })
  )
}

/**
 * 流式联网搜索现实事件
 * @param {Object} data - 包含 search_query, project_name, additional_context
 * @param {Object} handlers - { onEvent }
 * @returns {Promise<Object>}
 */
export function streamSearchSeedByKeyword(data, handlers = {}) {
  return streamNdjson('/api/graph/seed/web-search/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(data)
  }, handlers)
}

/**
 * 上传文件并分析现实事件（multipart 阶段）
 * @param {FormData} formData - 包含 files, project_name, additional_context
 * @returns {Promise}
 */
export function analyzeUploadedSeed(formData) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

/**
 * 流式上传文件并分析现实事件
 * @param {FormData} formData - 包含 files, project_name, additional_context
 * @param {Object} handlers - { onEvent }
 * @returns {Promise<Object>}
 */
export function streamAnalyzeUploadedSeed(formData, handlers = {}) {
  return streamNdjson('/api/graph/seed/upload/stream', {
    method: 'POST',
    body: formData
  }, handlers)
}

/**
 * 基于已确认的项目和模拟需求生成本体。
 * 兼容旧调用：如果传入 FormData，则退回 multipart 文件分析函数。
 * @param {Object|FormData} data - JSON 阶段包含 project_id, simulation_requirement
 * @returns {Promise}
 */
export function generateOntology(data) {
  if (typeof FormData !== 'undefined' && data instanceof FormData) {
    return analyzeUploadedSeed(data)
  }

  return requestWithRetry(() =>
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data
    })
  )
}

/**
 * 构建图谱
 * @param {Object} data - 包含project_id, graph_name等
 * @returns {Promise}
 */
export function buildGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * 查询任务状态
 * @param {String} taskId - 任务ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 获取图谱数据
 * @param {String} graphId - 图谱ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  }).then(res => {
    if (res.success && res.data?.type_translations) {
      setGraphTypeTranslations(res.data.type_translations)
    }
    return res
  })
}

/**
 * 获取图谱实体/关系类型翻译表
 * @returns {Promise}
 */
export function getGraphTypeTranslations() {
  return service({
    url: '/api/graph/type-translations',
    method: 'get'
  })
}

/**
 * 获取项目信息
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}

/**
 * 列出所有项目（跨浏览器共享）
 * @param {Number} limit - 返回数量限制，默认50
 * @returns {Promise}
 */
export function listProjects(limit = 50) {
  return service({
    url: '/api/graph/project/list',
    method: 'get',
    params: { limit }
  })
}

/**
 * 删除项目会话
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function deleteProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'delete'
  })
}
