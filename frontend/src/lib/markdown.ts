import { marked } from 'marked'
import { sanitizeHtml } from './sanitize'

export function markdownToHtml(md: string): string {
  if (!md) return ''

  // 去除首尾空白
  md = md.trim()

  // 使用 marked 统一处理 markdown 和 HTML 混合内容，再统一消毒
  return sanitizeHtml(marked.parse(md, { breaks: true, gfm: true }) as string)
}
