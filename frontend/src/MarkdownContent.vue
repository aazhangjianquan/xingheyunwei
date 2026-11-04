<script setup>
import { computed } from 'vue'
import { renderMarkdown } from './markdown'

const props = defineProps({ content: { type: String, default: '' } })
const html = computed(() => renderMarkdown(props.content))
</script>

<template>
  <div class="markdown-content" v-html="html"></div>
</template>

<style scoped>
.markdown-content{min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere;word-break:normal;font-size:14px;line-height:1.85;color:#41516a}
.markdown-content :deep(> :first-child){margin-top:0}
.markdown-content :deep(> :last-child){margin-bottom:0}
.markdown-content :deep(h1),.markdown-content :deep(h2),.markdown-content :deep(h3),.markdown-content :deep(h4),.markdown-content :deep(h5),.markdown-content :deep(h6){color:#273851;font-weight:650;line-height:1.5;overflow-wrap:anywhere;margin:1.55em 0 .65em}
.markdown-content :deep(h1){font-size:1.8em}.markdown-content :deep(h2){font-size:1.5em}.markdown-content :deep(h3){font-size:1.25em}.markdown-content :deep(h4),.markdown-content :deep(h5),.markdown-content :deep(h6){font-size:1.05em}
.markdown-content :deep(p){margin:.8em 0}
.markdown-content :deep(strong){color:#2c3c55;font-weight:650}
.markdown-content :deep(ul),.markdown-content :deep(ol){margin:.8em 0;padding-left:1.8em;list-style-position:outside}
.markdown-content :deep(ul){list-style-type:disc}.markdown-content :deep(ol){list-style-type:decimal}
.markdown-content :deep(li){display:list-item;margin:.3em 0;padding-left:.15em}
.markdown-content :deep(li > p){margin:.35em 0}
.markdown-content :deep(li > ul),.markdown-content :deep(li > ol){margin:.25em 0}
.markdown-content :deep(blockquote){margin:1em 0;padding:.15em 1em;border-left:3px solid #c5d2ec;color:#687b96;background:#f8faff}
.markdown-content :deep(a){color:#4265c5;text-decoration:underline;text-underline-offset:3px;overflow-wrap:anywhere}
.markdown-content :deep(a:hover){color:#28489f}
.markdown-content :deep(code){font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.9em;background:#f0f3f8;border-radius:4px;padding:.15em .35em;color:#354b70;overflow-wrap:anywhere}
.markdown-content :deep(pre){max-width:100%;margin:1em 0;padding:14px 16px;overflow-x:auto;border:1px solid #e1e7f0;border-radius:8px;background:#f6f8fc;white-space:pre;word-break:normal;overflow-wrap:normal;line-height:1.65;tab-size:4}
.markdown-content :deep(pre code){padding:0;background:transparent;border-radius:0;white-space:pre;word-break:normal;overflow-wrap:normal;color:inherit}
.markdown-content :deep(.markdown-table-scroll){max-width:100%;overflow-x:auto;margin:1em 0;border:1px solid #dfe6f0;border-radius:8px;overscroll-behavior-x:contain}
.markdown-content :deep(.markdown-table-scroll:focus-visible){outline:2px solid #829bdd;outline-offset:2px}
.markdown-content :deep(table){width:100%;min-width:360px;border-collapse:collapse;font-size:.92em;line-height:1.65}
.markdown-content :deep(th),.markdown-content :deep(td){min-width:90px;max-width:34em;padding:10px 13px;text-align:left;vertical-align:top;border-right:1px solid #e2e8f1;border-bottom:1px solid #e2e8f1;overflow-wrap:anywhere}
.markdown-content :deep(th){background:#f1f5fb;color:#354967;font-weight:650}
.markdown-content :deep(th:last-child),.markdown-content :deep(td:last-child){border-right:0}
.markdown-content :deep(tr:last-child td){border-bottom:0}
.markdown-content :deep(tbody tr:nth-child(even)){background:#fafbfe}
.markdown-content :deep(hr){margin:1.5em 0;border:0;border-top:1px solid #e2e8f0}
.markdown-content :deep(img){display:block;max-width:100%;height:auto;margin:1em 0;border-radius:6px}
@media(max-width:700px){.markdown-content{font-size:13px;line-height:1.8}.markdown-content :deep(h1){font-size:1.55em}.markdown-content :deep(h2){font-size:1.35em}.markdown-content :deep(pre){padding:12px}.markdown-content :deep(th),.markdown-content :deep(td){padding:9px 11px}}
</style>
