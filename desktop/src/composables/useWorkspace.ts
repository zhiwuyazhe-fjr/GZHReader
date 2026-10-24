import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listen } from '@tauri-apps/api/event'
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { enable, disable, isEnabled } from '@tauri-apps/plugin-autostart'
import { check } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'
import { core, isDesktop } from '../lib/core'
import { dayKey } from '../lib/dates'
import { defaultSettings, type Article, type Bootstrap, type Briefing, type Page, type Settings, type Source } from '../lib/types'

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作没有完成，请稍后重试'

export function useWorkspace() {
  const page = ref<Page>('home')
  const loading = ref(true)
  const loadFailed = ref(false)
  const message = ref('')
  const syncing = ref(false)
  const actions = ref(new Set<string>())
  const busy = computed(() => syncing.value || actions.value.size > 0)
  const bootstrap = ref<Bootstrap>({
    dashboard: { today_count: 0, unread_count: 0, today_briefing: null },
    sources: [], articles: [], briefings: [], settings: defaultSettings(),
    connection: { state: '', message: '' }, scheduler: { running: false },
  })
  const settingsDraft = ref(defaultSettings())
  let settingsBaseline = JSON.stringify(settingsDraft.value)
  const aiKey = ref('')
  const article = ref<Article | null>(null)
  let articleRequest = 0
  const addUrl = ref('')
  const resolvedSource = ref<Source | null>(null)
  const query = ref('')
  const unreadOnly = ref(false)
  const selectedSource = ref('')
  const articleItems = ref<Article[]>([])
  const articlesLoading = ref(false)
  const articlesFailed = ref(false)
  const articlePage = ref(0)
  const hasMore = ref(false)
  const selectedBriefingDay = ref('')
  const currentBriefing = computed(() => bootstrap.value.briefings.find(item => item.day === selectedBriefingDay.value) || bootstrap.value.briefings[0] || null)
  const connectionReady = computed(() => bootstrap.value.connection.state === 'ready')
  const connectionCoolingDown = computed(() => bootstrap.value.connection.state === 'cooldown')
  const connectionTitle = computed(() => connectionCoolingDown.value
    ? '微信读书暂时限制验证'
    : bootstrap.value.connection.state === 'verification' ? '需要重新确认访问' : '需要重新连接微信读书')
  const sidebarCollapsed = ref(readLocal('gzhreader.sidebarCollapsed') === '1')
  const today = ref('')
  let reloadRequest = 0
  let listRequest = 0
  let listTimer: ReturnType<typeof setTimeout> | undefined
  let calendarTimer: ReturnType<typeof setInterval> | undefined
  let disposed = false
  const cleanups: Array<() => void> = []
  const colorScheme = window.matchMedia('(prefers-color-scheme: dark)')

  function applyTheme() {
    const theme = settingsDraft.value.theme
    document.documentElement.dataset.theme = theme === 'system' ? colorScheme.matches ? 'dark' : 'light' : theme
  }
  watch(() => settingsDraft.value.theme, applyTheme)
  watch(sidebarCollapsed, value => writeLocal('gzhreader.sidebarCollapsed', value ? '1' : '0'))
  watch([query, unreadOnly, selectedSource], () => { articlePage.value = 0; scheduleArticles() })
  watch([page, articlePage], () => { if (page.value === 'articles') scheduleArticles() })

  async function runAction<T>(name: string, action: () => Promise<T>): Promise<T | undefined> {
    if (actions.value.has(name)) return
    actions.value.add(name)
    try { return await action() }
    catch (error) { message.value = errorMessage(error) }
    finally { actions.value.delete(name) }
  }

  function updateToday() {
    today.value = new Intl.DateTimeFormat('zh-CN', {
      timeZone: bootstrap.value.settings.timezone, month: 'long', day: 'numeric', weekday: 'long',
    }).format(new Date())
  }

  async function reload() {
    const request = ++reloadRequest
    loading.value = true
    try {
      const data = await core.call<Bootstrap>('app.bootstrap')
      if (disposed || request !== reloadRequest) return
      bootstrap.value = data
      syncing.value = Boolean(data.scheduler.syncing)
      loadFailed.value = false
      // Background events must not overwrite unsaved form fields or a newly entered key.
      if (JSON.stringify(settingsDraft.value) === settingsBaseline) {
        settingsDraft.value = clone(data.settings)
        settingsBaseline = JSON.stringify(settingsDraft.value)
      }
      if (!selectedBriefingDay.value && data.briefings.length) selectedBriefingDay.value = data.briefings[0]!.day
      updateToday()
      if (page.value === 'articles') await loadArticles()
    } catch (error) {
      if (request === reloadRequest) { loadFailed.value = true; message.value = errorMessage(error) }
    } finally { if (request === reloadRequest) loading.value = false }
  }

  function scheduleArticles() {
    ++listRequest // Invalidate a request as soon as its search/filter changes.
    if (listTimer) clearTimeout(listTimer)
    if (page.value !== 'articles') return
    articlesLoading.value = true
    listTimer = setTimeout(() => { void loadArticles() }, 250)
  }

  async function loadArticles() {
    if (listTimer) clearTimeout(listTimer)
    const request = ++listRequest
    articlesLoading.value = true
    articlesFailed.value = false
    try {
      const items = await core.call<Article[]>('articles.list', {
        query: query.value.trim(), source_id: selectedSource.value, unread_only: unreadOnly.value,
        offset: articlePage.value * 50, limit: 51,
      })
      if (disposed || request !== listRequest) return
      articleItems.value = items.slice(0, 50)
      hasMore.value = items.length > 50
    } catch (error) {
      if (request === listRequest) { articlesFailed.value = true; message.value = errorMessage(error) }
    } finally { if (request === listRequest) articlesLoading.value = false }
  }

  async function sync(sourceId = '') {
    if (syncing.value) return
    syncing.value = true
    message.value = '正在检查新文章…'
    try {
      const result = await core.call<{ accepted: boolean; message?: string }>('subscriptions.sync', { source_id: sourceId })
      if (!result.accepted) { message.value = result.message || '正在刷新，请稍候'; await reload() }
    } catch (error) { syncing.value = false; message.value = errorMessage(error) }
  }

  const resolveLink = () => runAction('resolve', async () => {
    if (!addUrl.value.trim()) return
    resolvedSource.value = null
    message.value = '正在读取文章信息…'
    resolvedSource.value = await core.call<Source>('subscriptions.resolve_link', { url: addUrl.value.trim() })
  })

  const followSource = () => runAction('follow', async () => {
    if (!resolvedSource.value) return
    const source = resolvedSource.value
    await core.call('subscriptions.add', { source })
    message.value = `已关注 ${source.name}`
    resolvedSource.value = null
    addUrl.value = ''
    await reload()
    if (!connectionReady.value) await core.call('auth.start', { source_id: source.id })
    else await sync(source.id)
  })

  const toggleSource = (source: Source) => runAction(`source-${source.id}`, async () => {
    await core.call('subscriptions.update', { source_id: source.id, enabled: !source.enabled })
    await reload()
  })

  const removeSource = (source: Source) => runAction(`source-${source.id}`, async () => {
    if (!confirm(`确定不再关注“${source.name}”吗？本地文章也会一并移除。`)) return
    await core.call('subscriptions.remove', { source_id: source.id })
    if (selectedSource.value === source.id) selectedSource.value = ''
    await reload()
  })

  async function openArticle(item: Article) {
    const request = ++articleRequest
    try {
      const detail = await core.call<Article>('articles.get', { article_id: item.id })
      if (disposed || request !== articleRequest) return
      article.value = detail
      await core.call('articles.mark_read', { article_id: item.id, read: true })
      if (!item.is_read) bootstrap.value.dashboard.unread_count = Math.max(0, bootstrap.value.dashboard.unread_count - 1)
      for (const entry of [...bootstrap.value.articles, ...articleItems.value]) if (entry.id === item.id) entry.is_read = 1
      if (unreadOnly.value && page.value === 'articles') await loadArticles()
    } catch (error) { message.value = errorMessage(error) }
  }

  function closeArticle() { ++articleRequest; article.value = null }

  const retrySummary = () => runAction('summary', async () => {
    if (!article.value) return
    const id = article.value.id
    const updated = await core.call<Article>('articles.retry_summary', { article_id: id })
    if (article.value?.id === id) article.value = updated
    message.value = updated.summary_status === 'done' ? '内容摘要已更新' : '已保留临时摘要，摘要服务恢复后可重新整理'
    await reload()
  })

  const generateBriefing = (day?: string) => runAction('briefing', async () => {
    const result = await core.call<Briefing>('briefings.generate', { day: day || dayKey(new Date(), bootstrap.value.settings.timezone) })
    selectedBriefingDay.value = result.day
    message.value = `${result.day} 的简报已经生成`
    await reload()
  })

  const saveSettings = () => runAction('settings', async () => {
    const draft = clone(settingsDraft.value)
    const settings = {
      refresh_minutes: draft.refresh_minutes, refresh_paused: draft.refresh_paused,
      briefing_enabled: draft.briefing_enabled, briefing_time: draft.briefing_time,
      auto_summary: draft.auto_summary, autostart: draft.autostart, theme: draft.theme,
      ai: { ...draft.ai, enabled: draft.auto_summary, ...(aiKey.value ? { api_key: aiKey.value } : {}) },
    }
    let result = await core.call<Settings & { briefing_due_now?: boolean }>('settings.update', { settings })
    let notice = '设置已保存'
    if (isDesktop && draft.autostart !== bootstrap.value.settings.autostart) {
      try { await (draft.autostart ? enable() : disable()) }
      catch (error) {
        result = { ...result, ...await core.call<Settings>('settings.update', { settings: { autostart: await isEnabled() } }) }
        notice = `其他设置已保存，开机启动设置未生效：${errorMessage(error)}`
      }
    }
    aiKey.value = ''
    settingsDraft.value = clone(result)
    settingsBaseline = JSON.stringify(settingsDraft.value)
    message.value = notice
    await reload()
    if (result.briefing_due_now && confirm('今天的简报时间已经过去，是否现在生成？')) await generateBriefing()
  })

  const testAi = () => runAction('ai-test', async () => {
    const result = await core.call<{ message: string }>('ai.test_connection', {
      ...settingsDraft.value.ai, ...(aiKey.value ? { api_key: aiKey.value } : {}),
    })
    message.value = result.message
  })

  const reconnect = (sourceId = '') => runAction('auth', async () => {
    const id = sourceId || bootstrap.value.sources[0]?.id
    if (!id) { page.value = 'sources'; message.value = '请先添加一个公众号'; return }
    await core.call('auth.reconnect', { source_id: id })
    message.value = '请在打开的浏览器中完成微信读书连接'
  })

  const toggleRefreshPause = () => runAction('pause', async () => {
    const paused = !bootstrap.value.settings.refresh_paused
    await core.call('settings.update', { settings: { refresh_paused: paused } })
    message.value = paused ? '已暂停自动刷新' : '已恢复自动刷新'
    await reload()
  })

  const openBriefingFolder = () => runAction('folder', async () => {
    if (currentBriefing.value?.file_path) await core.call('system.open_path', { path: currentBriefing.value.file_path.replace(/[\\/][^\\/]+$/, '') })
  })

  const openOriginal = () => runAction('original', async () => {
    if (article.value) await core.call('system.open_url', { url: article.value.url })
  })

  const checkForUpdates = (showResult = true) => runAction('update', async () => {
    if (!isDesktop) { if (showResult) message.value = '请在桌面应用中检查更新'; return }
    const todayKey = dayKey()
    if (!showResult && readLocal('gzhreader-update-check') === todayKey) return
    try {
      const update = await check()
      writeLocal('gzhreader-update-check', todayKey)
      if (!update) { if (showResult) message.value = '当前已经是最新版本'; return }
      if (confirm(`发现新版本 ${update.version}，是否现在更新？`)) {
        message.value = '正在下载更新…'
        await update.downloadAndInstall()
        await relaunch()
      }
    } catch { if (showResult) message.value = '暂时无法检查更新，请稍后再试' }
  })

  async function openBriefingLink(event: MouseEvent) {
    const target = event.target instanceof Element ? event.target.closest('a') : null
    const href = target?.getAttribute('href')
    if (!href) return
    event.preventDefault()
    await runAction('link', async () => {
      const url = new URL(href)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('这个链接暂时无法打开')
      await core.call('system.open_url', { url: url.toString() })
    })
  }

  onMounted(async () => {
    cleanups.push(
      core.on('sync.started', () => { syncing.value = true }),
      core.on<{ inserted?: number; errors?: string[] }>('sync.completed', async payload => {
        syncing.value = false
        message.value = payload.inserted ? `发现 ${payload.inserted} 篇新文章` : payload.errors?.[0] || '已是最新内容'
        await reload()
      }),
      core.on<{ message: string }>('sync.failed', async payload => { syncing.value = false; message.value = payload.message; await reload() }),
      core.on<{ count: number }>('articles.new_batch', payload => notify('发现新文章', `${payload.count} 篇新内容已经整理到工作台`)),
      core.on<{ message: string }>('auth.progress', payload => { message.value = payload.message }),
      core.on('auth.completed', async () => { message.value = '微信读书已连接'; await reload() }),
      core.on<{ message: string }>('auth.failed', payload => { message.value = payload.message }),
      core.on<{ message: string }>('verification.required', async payload => { syncing.value = false; message.value = payload.message; await reload() }),
      core.on<{ message: string }>('sync.skipped', async payload => { syncing.value = false; message.value = payload.message; await reload() }),
      core.on<{ message: string }>('link_resolution.progress', payload => { message.value = payload.message }),
      core.on<{ message: string }>('credential.expired', async payload => { message.value = payload.message; await reload(); await notify('需要重新连接', payload.message) }),
      core.on<{ message: string }>('provider.cooldown', async payload => { message.value = payload.message; await reload() }),
      core.on<{ message: string }>('core.error', payload => { message.value = payload.message }),
      core.on('core.disconnected', () => { syncing.value = false }),
      core.on('core.connected', reload),
      core.on('briefing.ready', async () => { await reload(); await notify('每日简报', '简报已经准备好') }),
    )
    if (isDesktop) {
      const unlisten = await listen<string>('tray-action', event => {
        if (event.payload === 'refresh') void sync()
        if (event.payload === 'briefing') void generateBriefing()
        if (event.payload === 'pause') void toggleRefreshPause()
      }).catch(() => undefined)
      if (unlisten) { if (disposed) unlisten(); else cleanups.push(unlisten) }
    }
    if (disposed) return
    colorScheme.addEventListener('change', applyTheme)
    cleanups.push(() => colorScheme.removeEventListener('change', applyTheme))
    await reload()
    if (disposed) return
    calendarTimer = setInterval(updateToday, 60_000)
    void checkForUpdates(false)
  })

  onBeforeUnmount(() => {
    disposed = true
    ++reloadRequest; ++listRequest; ++articleRequest
    cleanups.forEach(cleanup => cleanup())
    if (listTimer) clearTimeout(listTimer)
    if (calendarTimer) clearInterval(calendarTimer)
  })

  return { page, loading, loadFailed, busy, syncing, message, bootstrap, article, addUrl, resolvedSource, query, unreadOnly,
    selectedSource, settingsDraft, aiKey, selectedBriefingDay, sidebarCollapsed, currentBriefing, connectionReady, today,
    articleItems, articlesLoading, articlesFailed, articlePage, hasMore, reload, loadArticles, sync, resolveLink,
    connectionCoolingDown, connectionTitle, followSource, toggleSource, removeSource, openArticle, closeArticle, retrySummary, generateBriefing, saveSettings,
    testAi, reconnect, toggleRefreshPause, openBriefingFolder, openOriginal, checkForUpdates, openBriefingLink }
}

function readLocal(key: string) { try { return localStorage.getItem(key) } catch { return null } }
function writeLocal(key: string, value: string) { try { localStorage.setItem(key, value) } catch { /* Restricted browser storage. */ } }

async function notify(title: string, body: string) {
  if (!isDesktop) return
  try {
    let allowed = await isPermissionGranted()
    if (!allowed) allowed = (await requestPermission()) === 'granted'
    if (allowed) sendNotification({ title, body })
  } catch { /* System notifications are optional. */ }
}
