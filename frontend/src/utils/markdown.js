// Copyright (c) 2026, Sanjay Kumar and contributors
// For license information, please see license.txt
/**
 * Shared, safe markdown renderer.
 *
 * AI output is untrusted input. The renderer therefore sanitizes the HTML
 * produced by marked before it is passed to Vue v-html.
 */
import { marked } from 'marked'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('yaml', yaml)

marked.use({
  breaks: true,
  gfm: true,
})

const ALLOWED_TAGS = new Set([
  'A', 'P', 'BR', 'STRONG', 'EM', 'DEL', 'CODE', 'PRE', 'BLOCKQUOTE',
  'UL', 'OL', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
  'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'HR', 'SPAN', 'KBD',
])

const DROP_CONTENT_TAGS = new Set([
  'SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH',
  'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'OPTION', 'LINK', 'META',
])

const GLOBAL_SAFE_ATTRS = new Set(['class'])
const TAG_SAFE_ATTRS = {
  A: new Set(['href', 'title']),
  TH: new Set(['align']),
  TD: new Set(['align']),
}

function isSafeUrl(value) {
  const normalized = String(value || '').trim().replace(/[\u0000-\u001F\u007F\s]+/g, '')
  if (!normalized) return false
  if (normalized.startsWith('#') || normalized.startsWith('/') || normalized.startsWith('./') || normalized.startsWith('../')) {
    return true
  }
  return /^(https?:|mailto:|tel:)/i.test(normalized)
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * Sanitize marked output before Vue v-html renders it.
 * DOMParser parses inert HTML; no script is executed during sanitization.
 */
function sanitizeHtml(html) {
  if (typeof DOMParser === 'undefined') {
    // renderMarkdown is a browser utility. In a non-DOM environment fail closed.
    return escapeHtml(html)
  }

  const doc = new DOMParser().parseFromString(String(html), 'text/html')
  const elements = Array.from(doc.body.querySelectorAll('*'))

  for (const el of elements) {
    const tag = el.tagName

    if (DROP_CONTENT_TAGS.has(tag)) {
      el.remove()
      continue
    }

    if (!ALLOWED_TAGS.has(tag)) {
      el.replaceWith(...Array.from(el.childNodes))
      continue
    }

    const tagAttrs = TAG_SAFE_ATTRS[tag] || new Set()
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase()
      if (!GLOBAL_SAFE_ATTRS.has(name) && !tagAttrs.has(name)) {
        el.removeAttribute(attr.name)
      }
    }

    if (tag === 'A') {
      const href = el.getAttribute('href')
      if (!isSafeUrl(href)) {
        el.removeAttribute('href')
      }
      el.setAttribute('target', '_blank')
      el.setAttribute('rel', 'noopener noreferrer')
    }
  }

  return doc.body.innerHTML
}

/**
 * Render markdown into sanitized HTML.
 *
 * @param {string} content - Markdown text
 * @returns {string} sanitized HTML
 */
export function renderMarkdown(content) {
  let html = marked(String(content || ''))
  html = sanitizeHtml(html)

  // Syntax highlighting for fenced code blocks.
  html = html.replace(
    /<code class="language-([\w+-]+)">([\s\S]*?)<\/code>/g,
    (_match, lang, code) => {
      const decoded = code
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
      if (hljs.getLanguage(lang)) {
        const highlighted = hljs.highlight(decoded, { language: lang }).value
        return `<code class="hljs language-${lang}">${highlighted}</code>`
      }
      return `<code class="hljs">${escapeHtml(decoded)}</code>`
    }
  )

  // Auto-right-align numeric columns in tables.
  html = html.replace(/<table>([\s\S]*?)<\/table>/g, (_match, tableContent) => {
    return `<table>${autoAlignTableColumns(tableContent)}</table>`
  })

  return html
}

/**
 * Detect numeric columns in an HTML table and apply right-alignment
 * to both <th> and <td> cells in those columns.
 */
function autoAlignTableColumns(tableHtml) {
  const rows = []
  const tdRowRegex = /<tr>([\s\S]*?)<\/tr>/g
  let rowMatch
  while ((rowMatch = tdRowRegex.exec(tableHtml)) !== null) {
    const cells = []
    const cellRegex = /<td[^>]*>([\s\S]*?)<\/td>/g
    let cellMatch
    while ((cellMatch = cellRegex.exec(rowMatch[1])) !== null) {
      cells.push(cellMatch[1].trim())
    }
    if (cells.length > 0) rows.push(cells)
  }

  if (rows.length === 0) return tableHtml

  const numCols = rows[0].length
  const numericPattern = /^[₹$€£¥]?\s*-?\s*[\d,]+\.?\d*\s*%?$|^—$|^-$/
  const isNumeric = new Array(numCols).fill(false)
  for (let col = 1; col < numCols; col++) {
    const allNumeric = rows.every(row => {
      const val = (row[col] || '').replace(/<[^>]*>/g, '').trim()
      return val === '' || numericPattern.test(val)
    })
    if (allNumeric) isNumeric[col] = true
  }

  let colIdx = 0
  tableHtml = tableHtml.replace(/<th([^>]*)>/g, (match, attrs) => {
    const idx = colIdx++
    if (idx < numCols && isNumeric[idx]) {
      return `<th${attrs} style="text-align:right">`
    }
    return match
  })

  colIdx = 0
  tableHtml = tableHtml.replace(/<td([^>]*)>/g, (match, attrs) => {
    const idx = colIdx % numCols
    colIdx++
    if (isNumeric[idx]) {
      return `<td${attrs} style="text-align:right">`
    }
    return match
  })

  return tableHtml
}
