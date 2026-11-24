// HTML 消毒工具 — 所有 dangerouslySetInnerHTML 的内容必须先经过这里
// 使用 isomorphic-dompurify：浏览器端用 dompurify，SSR/构建期用 jsdom

import DOMPurify from 'isomorphic-dompurify'

/**
 * 消毒普通 HTML / Markdown 渲染结果。
 * 允许 marked 生成的标签与属性，剥离 script、事件处理器、javascript: 链接等。
 */
export function sanitizeHtml(dirty: string): string {
  if (!dirty) return ''
  return DOMPurify.sanitize(dirty, {
    // marked 会生成 class / id / target；代码块高亮可能带 style
    ADD_ATTR: ['target', 'rel'],
    // 禁止 <style>/<iframe>/<object>/<form> 等可执行或可注入内容
    FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form', 'input', 'button'],
    FORBID_ATTR: ['srcset', 'formaction'],
  })
}

/**
 * 消毒 mermaid 渲染出的 SVG。
 * SVG 需要保留 <style>、style 属性与 id/class，否则图形无法正确显示；
 * 但 script、foreignObject、事件属性仍会被剥离。
 */
export function sanitizeSvg(svg: string): string {
  if (!svg) return ''
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ['style'],
    ADD_ATTR: ['style', 'marker-end', 'marker-start'],
    FORBID_TAGS: ['script', 'foreignObject'],
  })
}