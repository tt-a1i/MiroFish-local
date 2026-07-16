import axios from 'axios'

function normalizeBaseURL(baseURL) {
  const trimmed = (baseURL || '').replace(/\/+$/, '')

  // 前端各 API 模块已经带 /api 前缀，本地 Vite 代理和 Nginx 反代都直接吃 /api 路径。
  // 如果 baseURL 也配置成 /api，会变成 /api/api/...，这里直接归一化为空。
  if (trimmed === '/api') {
    return ''
  }

  return trimmed
}

// 创建axios实例
const service = axios.create({
  baseURL: normalizeBaseURL(import.meta.env.VITE_API_BASE_URL),
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  config => {
    // 避免 baseURL 已经包含 /api 时，再和业务层的 /api/... 路径重复拼接。
    if (
      typeof config.baseURL === 'string' &&
      config.baseURL.endsWith('/api') &&
      typeof config.url === 'string' &&
      config.url.startsWith('/api/')
    ) {
      config.url = config.url.slice(4)
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      const apiError = new Error(res.error || res.message || 'Error')
      apiError.response = response
      apiError.status = response.status
      return Promise.reject(apiError)
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)
    if (error?.response?.status) {
      error.status = error.response.status
    }
    
    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }
    
    return Promise.reject(error)
  }
)

// 带重试的请求函数
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
