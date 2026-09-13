<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { listen } from '@tauri-apps/api/event'
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { enable as enableAutostart, disable as disableAutostart } from '@tauri-apps/plugin-autostart'
import { check } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'
import MarkdownIt from 'markdown-it'
import { core } from './lib/core'

type Page = 'home' | 'sources' | 'articles' | 'briefings' | 'settings'
const page = ref<Page>('home')
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const bootstrap = ref<any>({ dashboard: {}, sources: [], articles: [], briefings: [], settings: {}, connection: {} })
const article = ref<any>(null)
const addUrl = ref('')
const resolvedSource = ref<any>(null)
const query = ref('')
const unreadOnly = ref(false)
const selectedSource = ref('')
const settingsDraft = ref<any>({})
const aiKey = ref('')
const selectedBriefingDay = ref('')
const sidebarCollapsed = ref(localStorage.getItem('gzhreader.sidebarCollapsed') === '1')
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const currentBriefing = computed(() => bootstrap.value.briefings.find((item: any) => item.day === selectedBriefingDay.value) || bootstrap.value.briefings[0] || null)
const renderedBriefing = computed(() => markdown.render(currentBriefing.value?.markdown || ''))

const nav = [
  { id: 'home', label: '首页', icon: 'home' },
  { id: 'sources', label: '公众号', icon: 'sources' },
  { id: 'articles', label: '全部文章', icon: 'articles' },
  { id: 'briefings', label: '每日简报', icon: 'briefings' },
] as const
const filteredArticles = computed(() => bootstrap.value.articles.filter((a: any) => {
  const matchSource = !selectedSource.value || a.source_id === selectedSource.value
  const matchUnread = !unreadOnly.value || !a.is_read
  const text = `${a.title} ${a.summary} ${a.source_name}`.toLowerCase()
  return matchSource && matchUnread && text.includes(query.value.toLowerCase())
}))
const connectionReady = computed(() => bootstrap.value.connection?.state === 'ready')
const connectionCoolingDown = computed(() => bootstrap.value.connection?.state === 'cooldown')
const connectionTitle = computed(() => connectionCoolingDown.value
  ? '微信读书暂时限制验证'
  : (bootstrap.value.connection?.state === 'verification' ? '需要重新确认访问' : '需要重新连接微信读书'))
watch(() => settingsDraft.value.theme, applyTheme)
watch(sidebarCollapsed, value => localStorage.setItem('gzhreader.sidebarCollapsed', value ? '1' : '0'))

const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())

onMounted(async () => {
  await reload()
  core.on('sync.started', () => { busy.value = true })
  core.on('sync.completed', async (payload) => { busy.value = false; message.value = payload.inserted ? `发现 ${payload.inserted} 篇新文章` : (payload.errors?.[0] || '已是最新内容'); await reload() })
  core.on('articles.new_batch', payload => notify('发现新文章', `${payload.count} 篇新内容已经整理到工作台`))
  core.on('auth.progress', payload => { message.value = payload.message })
  core.on('auth.completed', async () => { message.value = '微信读书已连接'; await reload() })
  core.on('auth.failed', payload => message.value = payload.message)
  core.on('verification.required', async payload => { busy.value = false; message.value = payload.message; await reload() })
  core.on('sync.skipped', async payload => { busy.value = false; message.value = payload.message; await reload() })
  core.on('link_resolution.progress', payload => { message.value = payload.message })
  core.on('credential.expired', payload => { message.value = payload.message; notify('需要重新连接', payload.message) })
  core.on('provider.cooldown', async payload => { busy.value = false; message.value = payload.message; await reload() })
  core.on('core.error', payload => message.value = payload.message)
  core.on('briefing.ready', async () => { message.value = '今日简报已经准备好'; notify('每日简报', '今天的简报已经准备好'); await reload() })
  try {
    await listen<string>('tray-action', event => {
      if (event.payload === 'refresh') sync()
      if (event.payload === 'briefing') generateBriefing()
      if (event.payload === 'pause') toggleRefreshPause()
    })
  } catch { /* browser preview */ }
  checkForUpdates(false)
})

async function reload() {
  loading.value = true
  try {
    bootstrap.value = await core.call('app.bootstrap')
    settingsDraft.value = JSON.parse(JSON.stringify(bootstrap.value.settings))
    if (!selectedBriefingDay.value && bootstrap.value.briefings.length) selectedBriefingDay.value = bootstrap.value.briefings[0].day
    applyTheme(settingsDraft.value.theme)
  }
  catch (error: any) { message.value = error.message }
  finally { loading.value = false }
}
async function sync(sourceId = '') {
  busy.value = true; message.value = '正在检查新文章…'
  try { await core.call('subscriptions.sync', { source_id: sourceId }) }
  catch (error: any) { message.value = error.message; busy.value = false }
  setTimeout(() => { if (busy.value) busy.value = false }, 1500)
}
async function resolveLink() {
  if (!addUrl.value.trim()) return
  busy.value = true; resolvedSource.value = null; message.value = '正在读取文章信息…'
  try { resolvedSource.value = await core.call('subscriptions.resolve_link', { url: addUrl.value.trim() }) }
  catch (error: any) { message.value = error.message }
  finally { busy.value = false }
}
async function followSource() {
  if (!resolvedSource.value) return
  await core.call('subscriptions.add', { source: resolvedSource.value })
  message.value = `已关注 ${resolvedSource.value.name}`
  const sourceId = resolvedSource.value.id
  resolvedSource.value = null; addUrl.value = ''
  await reload()
  if (!connectionReady.value) await core.call('auth.start', { source_id: sourceId })
  else await sync(sourceId)
}
async function toggleSource(source: any) {
  await core.call('subscriptions.update', { source_id: source.id, enabled: !source.enabled }); await reload()
}
async function removeSource(source: any) {
  if (!confirm(`确定不再关注“${source.name}”吗？本地文章也会一并移除。`)) return
  await core.call('subscriptions.remove', { source_id: source.id }); await reload()
}
async function openArticle(item: any) {
  article.value = await core.call('articles.get', { article_id: item.id })
  await core.call('articles.mark_read', { article_id: item.id, read: true })
  item.is_read = 1
}
async function retrySummary() {
  if (!article.value) return
  busy.value = true
  try { article.value = await core.call('articles.retry_summary', { article_id: article.value.id }); message.value = '内容摘要已更新' }
  catch (error: any) { message.value = error.message }
  finally { busy.value = false }
}
async function generateBriefing() {
  busy.value = true
  try { await core.call('briefings.generate', { day: new Date().toISOString().slice(0, 10) }); message.value = '今日简报已经生成'; await reload() }
  catch (error: any) { message.value = error.message }
  finally { busy.value = false }
}
async function saveSettings() {
  try { settingsDraft.value.autostart ? await enableAutostart() : await disableAutostart() } catch { /* browser preview */ }
  const ai = { ...settingsDraft.value.ai, enabled: settingsDraft.value.auto_summary, api_key: aiKey.value || undefined }
  const settings = { ...settingsDraft.value, ai }
  const result: any = await core.call('settings.update', { settings })
  aiKey.value = ''; message.value = '设置已保存'; await reload()
  if (result.briefing_due_now && confirm('今天的简报时间已经过去，是否现在生成？')) await generateBriefing()
}
async function testAi() {
  const payload = { ...settingsDraft.value.ai, api_key: aiKey.value || undefined }
  const result: any = await core.call('ai.test_connection', payload); message.value = result.message
}
function formatTime(value: string) {
  if (!value) return '尚未刷新'
  const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit' }).format(date)
}
function sourceInitial(name: string) { return (name || '公').slice(0, 1) }
function applyTheme(theme?: string) {
  const value = theme || 'light'
  document.documentElement.dataset.theme = value
}
async function reconnect(sourceId = '') {
  const id = sourceId || bootstrap.value.sources[0]?.id
  if (!id) { page.value = 'sources'; message.value = '?????????'; return }
  try {
    await core.call('auth.reconnect', { source_id: id })
    message.value = '????????'
  } catch (error: any) {
    message.value = error.message
  }
}
async function toggleRefreshPause() {
  const paused = !bootstrap.value.settings.refresh_paused
  await core.call('settings.update', { settings: { refresh_paused: paused } })
  message.value = paused ? '已暂停自动刷新' : '已恢复自动刷新'
  await reload()
}
async function openBriefingFolder() {
  const item = currentBriefing.value
  if (item?.file_path) await core.call('system.open_path', { path: item.file_path.replace(/[\\/][^\\/]+$/, '') })
}
async function checkForUpdates(showResult = true) {
  try {
    const last = localStorage.getItem('gzhreader-update-check')
    const todayKey = new Date().toISOString().slice(0, 10)
    if (!showResult && last === todayKey) return
    localStorage.setItem('gzhreader-update-check', todayKey)
    const update = await check()
    if (!update) { if (showResult) message.value = '当前已经是最新版本'; return }
    if (confirm(`发现新版本 ${update.version}，是否现在更新？`)) {
      message.value = '正在下载更新…'
      await update.downloadAndInstall()
      await relaunch()
    }
  } catch {
    if (showResult) message.value = '暂时无法检查更新，请稍后再试'
  }
}
async function openBriefingLink(event: MouseEvent) {
  const target = (event.target as HTMLElement).closest('a')
  const href = target?.getAttribute('href') || ''
  if (!href) return
  event.preventDefault()
  try {
    const url = new URL(href)
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('unsupported protocol')
    await core.call('system.open_url', { url: url.toString() })
  } catch {
    message.value = '这个链接暂时无法打开'
  }
}

async function notify(title: string, body: string) {
  try {
    let allowed = await isPermissionGranted()
    if (!allowed) allowed = (await requestPermission()) === 'granted'
    if (allowed) sendNotification({ title, body })
  } catch { /* browser preview */ }
}
</script>

<template>
  <div class="app-shell" :class="{ loading, 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-mark" src="/gzhreader-logo.svg" alt="" aria-hidden="true" />
        <div class="brand-copy"><strong>GZHReader</strong><span>公众号工作台</span></div>
      </div>
      <button
        class="sidebar-toggle"
        type="button"
        :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
        :aria-pressed="sidebarCollapsed"
        @click="sidebarCollapsed = !sidebarCollapsed"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="sidebarCollapsed ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'"/></svg>
      </button>
      <nav class="nav-list" aria-label="主导航">
        <button v-for="item in nav" :key="item.id" :class="['nav-item', { active: page === item.id }]" :title="sidebarCollapsed ? item.label : ''" @click="page = item.id">
          <svg v-if="item.icon === 'home'" viewBox="0 0 24 24"><path d="M4 10.5 12 4l8 6.5V20H5a1 1 0 0 1-1-1z"/><path d="M9 20v-6h6v6"/></svg>
          <svg v-else-if="item.icon === 'sources'" viewBox="0 0 24 24"><circle cx="8" cy="12" r="3"/><circle cx="17" cy="7" r="3"/><circle cx="17" cy="17" r="3"/><path d="m10.5 10.5 4-2M10.5 13.5l4 2"/></svg>
          <svg v-else-if="item.icon === 'articles'" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7"/></svg>
          <svg v-else viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>
          <span class="nav-label">{{ item.label }}</span>
          <i v-if="item.id === 'articles' && bootstrap.dashboard.unread_count" class="count">{{ bootstrap.dashboard.unread_count }}</i>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <button :class="['nav-item', { active: page === 'settings' }]" :title="sidebarCollapsed ? '设置' : ''" @click="page = 'settings'">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19 13.5v-3l-2-.7-.7-1.7.9-1.9-2.1-2.1-1.9.9-1.7-.7-.7-2h-3l-.7 2-1.7.7-1.9-.9-2.1 2.1.9 1.9-.7 1.7-2 .7v3l2 .7.7 1.7-.9 1.9 2.1 2.1 1.9-.9 1.7.7.7 2h3l.7-2 1.7-.7 1.9.9 2.1-2.1-.9-1.9.7-1.7z"/></svg>
          <span class="nav-label">设置</span>
        </button>
        <div class="connection" :class="connectionReady ? 'ok' : 'warn'">
          <span class="status-dot"></span><span>{{ bootstrap.connection.message || '正在检查连接' }}</span>
        </div>
      </div>
    </aside>

    <main class="workspace">
      <div v-if="message" class="toast" @click="message = ''">{{ message }}</div>

      <section v-if="page === 'home'" class="page home-page">
        <header class="page-header home-header">
          <div><p class="eyebrow">{{ today }}</p><h1>今天的阅读</h1><p>重要内容已经按时间整理在这里。</p></div>
          <button class="primary-button" :disabled="busy" @click="sync()">{{ busy ? '正在刷新' : '立即刷新' }}</button>
        </header>
        <div class="briefing-lead" :class="{ ready: bootstrap.dashboard.today_briefing }">
          <div class="briefing-copy">
            <span class="section-label">每日简报</span>
            <h2>{{ bootstrap.dashboard.today_briefing ? '今天的简报已经准备好' : '今天的简报将在 ' + (bootstrap.settings.briefing_time || '21:30') + ' 生成' }}</h2>
            <p>{{ bootstrap.dashboard.today_briefing?.overview || `当前有 ${bootstrap.dashboard.today_count || 0} 篇今日文章，内容会持续更新。` }}</p>
          </div>
          <button v-if="bootstrap.dashboard.today_briefing" class="text-button" @click="page = 'briefings'">打开简报</button>
          <button v-else class="secondary-button" @click="generateBriefing">现在生成</button>
        </div>
        <div class="status-strip">
          <div><span>今日新增</span><strong>{{ bootstrap.dashboard.today_count || 0 }}</strong></div>
          <div><span>未读文章</span><strong>{{ bootstrap.dashboard.unread_count || 0 }}</strong></div>
          <div><span>最近刷新</span><strong class="small-value">{{ formatTime(bootstrap.settings.last_refresh_at) }}</strong></div>
          <div><span>下次刷新</span><strong class="small-value">{{ formatTime(bootstrap.settings.next_refresh_at) }}</strong></div>
        </div>
        <div class="section-heading"><div><h2>最新文章</h2><p>来自你关注的公众号</p></div><button class="text-button" @click="page = 'articles'">查看全部</button></div>
        <div v-if="bootstrap.articles.length" class="article-list compact">
          <button v-for="item in bootstrap.articles.slice(0, 6)" :key="item.id" class="article-row" @click="openArticle(item)">
            <span class="source-avatar">{{ sourceInitial(item.source_name) }}</span>
            <span class="article-main"><span class="article-meta">{{ item.source_name }} · {{ formatTime(item.published_at) }}</span><strong>{{ item.title }}</strong><span>{{ item.summary || '正在整理内容…' }}</span></span>
            <span v-if="!item.is_read" class="unread-dot"></span>
          </button>
        </div>
        <div v-else class="empty-state"><h3>还没有文章</h3><p>添加一个公众号后，最新内容会出现在这里。</p><button class="secondary-button" @click="page = 'sources'">添加公众号</button></div>
      </section>

      <section v-else-if="page === 'sources'" class="page">
        <header class="page-header"><div><p class="eyebrow">内容来源</p><h1>关注的公众号</h1><p>粘贴任意一篇公众号文章链接，即可开始关注。</p></div></header>
        <div v-if="!connectionReady && bootstrap.sources.length" class="connection-banner"><div><strong>{{ connectionTitle }}</strong><p>{{ bootstrap.connection.message || '完成后公众号会继续更新。' }}</p></div><button v-if="!connectionCoolingDown" class="secondary-button" @click="reconnect()">在浏览器中验证</button></div>
        <div class="add-source-panel">
          <label for="article-url">公众号文章链接</label>
          <div class="input-action"><input id="article-url" v-model="addUrl" placeholder="https://mp.weixin.qq.com/s/..." @keyup.enter="resolveLink"/><button class="primary-button" :disabled="busy || !addUrl" @click="resolveLink">识别公众号</button></div>
          <p class="help-text">链接只用于识别公众号，不会公开或上传到其他服务。</p>
          <div v-if="resolvedSource" class="source-confirm">
            <span class="source-avatar large">{{ sourceInitial(resolvedSource.name) }}</span><div><strong>{{ resolvedSource.name }}</strong><p>{{ resolvedSource.intro || '已识别公众号信息' }}</p></div><button class="primary-button" @click="followSource">开始关注</button>
          </div>
        </div>
        <div class="section-heading"><div><h2>已关注</h2><p>{{ bootstrap.sources.length }} 个公众号</p></div></div>
        <div v-if="bootstrap.sources.length" class="source-list">
          <div v-for="source in bootstrap.sources" :key="source.id" class="source-row">
            <span class="source-avatar large">{{ sourceInitial(source.name) }}</span>
            <div class="source-info"><strong>{{ source.name }}</strong><span>{{ source.intro || '公众号内容' }}</span><small>{{ source.last_success_at ? '上次更新 ' + formatTime(source.last_success_at) : '等待首次更新' }}</small></div>
            <span class="plain-status" :class="source.status">{{ source.status === 'limited' ? (bootstrap.connection.state === 'verification' ? '等待重新验证' : bootstrap.connection.state === 'cooldown' ? '冷却中' : '暂不支持自动更新') : source.enabled ? '正在关注' : '已暂停' }}</span>
            <div class="row-actions"><button @click="sync(source.id)">刷新</button><button @click="toggleSource(source)">{{ source.enabled ? '暂停' : '继续' }}</button><button class="danger-text" @click="removeSource(source)">删除</button></div>
          </div>
        </div>
        <div v-else class="empty-state left"><h3>从一个公众号开始</h3><p>找到你想关注的公众号文章，把链接粘贴到上方。</p></div>
      </section>

      <section v-else-if="page === 'articles'" class="page articles-page">
        <header class="page-header"><div><p class="eyebrow">收件箱</p><h1>全部文章</h1><p>按发布时间查看关注公众号的最新内容。</p></div></header>
        <div class="toolbar"><input v-model="query" class="search-input" placeholder="搜索标题、正文或摘要"/><select v-model="selectedSource"><option value="">全部公众号</option><option v-for="s in bootstrap.sources" :key="s.id" :value="s.id">{{ s.name }}</option></select><label class="check"><input v-model="unreadOnly" type="checkbox"/>只看未读</label></div>
        <div v-if="filteredArticles.length" class="article-list">
          <button v-for="item in filteredArticles" :key="item.id" class="article-row" @click="openArticle(item)">
            <span class="source-avatar">{{ sourceInitial(item.source_name) }}</span><span class="article-main"><span class="article-meta">{{ item.source_name }} · {{ formatTime(item.published_at) }}</span><strong>{{ item.title }}</strong><span>{{ item.takeaway || item.summary || '正在整理内容…' }}</span><span class="tag-line"><i v-for="tag in item.tags" :key="tag">{{ tag }}</i></span></span><span v-if="!item.is_read" class="unread-dot"></span>
          </button>
        </div>
        <div v-else class="empty-state"><h3>没有符合条件的文章</h3><p>调整筛选条件，或者刷新关注的公众号。</p></div>
      </section>

      <section v-else-if="page === 'briefings'" class="page briefing-page">
        <header class="page-header"><div><p class="eyebrow">阅读归档</p><h1>每日简报</h1><p>每天的重要内容和主题都保存在这里。</p></div><button class="secondary-button" @click="generateBriefing">生成今日简报</button></header>
        <div v-if="bootstrap.briefings.length" class="briefing-layout">
          <aside class="briefing-index">
            <button v-for="item in bootstrap.briefings" :key="item.day" :class="{ active: currentBriefing?.day === item.day }" @click="selectedBriefingDay = item.day">
              <strong>{{ item.day }}</strong><span>{{ item.article_count }} 篇文章</span>
            </button>
          </aside>
          <article v-if="currentBriefing" class="briefing-document">
            <div class="briefing-actions"><button class="text-button" @click="generateBriefing">重新生成</button><button class="text-button" @click="openBriefingFolder">打开文件目录</button></div>
            <div class="briefing-heading"><span>每日简报</span><strong>{{ currentBriefing.day }}</strong></div>
            <p v-if="currentBriefing.overview" class="briefing-overview">{{ currentBriefing.overview }}</p>
            <div class="briefing-markdown" @click="openBriefingLink" v-html="renderedBriefing"></div>
          </article>
        </div>
        <div v-else class="empty-state"><h3>还没有每日简报</h3><p>到设定时间后，应用会自动整理当天的文章。</p><button class="secondary-button" @click="generateBriefing">生成今日简报</button></div>
      </section>

      <section v-else class="page settings-page">
        <header class="page-header"><div><p class="eyebrow">偏好设置</p><h1>设置</h1><p>调整内容更新、摘要和每日简报。</p></div><button class="primary-button" @click="saveSettings">保存设置</button></header>
        <div class="settings-section"><div class="settings-title"><h2>智能摘要</h2><p>使用你自己的内容整理服务。</p></div><div class="settings-fields"><label>服务地址<input v-model="settingsDraft.ai.base_url" placeholder="https://api.example.com/v1"/></label><label>密钥<input v-model="aiKey" type="password" :placeholder="settingsDraft.ai.has_api_key ? '已保存，如需更换请重新输入' : '请输入密钥'"/></label><label>模型名称<input v-model="settingsDraft.ai.model" placeholder="例如 gpt-4o-mini"/></label><div class="inline-setting"><label class="switch-label"><input v-model="settingsDraft.auto_summary" type="checkbox"/>自动整理新文章</label><button class="secondary-button small" @click="testAi">测试连接</button></div></div></div>
        <div class="settings-section"><div class="settings-title"><h2>自动刷新</h2><p>应用在托盘运行时会按这个频率检查新内容。</p></div><div class="settings-fields"><label>刷新频率<select v-model.number="settingsDraft.refresh_minutes"><option :value="15">每 15 分钟</option><option :value="30">每 30 分钟</option><option :value="60">每 1 小时</option><option :value="120">每 2 小时</option><option :value="240">每 4 小时</option><option :value="0">仅手动刷新</option></select></label></div></div>
        <div class="settings-section"><div class="settings-title"><h2>每日简报</h2><p>在你习惯的时间整理当天内容。</p></div><div class="settings-fields"><label class="switch-label"><input v-model="settingsDraft.briefing_enabled" type="checkbox"/>每天自动生成简报</label><label>生成时间<input v-model="settingsDraft.briefing_time" type="time" :disabled="!settingsDraft.briefing_enabled"/></label></div></div>
        <div class="settings-section"><div class="settings-title"><h2>应用设置</h2><p>控制应用如何在电脑上运行。</p></div><div class="settings-fields"><label class="switch-label"><input v-model="settingsDraft.autostart" type="checkbox"/>登录 Windows 后自动启动</label><label>外观<select v-model="settingsDraft.theme"><option value="light">浅色</option><option value="dark">深色</option><option value="system">跟随系统</option></select></label><button class="secondary-button small update-button" @click="checkForUpdates()">检查更新</button></div></div>
      </section>
    </main>

    <div v-if="article" class="drawer-backdrop" @click.self="article = null"><aside class="article-drawer"><button class="drawer-close" @click="article = null">关闭</button><div class="article-detail-head"><span class="article-meta">{{ article.source_name }} · {{ formatTime(article.published_at) }}</span><h1>{{ article.title }}</h1><p v-if="article.takeaway" class="takeaway">{{ article.takeaway }}</p></div><section class="summary-section"><div class="section-heading"><h2>内容摘要</h2><button class="text-button" @click="retrySummary">重新整理</button></div><p>{{ article.summary || '正在整理内容…' }}</p><ul v-if="article.key_points?.length"><li v-for="point in article.key_points" :key="point">{{ point }}</li></ul><div class="tag-line"><i v-for="tag in article.tags" :key="tag">{{ tag }}</i></div></section><section class="content-section"><h2>正文</h2><p>{{ article.content || '正文暂时无法获取，请打开微信原文阅读。' }}</p></section><button class="primary-button full" @click="core.call('system.open_url', { url: article.url })">打开微信原文</button></aside></div>
  </div>
</template>
