const OPTION_PREFIX_PATTERN = /^\s*(?:[-*]\s*)?(?:第\s*)?(?:\d+|[一二三四五六七八九十]+)\s*(?:[.．](?!\d)|[、)\）:：])\s*/

export const stripOptionPrefix = (text = '') => {
  if (!text) return ''

  return String(text)
    .replace(/^#+\s*/, '')
    .replace(OPTION_PREFIX_PATTERN, '')
    .trim()
}

export const formatSimulationRequirement = (text = '') => {
  return stripOptionPrefix(String(text || '').replace(/可模拟/g, '推演'))
}

const compactTitleText = (text = '') => {
  return stripOptionPrefix(text)
    .replace(/\s+/g, ' ')
    .trim()
}

export const getProjectDisplayTitle = (project = {}) => {
  const eventTitle = compactTitleText(project?.search_query || project?.event_topic || project?.name || '')
  const simulationRequirement = compactTitleText(formatSimulationRequirement(project?.simulation_requirement || ''))

  if (eventTitle && simulationRequirement) {
    if (eventTitle === simulationRequirement || simulationRequirement.startsWith(`${eventTitle}：`) || simulationRequirement.startsWith(`${eventTitle}:`)) {
      return simulationRequirement
    }
    return `${eventTitle}：${simulationRequirement}`
  }

  if (simulationRequirement) return simulationRequirement
  if (eventTitle) return eventTitle

  const summary = project?.seed_summary_md || project?.analysis_summary || ''
  const firstLine = String(summary)
    .split('\n')
    .map(line => compactTitleText(line))
    .find(Boolean)

  return firstLine || '事件概述'
}
