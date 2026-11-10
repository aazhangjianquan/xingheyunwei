// Public presentation settings only; authentication is always verified by /me.
export function readBootstrapSettings(doc = document) {
  try {
    const node = doc.getElementById('yunwei-public-settings')
    const value = node ? JSON.parse(node.textContent) : null
    if (!value || typeof value.site_name !== 'string' || !value.site_name.trim()) return null
    return {
      site_name: value.site_name,
      feishu_login_enabled: value.feishu_login_enabled === true,
      feishu_task_login_enabled: value.feishu_task_login_enabled === true,
      local_login_enabled: value.local_login_enabled !== false,
    }
  } catch { return null }
}
