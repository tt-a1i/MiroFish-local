// 基于 localStorage 的会话历史记录 store
const STORAGE_KEY = 'mirofish_sessions'
const MAX_SESSIONS = 50

// 安全写入 localStorage，隐私模式或配额满时降级为静默失败
function safeSetItem(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // 降级：隐私模式、存储配额满等情况下静默忽略
  }
}

export function getSessions() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    // 校验结构，过滤无效项
    if (!Array.isArray(raw)) return []
    return raw.filter(s => s && typeof s.projectId === 'string' && s.projectId)
  } catch {
    return []
  }
}

export function addSession(session) {
  // session: { id, title, projectId, createdAt }
  if (!session || typeof session.projectId !== 'string' || !session.projectId) return

  const sessions = getSessions()
  const existing = sessions.findIndex(s => s.projectId === session.projectId)
  const normalized = {
    id: session.id || session.projectId,
    title: typeof session.title === 'string' ? session.title : '新会话',
    projectId: session.projectId,
    createdAt: typeof session.createdAt === 'number' ? session.createdAt : Date.now(),
    updatedAt: Date.now()
  }

  if (existing >= 0) {
    // 更新并移到顶部（最近使用优先）
    sessions.splice(existing, 1)
    sessions.unshift(normalized)
  } else {
    sessions.unshift(normalized)
  }

  if (sessions.length > MAX_SESSIONS) sessions.splice(MAX_SESSIONS)
  safeSetItem(STORAGE_KEY, JSON.stringify(sessions))
}

export function removeSession(projectId) {
  const sessions = getSessions().filter(s => s.projectId !== projectId)
  safeSetItem(STORAGE_KEY, JSON.stringify(sessions))
}

export function clearSessions() {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 静默忽略
  }
}
