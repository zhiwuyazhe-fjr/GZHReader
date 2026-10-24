export type Page = 'home' | 'sources' | 'articles' | 'briefings' | 'settings'
export type Theme = 'light' | 'dark' | 'system'

export interface Source {
  id: string
  name: string
  intro: string
  avatar: string
  sample_url?: string
  enabled?: number | boolean
  status: string
  last_success_at?: string
}

export interface Article {
  id: number
  source_id: string
  source_name: string
  title: string
  published_at: string
  summary: string
  takeaway: string
  key_points: string[]
  tags: string[]
  content: string
  url: string
  is_read: number
  summary_status: string
}

export interface Briefing {
  day: string
  overview: string
  article_count: number
  markdown: string
  file_path: string
}

export interface AiConfig {
  base_url: string
  model: string
  has_api_key: boolean
  enabled: boolean
}

export interface Settings {
  refresh_minutes: number
  refresh_paused: boolean
  briefing_enabled: boolean
  briefing_time: string
  auto_summary: boolean
  autostart: boolean
  theme: Theme
  timezone: string
  last_refresh_at: string
  next_refresh_at: string
  ai: AiConfig
}

export interface Bootstrap {
  dashboard: { today_count: number; unread_count: number; today_briefing: Briefing | null }
  sources: Source[]
  articles: Article[]
  briefings: Briefing[]
  settings: Settings
  connection: { state: string; message: string; reconnect_required?: boolean }
  scheduler: { running: boolean; syncing?: boolean }
}

export function defaultSettings(): Settings {
  return {
    refresh_minutes: 60, refresh_paused: false, briefing_enabled: true,
    briefing_time: '21:30', auto_summary: true, autostart: true, theme: 'light',
    timezone: 'Asia/Shanghai', last_refresh_at: '', next_refresh_at: '',
    ai: { base_url: '', model: '', has_api_key: false, enabled: true },
  }
}
