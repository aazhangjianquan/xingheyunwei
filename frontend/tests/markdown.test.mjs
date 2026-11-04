import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { renderMarkdown } from '../src/markdown.js'

test('manual headings, emphasis, lists and tables render as document elements', () => {
  const html = renderMarkdown('### 修改姓名和密码\n\n**数量口径。** 申请数量与待办不同。\n\n- 先登录\n- 再修改\n\n1. 打开页面\n2. 提交内容\n\n| 页面表现 | 应对方法 |\n| --- | --- |\n| 账号已停用 | 联系管理员 |')
  assert.match(html, /<h3>修改姓名和密码<\/h3>/)
  assert.match(html, /<strong>数量口径。<\/strong>/)
  assert.match(html, /<ul>\s*<li>先登录<\/li>/)
  assert.match(html, /<ol>\s*<li>打开页面<\/li>/)
  assert.match(html, /class="markdown-table-scroll"[^>]+tabindex="0"><table>/)
  assert.match(html, /<th>页面表现<\/th>/)
  assert.match(html, /<td>联系管理员<\/td>/)
})

test('the actual usage manual renders its tables and hides Word pagination markers', () => {
  const manual = readFileSync(new URL('../../docs/星河项目协作平台使用手册.md', import.meta.url), 'utf8')
  const html = renderMarkdown(manual)
  assert.match(html, /<h1>星河项目协作平台使用手册<\/h1>/)
  assert.equal((html.match(/<table>/g) || []).length, 13)
  assert.doesNotMatch(html, /pagebreak/)
})

test('fenced code and inline code keep whitespace and escape markup', () => {
  const html = renderMarkdown('> 注意保存\n\n`<tag>`\n\n```html\n<script>alert(1)</script>\n  保留缩进\n```')
  assert.match(html, /<blockquote>/)
  assert.match(html, /<code>&lt;tag&gt;<\/code>/)
  assert.match(html, /<pre><code class="language-html">&lt;script&gt;alert\(1\)&lt;\/script&gt;\n  保留缩进/)
  assert.doesNotMatch(html, /<script>/)
})

test('raw scripts, event handlers, SVG and frames cannot become active elements', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n<svg onload="alert(1)"></svg>\n\n<iframe src="https://example.test"></iframe>')
  assert.doesNotMatch(html, /<(?:script|img|svg|iframe)\b/i)
  assert.match(html, /&lt;script&gt;/)
})

test('unsafe and encoded URL schemes cannot create clickable links or images', () => {
  for (const target of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'javascript&#58;alert(1)', 'vbscript:msgbox(1)', 'file:///etc/passwd', 'data:text/html;base64,PHNjcmlwdD4=', 'data:image/svg+xml;base64,PHN2Zz4=']) {
    const html = renderMarkdown(`[链接](${target})\n\n![图片](${target})`)
    assert.doesNotMatch(html, /<(?:a|img)\b/, target)
  }
})

test('normal links open without opener access and attribute injection stays escaped', () => {
  const html = renderMarkdown('[平台](https://example.test/path "正常标题")\n\n![说明](https://example.test/image.png "x&quot; onerror=&quot;alert(1)")')
  assert.match(html, /target="_blank" rel="noopener noreferrer"/)
  assert.match(html, /loading="lazy" referrerpolicy="no-referrer"/)
  assert.match(html, /title="x&quot; onerror=&quot;alert\(1\)"/)
})

test('plain text line breaks and non-string empty values remain readable', () => {
  assert.equal(renderMarkdown(null), '')
  assert.equal(renderMarkdown('第一行\n第二行'), '<p>第一行<br>\n第二行</p>\n')
})
