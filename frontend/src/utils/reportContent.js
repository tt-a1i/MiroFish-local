const TOOL_BLOCK_PATTERNS = [
  /<\|tool_calls_section_begin\|>[\s\S]*?<\|tool_calls_section_end\|>/gi,
  /<\|tool_call_begin\|>[\s\S]*?<\|tool_call_end\|>/gi,
  /<tool_call>[\s\S]*?<\/tool_call>/gi,
  /<toolcallsection\/begin\/>[\s\S]*?<toolcallsection\/end\/>/gi,
  /<toolcallbegin\/>[\s\S]*?<toolcallend\/>/gi,
  /\[TOOL_CALL\][^\n]*(?:\n|$)/gi
]

export const sanitizeReportContent = (content = '') => {
  if (!content) return ''

  let cleaned = String(content)
  TOOL_BLOCK_PATTERNS.forEach((pattern) => {
    cleaned = cleaned.replace(pattern, '')
  })

  cleaned = cleaned
    .replace(/<\/?think>/gi, '')
    .replace(/<\|[^|>]*tool[^|>]*\|>/gi, '')
    .replace(/<\/?toolcall[^>]*>/gi, '')
    .replace(/^\s*(?:Final Answer|最终答案|最终回答)\s*[:：]\s*/gim, '')
    .replace(/^\s*(?:我需要|让我|现在|首先|接下来|我将)[^\n。！？]*?(?:调用|使用)[^\n。！？]*?(?:工具|检索|搜索)[^\n]*$/gim, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  return cleaned
}
