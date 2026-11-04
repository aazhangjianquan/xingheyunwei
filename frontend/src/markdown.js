import MarkdownIt from 'markdown-it'

// Only the parser produces HTML. User HTML stays escaped, including scripts,
// event handlers, iframes and SVG; markdown-it also rejects unsafe link schemes.
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderLink = markdown.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options))
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderLink(tokens, index, options, env, renderer)
}
const renderImage = markdown.renderer.rules.image
markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('loading', 'lazy')
  tokens[index].attrSet('referrerpolicy', 'no-referrer')
  return renderImage(tokens, index, options, env, renderer)
}
markdown.renderer.rules.table_open = () => '<div class="markdown-table-scroll" role="region" aria-label="文档表格，可横向滚动" tabindex="0"><table>\n'
markdown.renderer.rules.table_close = () => '</table></div>\n'

export function renderMarkdown(content) {
  // The manual uses this non-content marker only for Word pagination.
  return markdown.render(String(content ?? '').replace(/^[\t ]*<!-- pagebreak -->[\t ]*$/gm, ''))
}
