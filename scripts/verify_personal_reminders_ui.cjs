// Run only against the isolated personal-reminder fixture server, never production.
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path')
const { chromium, request } = require('playwright')
const root = path.resolve(__dirname, '..'), origin = 'http://personal-reminders.test'
const apiOrigin = process.env.PERSONAL_REMINDER_TEST_API || 'http://127.0.0.1:18075'
const password = 'PersonalReminder123!', errors = []
async function main() {
  const api = await request.newContext({baseURL:apiOrigin})
  const login = async name => {
    const response = await api.post('/api/login', {data:{username:name,password}})
    assert.equal(response.status(),200,await response.text());return (await response.json()).token
  }
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
  const tokens = {}
  async function open(name, url='/personal-reminders') {
    const token = tokens[name] ||= await login(name)
    const page = await browser.newPage({viewport:{width:1440,height:1080},timezoneId:'America/New_York'})
    page.on('pageerror', error=>errors.push(error.message))
    await page.addInitScript(value=>localStorage.setItem('yunwei_token',value),token)
    await page.route('**/*', async route=>{
      const url = new URL(route.request().url())
      assert.equal(url.origin,origin,'UI may only access the dedicated fixture')
      if(url.pathname.startsWith('/api/'))return route.fulfill({response:await route.fetch({url:apiOrigin+url.pathname+url.search})})
      const file=path.join(root,'frontend/dist',url.pathname.startsWith('/assets/')?url.pathname.slice(1):'index.html')
      return route.fulfill({path:file,contentType:file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml'})
    })
    await page.goto(origin+url);return page
  }
  try {
    const page=await open('pr-owner')
    await page.getByRole('button',{name:'新建提醒',exact:true}).click()
    const dialog=page.getByRole('dialog')
    assert.equal(await dialog.getByText('未配置飞书的成员',{exact:true}).count(),0)
    assert.equal(await dialog.getByRole('checkbox',{name:/提醒创建者/}).isChecked(),true)
    await dialog.getByLabel('提醒内容 *').fill('客户升级前核对资料\n请检查附件并安排时间。')
    await dialog.getByLabel('提醒时间（北京时间） *').fill('2099-01-02T09:30')
    await dialog.getByRole('checkbox',{name:/被提醒人/}).check()
    const created=page.waitForResponse(response=>response.url().endsWith('/api/personal-reminders')&&response.request().method()==='POST')
    await dialog.getByRole('button',{name:'保存草稿',exact:true}).click()
    const result=await (await created).json(), id=result.id
    assert.equal(result.remind_at,'2099-01-02T01:30:00+00:00')
    await dialog.getByLabel('选择提醒附件').setInputFiles({name:'检查清单.txt',mimeType:'text/plain',buffer:Buffer.from('升级前核对：\n1. 备份\n2. 资料确认')})
    await dialog.locator('.attachment-row').getByText('检查清单.txt',{exact:true}).waitFor()
    await dialog.getByRole('button',{name:'启用定时提醒',exact:true}).click()
    await dialog.getByRole('heading',{name:'待提醒 · 个人提醒'}).waitFor()
    await page.screenshot({path:path.join(root,'reports/personal-reminder-desktop.png'),fullPage:true})
    await page.setViewportSize({width:390,height:844})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    assert.equal(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),true)
    await page.screenshot({path:path.join(root,'reports/personal-reminder-mobile.png'),fullPage:true})
    await dialog.getByRole('button',{name:'关闭个人提醒'}).click()
    const trigger=await api.post('/_test/trigger/'+id);assert.equal(trigger.status(),200)
    assert.equal((await trigger.json()).notifications,2)
    const recipient=await open('pr-recipient',`/personal-reminders/${id}/files`)
    await recipient.locator('.attachment-row').getByText('检查清单.txt',{exact:true}).waitFor()
    assert.equal(await recipient.getByText('客户升级前核对资料',{exact:true}).count(),0)
    await recipient.getByRole('button',{name:'预览',exact:true}).click()
    await recipient.getByRole('dialog').locator('pre').getByText(/升级前核对/).waitFor()
    await recipient.getByRole('button',{name:'关闭附件预览'}).click()
    const downloadEvent=recipient.waitForEvent('download')
    await recipient.getByRole('button',{name:'下载',exact:true}).click()
    const download=await downloadEvent;assert.equal(download.suggestedFilename(),'检查清单.txt')
    assert.match(fs.readFileSync(await download.path(),'utf8'),/资料确认/)
    const admin=await open('pr-admin',`/personal-reminders/${id}/files`)
    await admin.getByRole('alert').getByText(/无权查看/).waitFor()
    await recipient.getByRole('button',{name:'个人提醒',exact:true}).click()
    await recipient.getByText('还没有个人提醒',{exact:true}).waitFor()
    assert.deepEqual(errors,[])
    const report={checks:8,errors,reminder_id:id,timezone:'America/New_York',beijing_time:'2099-01-02 09:30',result:'passed'}
    fs.writeFileSync(path.join(root,'reports/personal-reminder-ui.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report))
  } finally {await browser.close();await api.dispose()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
