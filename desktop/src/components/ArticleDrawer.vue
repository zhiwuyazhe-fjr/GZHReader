<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { formatTime } from '../lib/dates'
import type { Article } from '../lib/types'

defineProps<{ article: Article; busy: boolean }>()
const emit = defineEmits<{ close: []; retry: []; original: [] }>()
const drawer = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''

onMounted(() => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  drawer.value?.focus()
})
onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  previousFocus?.focus()
})

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close') }
  if (event.key !== 'Tab') return
  const buttons = drawer.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], [tabindex="0"]')
  const first = buttons?.[0]
  const last = buttons?.[buttons.length - 1]
  if (!first || !last) { event.preventDefault(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer.value)) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === drawer.value)) {
    event.preventDefault(); first.focus()
  }
}
</script>

<template>
  <div class="drawer-backdrop" @click.self="emit('close')" @keydown="onKeydown">
    <aside ref="drawer" class="article-drawer" role="dialog" aria-modal="true" aria-labelledby="article-title" tabindex="-1">
      <button class="drawer-close" @click="emit('close')">关闭</button>
      <div class="article-detail-head">
        <span class="article-meta">{{ article.source_name }} · {{ formatTime(article.published_at) }}</span>
        <h1 id="article-title">{{ article.title }}</h1>
        <p v-if="article.takeaway" class="takeaway">{{ article.takeaway }}</p>
      </div>
      <section class="summary-section">
        <div class="section-heading"><h2>内容摘要</h2><button class="text-button" :disabled="busy" @click="emit('retry')">重新整理</button></div>
        <p>{{ article.summary || '正在整理内容…' }}</p>
        <ul v-if="article.key_points.length"><li v-for="point in article.key_points" :key="point">{{ point }}</li></ul>
        <div class="tag-line"><i v-for="tag in article.tags" :key="tag">{{ tag }}</i></div>
      </section>
      <section class="content-section"><h2>正文</h2><p>{{ article.content || '正文暂时无法获取，请打开微信原文阅读。' }}</p></section>
      <button class="primary-button full" :disabled="busy" @click="emit('original')">打开微信原文</button>
    </aside>
  </div>
</template>
