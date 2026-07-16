function normalizeBaseURL(baseURL) {
  const trimmed = (baseURL || '').replace(/\/+$/, '')
  return trimmed === '/api' ? '' : trimmed
}

export function getApiUrl(path) {
  const baseURL = normalizeBaseURL(import.meta.env.VITE_API_BASE_URL)
  if (!baseURL) return path
  if (baseURL.endsWith('/api') && path.startsWith('/api/')) {
    return `${baseURL}${path.slice(4)}`
  }
  return `${baseURL}${path}`
}

export async function streamNdjson(path, options = {}, handlers = {}) {
  const response = await fetch(getApiUrl(path), options)
  if (!response.ok) {
    let detail = ''
    try {
      detail = await response.text()
    } catch (err) {
      detail = ''
    }
    throw new Error(detail || `请求失败：HTTP ${response.status}`)
  }

  if (!response.body) {
    const payload = await response.json()
    if (payload?.success === false) throw new Error(payload.error || '请求失败')
    handlers.onEvent?.({ event: 'complete', data: payload?.data, message: payload?.message || '处理完成' })
    return payload?.data
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalData = null

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const cleanLine = line.trim()
      if (!cleanLine) continue
      const event = JSON.parse(cleanLine)
      handlers.onEvent?.(event)
      if (event.event === 'error') throw new Error(event.message || event.error || '处理失败')
      if (event.event === 'complete' || event.event === 'done') finalData = event.data || event
    }
  }

  if (buffer.trim()) {
    const event = JSON.parse(buffer.trim())
    handlers.onEvent?.(event)
    if (event.event === 'error') throw new Error(event.message || event.error || '处理失败')
    if (event.event === 'complete' || event.event === 'done') finalData = event.data || event
  }

  return finalData
}
