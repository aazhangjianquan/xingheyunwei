// Isolated loopback fixture only. Feishu authorization is simulated at both boundaries.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {chromium} = require('playwright')
const root = path.resolve(__dirname, '..')
const origin = 'http://feishu-task.test'
const api = 'http://127.0.0.1:18091'
const task = process.env.FEISHU_UI_TASK || '/resource/3'
const checks = [], errors = []

async function main() {
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
  async function open({feishu=false,deny=false}={}) {
    const context = await browser.newContext({viewport:{width:1440,height:1080},
      ...(feishu ? {userAgent:'Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Feishu/7.40.0'} : {})})
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    const authRequests = []
    await page.route('**/*', async route => {
      const url = new URL(route.request().url())
      assert.equal(url.origin,origin)
      if (url.pathname.startsWith('/api/')) {
        authRequests.push(url.pathname)
        const response = await route.fetch({url:api+url.pathname+url.search,maxRedirects:0})
        const headers = response.headers()
        if (headers.location) {
          let target = new URL(headers.location,origin)
          if (target.origin === 'https://accounts.feishu.cn') {
            assert.equal(feishu,true)
            const next = new URL(origin+'/api/auth/feishu/callback')
            next.searchParams.set('state',target.searchParams.get('state'))
            next.searchParams.set(deny ? 'error' : 'code',deny ? 'access_denied' : 'mock-official-code')
            target = next
          }
          // 路由拦截器对 HTTP 重定向只处理首跳；模拟单独导航以保持所有流量在隔离夹具中。
          delete headers.location
          delete headers['content-length']
          return route.fulfill({status:200,headers:{...headers,'content-type':'text/html'},
            body:`<!doctype html><script>location.replace(${JSON.stringify(target.href)})</script>`})
        }
        return route.fulfill({response})
      }
      const file = path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
      return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
    })
    return {page,context,authRequests}
  }
  try {
    const ordinary = await open()
    await ordinary.page.goto(origin+task+'?feishu_task=1')
    await ordinary.page.getByRole('heading',{name:'登录工作空间'}).waitFor()
    assert.equal(ordinary.authRequests.includes('/api/auth/feishu/task/start'),false)
    await ordinary.page.screenshot({path:path.join(root,'reports/feishu-task-browser-login.png'),fullPage:true})
    checks.push('ordinary browser with the copied card link requires a password')
    await ordinary.context.close()

    const taskView = await open({feishu:true})
    await taskView.page.goto(origin+task)
    await taskView.page.locator('.app-shell').waitFor()
    await taskView.page.locator('.modal-panel').getByText('隔离浏览器联调',{exact:true}).waitFor()
    assert.equal(taskView.authRequests.includes('/api/auth/ticket'),true)
    assert.equal(new URL(taskView.page.url()).pathname,task)
    assert.equal(new URL(taskView.page.url()).hash,'')
    await taskView.page.screenshot({path:path.join(root,'reports/feishu-task-authenticated.png'),fullPage:true})
    checks.push('old Feishu task URL completes cookie-bound OAuth and opens its detail without password')
    await taskView.context.close()

    const failed = await open({feishu:true,deny:true})
    await failed.page.goto(origin+task+'?feishu_task=1')
    await failed.page.getByRole('alert').filter({hasText:'飞书授权未完成'}).waitFor()
    await failed.page.getByRole('button',{name:'重新通过飞书进入任务'}).waitFor()
    assert.equal(failed.authRequests.filter(p=>p==='/api/auth/feishu/task/start').length,1)
    await failed.page.setViewportSize({width:390,height:844})
    assert.equal(await failed.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    await failed.page.screenshot({path:path.join(root,'reports/feishu-task-retry-mobile.png'),fullPage:true})
    checks.push('denied authorization keeps task, shows password fallback and retry with no loop or mobile overflow')
    await failed.context.close()

    const settings = await open()
    await settings.page.goto(origin+'/settings')
    await settings.page.getByPlaceholder('请输入用户名').fill('admin')
    await settings.page.getByPlaceholder('请输入密码').fill('Demo@2026')
    await settings.page.getByRole('button',{name:'登录平台'}).click()
    await settings.page.getByRole('checkbox',{name:'启用飞书任务免密'}).waitFor()
    assert.equal(await settings.page.getByRole('checkbox',{name:'启用飞书任务免密'}).isChecked(),true)
    await settings.page.getByText(origin+'/api/auth/feishu/callback',{exact:true}).waitFor()
    await settings.page.screenshot({path:path.join(root,'reports/feishu-task-settings.png'),fullPage:true})
    await settings.page.setViewportSize({width:390,height:844})
    assert.equal(await settings.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    checks.push('admin can see saved task switch and exact callback URL; settings fit desktop and mobile')
    await settings.context.close()

    assert.deepEqual(errors,[])
    const report = {result:'passed',scope:'isolated SQLite, real Chrome, simulated Feishu OAuth; no production writes or messages',checks,errors}
    fs.writeFileSync(path.join(root,'reports/feishu-task-ui.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
