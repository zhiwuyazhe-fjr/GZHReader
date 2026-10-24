import { dayKey } from './dates'
import { defaultSettings, type Article, type Bootstrap, type Briefing, type Source } from './types'

/** Browser-only demo data, isolated from the desktop transport and persisted workspace. */
export function createPreviewClient() {
  const listeners = new Map<string, Set<(payload: unknown) => void | Promise<void>>>()
  const emit = (event: string, payload: unknown) => {
    for (const listener of listeners.get(event) ?? []) void Promise.resolve(listener(payload)).catch(console.warn)
  }
  const articles: Article[] = [
    { id: 1, source_id: 'demo-1', source_name: '少数派', title: '把信息整理成真正能回看的知识', published_at: new Date().toISOString(), summary: '文章讨论了信息收集与知识整理的差别。与其不断增加订阅，不如建立稳定的筛选、摘要和回顾节奏。', takeaway: '好的阅读系统不是收集更多，而是留下真正重要的内容。', key_points: ['先筛选，再阅读', '摘要服务于回顾', '定期清理低价值来源'], tags: ['阅读', '知识管理'], content: '这是一篇用于桌面界面预览的示例文章。整理个人资料库时，可以按主题归档并定期回顾。', url: 'https://mp.weixin.qq.com/', is_read: 0, summary_status: 'done' },
    { id: 2, source_id: 'demo-2', source_name: '产品沉思录', title: '如何保持长期而稳定的输入', published_at: new Date(Date.now() - 3600000).toISOString(), summary: '稳定的内容输入依赖清楚的关注范围和低摩擦的回顾工具，而不是依赖意志力。', takeaway: '让系统承担重复工作，把注意力留给判断。', key_points: ['减少重复操作', '保持固定节奏'], tags: ['产品', '方法'], content: '示例正文。', url: 'https://mp.weixin.qq.com/', is_read: 0, summary_status: 'done' },
  ]
  const state: Bootstrap = {
    dashboard: { today_count: 2, unread_count: 2, today_briefing: null },
    settings: defaultSettings(),
    sources: [
      { id: 'demo-1', name: '少数派', intro: '高效工作与品质生活', avatar: '', enabled: 1, status: 'active' },
      { id: 'demo-2', name: '产品沉思录', intro: '产品与个人成长', avatar: '', enabled: 1, status: 'active' },
    ], articles, briefings: [],
    connection: { state: 'disconnected', message: '需要连接微信读书', reconnect_required: true },
    scheduler: { running: true, syncing: false },
  }
  function findArticle(id: unknown) {
    const article = state.articles.find(item => item.id === id)
    if (!article) throw new Error('文章不存在或已经删除')
    return article
  }
  return {
    start: async () => {},
    stop: async () => {},
    on<T = unknown>(event: string, listener: (payload: T) => void | Promise<void>): () => void {
      const set = listeners.get(event) ?? new Set()
      const callback = (payload: unknown) => listener(payload as T)
      set.add(callback)
      listeners.set(event, set)
      return () => { set.delete(callback) }
    },
    async call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      let result: unknown
      switch (method) {
        case 'app.bootstrap':
          state.dashboard.unread_count = state.articles.filter(item => !item.is_read).length
          state.dashboard.today_briefing = state.briefings.find(item => item.day === dayKey(new Date(), state.settings.timezone)) ?? null
          result = state
          break
        case 'articles.list': {
          const words = String(params.query ?? '').toLowerCase().trim().split(/\s+/)
          const filtered = state.articles.filter(item =>
            (!params.source_id || item.source_id === params.source_id) && (!params.unread_only || !item.is_read) &&
            words.every(word => `${item.title} ${item.content} ${item.summary} ${item.source_name}`.toLowerCase().includes(word)))
          const offset = Number(params.offset ?? 0)
          result = filtered.slice(offset, offset + Number(params.limit ?? 200))
          break
        }
        case 'articles.get': case 'articles.retry_summary': result = findArticle(params.article_id); break
        case 'articles.mark_read': findArticle(params.article_id).is_read = params.read === false ? 0 : 1; result = { ok: true }; break
        case 'subscriptions.list': result = state.sources; break
        case 'subscriptions.resolve_link':
          if (!/^https?:\/\/mp\.weixin\.qq\.com\//.test(String(params.url))) throw new Error('请粘贴有效的公众号文章链接')
          result = { id: 'MP_WXS_preview', name: '识别到的公众号', intro: '请确认后开始关注', avatar: '', sample_url: params.url, status: 'active' }
          break
        case 'subscriptions.add': {
          const source = params.source as Source
          if (!state.sources.some(item => item.id === source.id)) state.sources.unshift({ ...source, enabled: 1 })
          result = source
          break
        }
        case 'subscriptions.update': {
          const source = state.sources.find(item => item.id === params.source_id)
          if (source) source.enabled = Boolean(params.enabled)
          result = { ok: true }
          break
        }
        case 'subscriptions.remove':
          state.sources = state.sources.filter(item => item.id !== params.source_id)
          state.articles = state.articles.filter(item => item.source_id !== params.source_id)
          result = { ok: true }
          break
        case 'subscriptions.sync':
          if (state.scheduler.syncing) return { accepted: false } as T
          state.scheduler.syncing = true
          emit('sync.started', {})
          setTimeout(() => { state.scheduler.syncing = false; emit('sync.completed', { inserted: 0, errors: [] }) }, 300)
          result = { accepted: true }
          break
        case 'auth.start': case 'auth.reconnect':
          state.connection = { state: 'ready', message: '微信读书已连接', reconnect_required: false }
          emit('auth.completed', {})
          result = { accepted: true }
          break
        case 'briefings.list': result = state.briefings; break
        case 'briefings.generate': {
          const day = String(params.day || dayKey(new Date(), state.settings.timezone))
          const briefing: Briefing = { day, overview: '今天的内容主要围绕阅读方法和产品思考。', article_count: state.articles.length, markdown: `# ${day} 每日简报\n\n今天的阅读内容已经整理完成。\n\n## 阅读与知识管理\n\n- 建立稳定的筛选、摘要和回顾节奏。\n- [打开示例原文](https://mp.weixin.qq.com/)`, file_path: `Documents/GZHReader/Briefings/${day}.md` }
          state.briefings = [briefing, ...state.briefings.filter(item => item.day !== day)].sort((a, b) => b.day.localeCompare(a.day))
          emit('briefing.ready', { day })
          result = briefing
          break
        }
        case 'settings.get': result = state.settings; break
        case 'settings.update': {
          const settings = params.settings as Partial<Bootstrap['settings']>
          state.settings = { ...state.settings, ...settings, ai: { ...state.settings.ai, ...settings.ai } }
          result = state.settings
          break
        }
        case 'ai.test_connection': result = { ok: true, message: '浏览器预览模式，不会连接真实摘要服务' }; break
        case 'system.open_url': case 'system.open_path': result = { ok: true }; break
        default: throw new Error(`预览不支持此操作：${method}`)
      }
      return structuredClone(result) as T
    },
  }
}
