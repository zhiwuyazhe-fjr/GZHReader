<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import ArticleDrawer from './components/ArticleDrawer.vue'
import { useWorkspace } from './composables/useWorkspace'
import { formatTime } from './lib/dates'

const {
  page, loading, loadFailed, busy, syncing, message, bootstrap, article, addUrl, resolvedSource, query,
  unreadOnly, selectedSource, settingsDraft, aiKey, selectedBriefingDay, sidebarCollapsed, currentBriefing,
  connectionReady, connectionCoolingDown, connectionTitle, today, articleItems, articlesLoading, articlesFailed, articlePage, hasMore,
  reload, loadArticles, sync, resolveLink, followSource, toggleSource, removeSource, openArticle,
  closeArticle, retrySummary, generateBriefing, saveSettings, testAi, reconnect, openBriefingFolder,
  openOriginal, checkForUpdates, openBriefingLink,
} = useWorkspace()
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderedBriefing = computed(() => markdown.render(currentBriefing.value?.markdown || ''))
const nav = [
  { id: 'home', label: '首页', icon: 'home' },
  { id: 'sources', label: '公众号', icon: 'sources' },
  { id: 'articles', label: '全部文章', icon: 'articles' },
  { id: 'briefings', label: '每日简报', icon: 'briefings' },
] as const
function sourceInitial(name: string) { return (name || '公').slice(0, 1) }
</script>

<template>
  <div class="app-shell" :class="{ loading, 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar" :inert="!!article">
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
        <button v-for="item in nav" :key="item.id" :class="['nav-item', { active: page === item.id }]" :aria-label="item.label" :aria-current="page === item.id ? 'page' : undefined" :title="sidebarCollapsed ? item.label : ''" @click="page = item.id">
          <svg v-if="item.icon === 'home'" viewBox="0 0 24 24"><path d="M4 10.5 12 4l8 6.5V20H5a1 1 0 0 1-1-1z"/><path d="M9 20v-6h6v6"/></svg>
          <svg v-else-if="item.icon === 'sources'" viewBox="0 0 24 24"><circle cx="8" cy="12" r="3"/><circle cx="17" cy="7" r="3"/><circle cx="17" cy="17" r="3"/><path d="m10.5 10.5 4-2M10.5 13.5l4 2"/></svg>
          <svg v-else-if="item.icon === 'articles'" viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7"/></svg>
          <svg v-else viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>
          <span class="nav-label">{{ item.label }}</span>
          <i v-if="item.id === 'articles' && bootstrap.dashboard.unread_count" class="count">{{ bootstrap.dashboard.unread_count }}</i>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <button :class="['nav-item', { active: page === 'settings' }]" aria-label="设置" :title="sidebarCollapsed ? '设置' : ''" @click="page = 'settings'">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19 13.5v-3l-2-.7-.7-1.7.9-1.9-2.1-2.1-1.9.9-1.7-.7-.7-2h-3l-.7 2-1.7.7-1.9-.9-2.1 2.1.9 1.9-.7 1.7-2 .7v3l2 .7.7 1.7-.9 1.9 2.1 2.1 1.9-.9 1.7.7.7 2h3l.7-2 1.7-.7 1.9.9 2.1-2.1-.9-1.9.7-1.7z"/></svg>
          <span class="nav-label">设置</span>
        </button>
        <div class="connection" :class="connectionReady ? 'ok' : 'warn'">
          <span class="status-dot"></span><span>{{ bootstrap.connection.message || '正在检查连接' }}</span>
        </div>
      </div>
    </aside>

    <main class="workspace" :inert="!!article">
      <div v-if="message" class="toast" role="status" aria-live="polite"><span>{{ message }}</span><button aria-label="关闭提示" @click="message = ''">×</button></div>
      <div v-if="loadFailed" class="load-status" role="alert">工作台暂时无法加载。<button class="text-button" @click="reload">重新加载</button></div>
      <div v-else-if="loading" class="load-status" role="status">正在更新工作台…</div>

      <section v-if="page === 'home'" class="page home-page">
        <header class="page-header home-header">
          <div><p class="eyebrow">{{ today }}</p><h1>今天的阅读</h1><p>重要内容已经按时间整理在这里。</p></div>
          <button class="primary-button" :disabled="busy" @click="sync()">{{ syncing ? '正在刷新' : '立即刷新' }}</button>
        </header>
        <div class="briefing-lead" :class="{ ready: bootstrap.dashboard.today_briefing }">
          <div class="briefing-copy">
            <span class="section-label">每日简报</span>
            <h2>{{ bootstrap.dashboard.today_briefing ? '今天的简报已经准备好' : bootstrap.settings.briefing_enabled ? '今天的简报将在 ' + bootstrap.settings.briefing_time + ' 生成' : '每日自动简报已暂停' }}</h2>
            <p>{{ bootstrap.dashboard.today_briefing?.overview || `当前有 ${bootstrap.dashboard.today_count || 0} 篇今日文章，内容会持续更新。` }}</p>
          </div>
          <button v-if="bootstrap.dashboard.today_briefing" class="text-button" @click="selectedBriefingDay = bootstrap.dashboard.today_briefing.day; page = 'briefings'">打开简报</button>
          <button v-else class="secondary-button" :disabled="busy || loading" @click="generateBriefing()">现在生成</button>
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
        <div v-if="!connectionReady && bootstrap.sources.length" class="connection-banner"><div><strong>{{ connectionTitle }}</strong><p>{{ bootstrap.connection.message || '完成后公众号会继续更新。' }}</p></div><button v-if="!connectionCoolingDown" class="secondary-button" :disabled="busy" @click="reconnect()">在浏览器中验证</button></div>
        <div class="add-source-panel">
          <label for="article-url">公众号文章链接</label>
          <div class="input-action"><input id="article-url" v-model="addUrl" placeholder="https://mp.weixin.qq.com/s/..." @keyup.enter="resolveLink"/><button class="primary-button" :disabled="busy || loading || !addUrl.trim()" @click="resolveLink">识别公众号</button></div>
          <p class="help-text">链接只用于识别公众号，不会公开或上传到其他服务。</p>
          <div v-if="resolvedSource" class="source-confirm">
            <span class="source-avatar large">{{ sourceInitial(resolvedSource.name) }}</span><div><strong>{{ resolvedSource.name }}</strong><p>{{ resolvedSource.intro || '已识别公众号信息' }}</p></div><button class="primary-button" :disabled="busy" @click="followSource">开始关注</button>
          </div>
        </div>
        <div class="section-heading"><div><h2>已关注</h2><p>{{ bootstrap.sources.length }} 个公众号</p></div></div>
        <div v-if="bootstrap.sources.length" class="source-list">
          <div v-for="source in bootstrap.sources" :key="source.id" class="source-row">
            <span class="source-avatar large">{{ sourceInitial(source.name) }}</span>
            <div class="source-info"><strong>{{ source.name }}</strong><span>{{ source.intro || '公众号内容' }}</span><small>{{ source.last_success_at ? '上次更新 ' + formatTime(source.last_success_at) : '等待首次更新' }}</small></div>
            <span class="plain-status" :class="source.status">{{ source.status === 'limited' ? (bootstrap.connection.state === 'verification' ? '等待重新验证' : bootstrap.connection.state === 'cooldown' ? '冷却中' : '暂不支持自动更新') : source.enabled ? '正在关注' : '已暂停' }}</span>
            <div class="row-actions"><button :disabled="busy" @click="sync(source.id)">刷新</button><button :disabled="busy" @click="toggleSource(source)">{{ source.enabled ? '暂停' : '继续' }}</button><button class="danger-text" :disabled="busy" @click="removeSource(source)">删除</button></div>
          </div>
        </div>
        <div v-else class="empty-state left"><h3>从一个公众号开始</h3><p>找到你想关注的公众号文章，把链接粘贴到上方。</p></div>
      </section>

      <section v-else-if="page === 'articles'" class="page articles-page">
        <header class="page-header"><div><p class="eyebrow">收件箱</p><h1>全部文章</h1><p>按发布时间查看关注公众号的最新内容。</p></div></header>
        <div class="toolbar"><input v-model="query" aria-label="搜索文章" class="search-input" placeholder="搜索标题、正文或摘要"/><select v-model="selectedSource" aria-label="筛选公众号"><option value="">全部公众号</option><option v-for="s in bootstrap.sources" :key="s.id" :value="s.id">{{ s.name }}</option></select><label class="check"><input v-model="unreadOnly" type="checkbox"/>只看未读</label></div>
        <p v-if="articlesLoading" class="list-status" role="status">正在搜索文章…</p>
        <p v-if="articlesFailed" class="list-status" role="alert">文章加载失败。<button class="text-button" @click="loadArticles">重试</button></p>
        <div v-if="articleItems.length && !articlesFailed" class="article-list" :aria-busy="articlesLoading">
          <button v-for="item in articleItems" :key="item.id" class="article-row" @click="openArticle(item)">
            <span class="source-avatar">{{ sourceInitial(item.source_name) }}</span><span class="article-main"><span class="article-meta">{{ item.source_name }} · {{ formatTime(item.published_at) }}</span><strong>{{ item.title }}</strong><span>{{ item.takeaway || item.summary || '正在整理内容…' }}</span><span class="tag-line"><i v-for="tag in item.tags" :key="tag">{{ tag }}</i></span></span><span v-if="!item.is_read" class="unread-dot"></span>
          </button>
        </div>
        <div v-else-if="!articlesLoading && !articlesFailed" class="empty-state"><h3>没有符合条件的文章</h3><p>调整筛选条件，或者刷新关注的公众号。</p></div>
        <nav v-if="articleItems.length || articlePage" class="pagination" aria-label="文章分页"><button class="secondary-button small" :disabled="articlesLoading || articlePage === 0" @click="articlePage--">上一页</button><span>第 {{ articlePage + 1 }} 页</span><button class="secondary-button small" :disabled="articlesLoading || !hasMore" @click="articlePage++">下一页</button></nav>
      </section>

      <section v-else-if="page === 'briefings'" class="page briefing-page">
        <header class="page-header"><div><p class="eyebrow">阅读归档</p><h1>每日简报</h1><p>每天的重要内容和主题都保存在这里。</p></div><button class="secondary-button" :disabled="busy || loading" @click="generateBriefing()">生成今日简报</button></header>
        <div v-if="bootstrap.briefings.length" class="briefing-layout">
          <aside class="briefing-index">
            <button v-for="item in bootstrap.briefings" :key="item.day" :class="{ active: currentBriefing?.day === item.day }" @click="selectedBriefingDay = item.day">
              <strong>{{ item.day }}</strong><span>{{ item.article_count }} 篇文章</span>
            </button>
          </aside>
          <article v-if="currentBriefing" class="briefing-document">
            <div class="briefing-actions"><button class="text-button" :disabled="busy || loading" @click="generateBriefing(currentBriefing.day)">重新生成</button><button class="text-button" @click="openBriefingFolder">打开文件目录</button></div>
            <div class="briefing-markdown" @click="openBriefingLink" v-html="renderedBriefing"></div>
          </article>
        </div>
        <div v-else class="empty-state"><h3>还没有每日简报</h3><p>到设定时间后，应用会自动整理当天的文章。</p><button class="secondary-button" :disabled="busy || loading" @click="generateBriefing()">生成今日简报</button></div>
      </section>

      <section v-else class="page settings-page">
        <header class="page-header"><div><p class="eyebrow">偏好设置</p><h1>设置</h1><p>调整内容更新、摘要和每日简报。</p></div><button class="primary-button" :disabled="busy || loading || loadFailed" @click="saveSettings">保存设置</button></header>
        <div class="settings-section"><div class="settings-title"><h2>智能摘要</h2><p>使用你自己的内容整理服务。</p></div><div class="settings-fields"><label>服务地址<input v-model="settingsDraft.ai.base_url" placeholder="https://api.example.com/v1"/></label><label>密钥<input v-model="aiKey" type="password" :placeholder="settingsDraft.ai.has_api_key ? '已保存，如需更换请重新输入' : '请输入密钥'"/></label><label>模型名称<input v-model="settingsDraft.ai.model" placeholder="例如 gpt-4o-mini"/></label><div class="inline-setting"><label class="switch-label"><input v-model="settingsDraft.auto_summary" type="checkbox"/>自动整理新文章</label><button class="secondary-button small" :disabled="busy || loading" @click="testAi">测试连接</button></div></div></div>
        <div class="settings-section"><div class="settings-title"><h2>自动刷新</h2><p>应用在托盘运行时会按这个频率检查新内容。</p></div><div class="settings-fields"><label>刷新频率<select v-model.number="settingsDraft.refresh_minutes"><option :value="15">每 15 分钟</option><option :value="30">每 30 分钟</option><option :value="60">每 1 小时</option><option :value="120">每 2 小时</option><option :value="240">每 4 小时</option><option :value="0">仅手动刷新</option></select></label></div></div>
        <div class="settings-section"><div class="settings-title"><h2>每日简报</h2><p>在你习惯的时间整理当天内容。</p></div><div class="settings-fields"><label class="switch-label"><input v-model="settingsDraft.briefing_enabled" type="checkbox"/>每天自动生成简报</label><label>生成时间<input v-model="settingsDraft.briefing_time" type="time" :disabled="!settingsDraft.briefing_enabled"/></label></div></div>
        <div class="settings-section"><div class="settings-title"><h2>应用设置</h2><p>控制应用如何在电脑上运行。</p></div><div class="settings-fields"><label class="switch-label"><input v-model="settingsDraft.autostart" type="checkbox"/>登录 Windows 后自动启动</label><label>外观<select v-model="settingsDraft.theme"><option value="light">浅色</option><option value="dark">深色</option><option value="system">跟随系统</option></select></label><button class="secondary-button small update-button" :disabled="busy" @click="checkForUpdates()">检查更新</button></div></div>
      </section>
    </main>

    <ArticleDrawer v-if="article" :key="article.id" :article="article" :busy="busy" @close="closeArticle" @retry="retrySummary" @original="openOriginal" />
  </div>
</template>
