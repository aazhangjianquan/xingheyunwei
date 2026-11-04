// Run after building frontend/dist. All requests use local fixtures; no platform data is changed.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'frontend/dist')
const reports = path.join(root, 'reports')
const origin = 'http://workflow-http.test'
const user = { id: 1, username: 'http-check', display_name: 'HTTP 验证', department: '验证部门', roles: ['admin'], active: true }
const settings = { site_name: '项目协作平台', public_base_url: origin, local_login_enabled: true, feishu_login_enabled: false }
const drafts = new Map()
const failures = []
const kinds = ['resource', 'change', 'delivery', 'license']

function flow(kind) {
  return { kind, revision: 1, versions: [], fields: kind === 'license' ? ['product', 'customer', 'department'] : ['environment', 'department'],
    draft: { steps: [{ id: 'final', name: kind === 'license' ? '最终审批' : '执行回填', type: kind === 'license' ? 'license_approval' : 'execution', mode: 'all', assignee: { source: 'fixed', user_ids: [2] } }] } }
}
function idsIn(steps) {
  return steps.flatMap(node => [node.id, ...(node.branches || []).flatMap(arm => [arm.id, ...idsIn(arm.steps)])])
}

;(async () => {
  fs.mkdirSync(reports, { recursive: true })
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) })
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    page.on('pageerror', error => failures.push(error.message))
    await page.addInitScript(() => localStorage.setItem('yunwei_token', 'fixture-only'))
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname
      assert.equal(url.origin, origin, 'Verification must not contact a real platform')
      let data
      if (pathname === '/api/me') data = user
      else if (['/api/auth/options', '/api/admin/settings'].includes(pathname)) data = settings
      else if (pathname === '/api/roles') data = [{ id: 'project_owner', name: '项目负责人', project_role: true }, { id: 'operator', name: '运维人员', project_role: true }]
      else if (pathname === '/api/users') data = [{ ...user, id: 2, display_name: '验证审批人', roles: ['operator'] }]
      else if (pathname === '/api/projects') data = [{ id: 1, name: '验证项目', active: true }]
      else if (/^\/api\/admin\/flows\/\w+$/.test(pathname)) data = flow(pathname.split('/').at(-1))
      else if (/^\/api\/admin\/flows\/\w+\/draft$/.test(pathname) && request.method() === 'PUT') {
        const body = request.postDataJSON()
        drafts.set(pathname.split('/')[4], body.definition)
        data = { revision: body.revision + 1 }
      } else if (['/api/changes', '/api/deliveries'].includes(pathname)) data = { items: [], counts: { all: 0 }, total: 0, total_pages: 1, page: 1 }
      else if (pathname.startsWith('/api/')) {
        failures.push(`Unexpected API request: ${request.method()} ${pathname}`)
        return route.fulfill({ status: 404, json: { detail: 'Unexpected verification request' } })
      } else {
        const relative = pathname.startsWith('/assets/') ? pathname.slice(1) : 'index.html'
        const contentType = relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : relative.endsWith('.svg') ? 'image/svg+xml' : 'text/html'
        return route.fulfill({ contentType, body: fs.readFileSync(path.join(dist, relative)) })
      }
      return route.fulfill({ json: data })
    })
    await page.goto(origin + '/settings')
    const environment = await page.evaluate(() => ({ isSecureContext, randomUUID: typeof crypto.randomUUID, getRandomValues: typeof crypto.getRandomValues }))
    assert.equal(environment.isSecureContext, false)
    assert.equal(environment.randomUUID, 'undefined')
    await page.getByRole('button', { name: '审批流程', exact: true }).click()
    for (const kind of kinds) {
      await page.locator('.flow-toolbar > select').first().selectOption(kind)
      const steps = page.locator('.workflow-editor > .flow-steps')
      await page.waitForFunction(() => document.querySelectorAll('.workflow-editor > .flow-steps > .flow-node').length === 1)
      await steps.locator(':scope > .flow-add > button').nth(0).click()
      await steps.locator(':scope > .flow-add > button').nth(1).click()
      assert.equal(await steps.locator(':scope > .flow-node').count(), 3)
      const branch = steps.locator(':scope > .flow-node.branch')
      await branch.getByRole('button', { name: '＋ 添加条件分支', exact: true }).click()
      assert.equal(await branch.locator(':scope > .flow-arm').count(), 3)
      const arm = branch.locator(':scope > .flow-arm').first()
      await arm.locator('.flow-add > button').nth(0).click()
      assert.equal(await arm.locator('.flow-node').count(), 1)
      await page.getByRole('button', { name: '保存草稿', exact: true }).click()
      await page.waitForFunction(() => [...document.querySelectorAll('.flow-toolbar .muted')].some(node => node.textContent.includes('草稿修订 2')))
      const saved = drafts.get(kind)
      assert.ok(saved)
      const ids = idsIn(saved.steps)
      assert.equal(new Set(ids).size, ids.length)
      assert.ok(ids.every(id => id.length <= 60))
      assert.equal(saved.steps.at(-1).type, kind === 'license' ? 'license_approval' : 'execution')
    }
    await page.screenshot({ path: path.join(reports, 'workflow-http-fixed.png'), fullPage: true })
    for (const section of ['变更管理', '私有化交付']) {
      await page.locator('.nav-list').getByRole('button', { name: section, exact: true }).click()
      await page.getByRole('button', { name: section === '变更管理' ? '新建变更单' : '创建交付项目', exact: true }).click()
      await page.locator('input[type=file]').setInputFiles({ name: 'HTTP附件验证.txt', mimeType: 'text/plain', buffer: Buffer.from('HTTP attachment compatibility') })
      assert.equal(await page.locator('.attachment-queue-row').count(), 1)
      await page.getByRole('button', { name: '移除', exact: true }).click()
      assert.equal(await page.locator('.attachment-queue-row').count(), 0)
      await page.locator('.modal-head > button.icon-button').click()
    }
    assert.deepEqual(failures, [])
    const result = { environment, kinds, operations: ['add approval', 'add branch', 'add branch arm', 'add nested approval', 'save draft', 'select/remove change and delivery attachments'], pageErrors: failures }
    fs.writeFileSync(path.join(reports, 'workflow-http-verification.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result, null, 2))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
