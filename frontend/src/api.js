// Copyright (c) 2026, Sanjay Kumar and contributors
// For license information, please see license.txt
/**
 * API Utility Module
 * Handles all API calls to Frappe backend
 */

const API_BASE = '/api/method/ai_chatbot.api.chat'
const FILES_API_BASE = '/api/method/ai_chatbot.api.files'
const EXPORT_API_BASE = '/api/method/ai_chatbot.api.export'
const CRUD_API_BASE = '/api/method/ai_chatbot.api.crud'
const FOUNDRY_API_BASE = '/api/method/ai_chatbot.api.foundry_agents'

class ChatAPI {
  constructor() {
    this.csrfToken = null
    this.initializeToken()
  }

  /**
   * Initialize CSRF token from Frappe
   */
  async initializeToken() {
    try {
      // Try to get from window first (if loaded in Frappe)
      if (window.csrf_token) {
        this.csrfToken = window.csrf_token
        return
      }

      // Otherwise fetch from Frappe
      const response = await fetch('/api/method/frappe.auth.get_logged_user', {
        credentials: 'include'
      })
      
      if (response.ok) {
        const data = await response.json()
        // Get CSRF token from cookies
        const cookies = document.cookie.split(';')
        for (let cookie of cookies) {
          const [name, value] = cookie.trim().split('=')
          if (name === 'csrf_token') {
            this.csrfToken = decodeURIComponent(value)
            break
          }
        }
      }
    } catch (error) {
      console.error('Failed to initialize CSRF token:', error)
    }
  }

  /**
   * Get CSRF token
   */
  async getToken() {
    if (!this.csrfToken) {
      await this.initializeToken()
    }
    return this.csrfToken
  }

  /**
   * Make API request to Frappe
   */
  async request(endpoint, data = {}) {
    try {
      const token = await this.getToken()
      
      const response = await fetch(`${API_BASE}.${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-Frappe-CSRF-Token': token || '',
        },
        credentials: 'include', // Important for session cookies
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        // Try to get error message from response
        const errorText = await response.text()
        console.error('API Error Response:', errorText)
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const result = await response.json()
      return result.message || result
    } catch (error) {
      console.error('API request error:', error)
      throw error
    }
  }

  /**
   * Create a new conversation
   */
  async createConversation(title, aiProvider = 'OpenAI', foundryAgent = null) {
    return this.request('create_conversation', {
      title,
      ai_provider: aiProvider,
      foundry_agent: foundryAgent,
    })
  }

  /**
   * Get all conversations for current user
   */
  async getConversations(limit = 20) {
    return this.request('get_conversations', { limit })
  }

  /**
   * Get messages for a conversation
   */
  async getConversationMessages(conversationId) {
    return this.request('get_conversation_messages', {
      conversation_id: conversationId,
    })
  }

  /**
   * Send a message and get AI response (non-streaming)
   */
  async sendMessage(conversationId, message, stream = false, attachments = null, { isRetry = false } = {}) {
    return this.request('send_message', {
      conversation_id: conversationId,
      message,
      stream,
      attachments: attachments ? JSON.stringify(attachments) : null,
      is_retry: isRetry,
    })
  }

  /**
   * Send a message with streaming enabled.
   * The actual response tokens arrive via Socket.IO realtime events.
   * This HTTP call returns immediately with a stream_id.
   */
  async sendMessageStreaming(conversationId, message, attachments = null, { isRetry = false } = {}) {
    return this.request('send_message', {
      conversation_id: conversationId,
      message,
      stream: true,
      attachments: attachments ? JSON.stringify(attachments) : null,
      is_retry: isRetry,
    })
  }

  /**
   * Upload a file for a chat conversation.
   * Uses multipart/form-data instead of JSON.
   */
  async uploadFile(conversationId, file) {
    const token = await this.getToken()
    const formData = new FormData()
    formData.append('file', file)
    formData.append('conversation_id', conversationId)

    const response = await fetch(`${FILES_API_BASE}.upload_chat_file`, {
      method: 'POST',
      headers: {
        'X-Frappe-CSRF-Token': token || '',
      },
      credentials: 'include',
      body: formData,
    })

    if (!response.ok) {
      throw new Error(`Upload failed: ${response.status}`)
    }

    const result = await response.json()
    return result.message || result
  }

  /**
   * Get values for @mention autocomplete.
   */
  async getMentionValues(mentionType, searchTerm = '', company = null) {
    return this.request('get_mention_values', {
      mention_type: mentionType,
      search_term: searchTerm,
      company,
    })
  }

  /**
   * Search conversations by title or message content
   */
  async searchConversations(query, limit = 20) {
    return this.request('search_conversations', { query, limit })
  }

  /**
   * Delete a conversation
   */
  async deleteConversation(conversationId) {
    return this.request('delete_conversation', {
      conversation_id: conversationId,
    })
  }

  /**
   * Update conversation title
   */
  async updateConversationTitle(conversationId, title) {
    return this.request('update_conversation_title', {
      conversation_id: conversationId,
      title,
    })
  }

  /**
   * Set conversation language preference
   */
  async setConversationLanguage(conversationId, language) {
    return this.request('set_conversation_language', {
      conversation_id: conversationId,
      language,
    })
  }

  /**
   * Get chatbot settings
   */
  async getSettings() {
    return this.request('get_settings')
  }

  /**
   * List enabled az-agent-* Foundry Agents (name/description/icon only —
   * never the underlying assistant id). Used by the agent picker when
   * ai_provider is "Azure AI Foundry Agent".
   */
  async getFoundryAgents() {
    return this.request('get_foundry_agents')
  }

  /**
   * Get sample prompts parsed from docs/SAMPLE_USER_PROMPT.md.
   * Returns { success, categories: [...], mentions: [...] }.
   */
  async getSamplePrompts() {
    return this.request('get_sample_prompts')
  }

  /**
   * Export a single assistant message as PDF.
   * Returns { success, file_url }.
   */
  async exportMessagePdf(messageName) {
    return this.requestEndpoint(EXPORT_API_BASE, 'export_message_pdf', {
      message_name: messageName,
    })
  }

  /**
   * Export an entire conversation as PDF.
   * Returns { success, file_url }.
   */
  async exportConversationPdf(conversationId) {
    return this.requestEndpoint(EXPORT_API_BASE, 'export_conversation_pdf', {
      conversation_id: conversationId,
    })
  }

  /**
   * Make API request to an arbitrary endpoint base.
   */
  async requestEndpoint(base, endpoint, data = {}) {
    try {
      const token = await this.getToken()

      const response = await fetch(`${base}.${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-Frappe-CSRF-Token': token || '',
        },
        credentials: 'include',
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        const errorText = await response.text()
        console.error('API Error Response:', errorText)
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const result = await response.json()
      return result.message || result
    } catch (error) {
      console.error('API request error:', error)
      throw error
    }
  }

  // ── Phase 13B: CRUD Confirmation API methods ──

  /**
   * Confirm a proposed CRUD action (user clicked "Save" / "Save Draft" / "Submit" / etc.).
   * @param {string} confirmationId - The confirmation UUID from the propose_* tool result
   * @param {string|null} userOverrides - JSON string with user-edited prerequisite fields
   * @param {boolean} submitAfterCreate - If true, submit the document after creating it
   * @returns {Promise<Object>} Result with success, name, doc_url, undo_token
   */
  async confirmAction(confirmationId, userOverrides = null, submitAfterCreate = false) {
    return this.requestEndpoint(CRUD_API_BASE, 'confirm_action', {
      confirmation_id: confirmationId,
      user_overrides: userOverrides,
      submit_after_create: submitAfterCreate,
    })
  }

  /**
   * Cancel a proposed CRUD action (user clicked "Cancel").
   * @param {string} confirmationId - The confirmation UUID
   * @returns {Promise<Object>} Result with success status
   */
  async cancelAction(confirmationId) {
    return this.requestEndpoint(CRUD_API_BASE, 'cancel_action', {
      confirmation_id: confirmationId,
    })
  }

  /**
   * Undo a recently confirmed CRUD action (within 5-minute window).
   * @param {string} undoToken - The undo token UUID from confirm_action result
   * @returns {Promise<Object>} Result with success status
   */
  async undoAction(undoToken) {
    return this.requestEndpoint(CRUD_API_BASE, 'undo_action', {
      undo_token: undoToken,
    })
  }

  // ── Link field search (for SearchableDropdown) ──

  /**
   * Search link field values using Frappe's built-in search_link.
   * Used by SearchableDropdown in the unified item mapping table.
   * @param {string} doctype - DocType to search (e.g. "Item", "UOM", "Item Group")
   * @param {string} txt - Search text
   * @param {number} pageLength - Max results (default 10)
   * @returns {Promise<Array<{value: string, description: string}>>}
   */
  async searchLinkValues(doctype, txt = '', pageLength = 10) {
    const token = await this.getToken()
    const response = await fetch('/api/method/frappe.desk.search.search_link', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Frappe-CSRF-Token': token || '',
      },
      credentials: 'include',
      body: JSON.stringify({ doctype, txt, page_length: pageLength }),
    })
    if (!response.ok) throw new Error(`Search failed: ${response.status}`)
    const result = await response.json()
    return result.message || result.results || []
  }

}

export const chatAPI = new ChatAPI()

/**
 * Chart rendering utilities
 */
export const ChartUtils = {
  /**
   * Create chart from data
   */
  createChart(type, data, options = {}) {
    // This will be used with Chart.js in components
    return {
      type,
      data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        ...options,
      },
    }
  },

  /**
   * Parse chart data from markdown code block
   */
  parseChartData(codeBlock) {
    try {
      return JSON.parse(codeBlock)
    } catch (error) {
      console.error('Error parsing chart data:', error)
      return null
    }
  },
}

/**
 * Table rendering utilities
 */
export const TableUtils = {
  /**
   * Convert markdown table to HTML
   */
  markdownTableToHTML(markdown) {
    const lines = markdown.trim().split('\n')
    if (lines.length < 2) return markdown

    const headers = lines[0].split('|').map(h => h.trim()).filter(Boolean)
    const rows = lines.slice(2).map(line =>
      line.split('|').map(cell => cell.trim()).filter(Boolean)
    )

    let html = '<table class="min-w-full border-collapse">'
    html += '<thead><tr>'
    headers.forEach(header => {
      html += `<th class="border border-gray-300 px-4 py-2 bg-gray-50 font-semibold text-left">${header}</th>`
    })
    html += '</tr></thead><tbody>'

    rows.forEach(row => {
      html += '<tr>'
      row.forEach(cell => {
        html += `<td class="border border-gray-300 px-4 py-2">${cell}</td>`
      })
      html += '</tr>'
    })

    html += '</tbody></table>'
    return html
  },
}

/**
 * Format utilities
 */
export const FormatUtils = {
  /**
   * Format number with commas
   */
  formatNumber(num) {
    return new Intl.NumberFormat('en-US').format(num)
  },

  /**
   * Format currency
   */
  formatCurrency(amount, currency = 'USD') {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
    }).format(amount)
  },

  /**
   * Format date
   */
  formatDate(dateString) {
    const date = new Date(dateString)
    return new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    }).format(date)
  },

  /**
   * Format relative time
   */
  formatRelativeTime(dateString) {
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now - date
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? 's' : ''} ago`
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`
    if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`
    
    return this.formatDate(dateString)
  },
}
