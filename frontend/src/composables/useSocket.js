// Copyright (c) 2026, Sanjay Kumar and contributors
// For license information, please see license.txt
/**
 * Socket.IO Client Composable
 *
 * Manages connection to Frappe's Socket.IO server from a standalone Vue app
 * (outside Frappe desk). Follows the same pattern as Frappe CRM.
 *
 * Connection details:
 * - socketio_port: provided by Frappe boot data (or VITE_SOCKETIO_PORT in local dev)
 * - site_name: injected into window by the server-rendered HTML template
 * - Auth: via session cookie (sid) with withCredentials: true
 *
 * Phase 13A: Adds browser online/offline detection (navigator.onLine) for
 * instant Wi-Fi disconnect detection.  Socket.IO's heartbeat-based disconnect
 * event can take 20-60s to fire; the browser's `offline` event fires instantly.
 */

import { ref } from 'vue'
import { Manager } from 'socket.io-client'
let socket = null
let manager = null
const isConnected = ref(false)
const connectionError = ref(null)
const reconnectFailed = ref(false)

/** Whether the browser has network connectivity (navigator.onLine). */
const isOnline = ref(typeof navigator !== 'undefined' ? navigator.onLine : true)

// --- Browser online/offline event listeners ---
// These fire instantly when Wi-Fi is toggled, unlike Socket.IO heartbeats.
let _onlineListenerRegistered = false

function _registerOnlineListeners() {
  if (_onlineListenerRegistered || typeof window === 'undefined') return
  _onlineListenerRegistered = true

  window.addEventListener('online', () => {
    console.debug('[AI Chatbot] Browser: online')
    isOnline.value = true
    // Always tear down and re-create the socket on network restore.
    // socket.connect() is unreliable after the Manager has exhausted its
    // reconnection attempts (reconnectFailed=true) or when the underlying
    // transport is in a broken state.  A fresh init is the safest path.
    if (socket) {
      console.debug('[AI Chatbot] Re-initializing socket after network restore')
      try { socket.disconnect() } catch { /* ignore */ }
      socket = null
      manager = null
      reconnectFailed.value = false
      connectionError.value = null
    }
    // Small delay to let the network stack stabilize before reconnecting
    setTimeout(() => initSocket(), 500)
  })

  window.addEventListener('offline', () => {
    console.debug('[AI Chatbot] Browser: offline')
    isOnline.value = false
    // Mark as disconnected immediately — don't wait for Socket.IO ping timeout.
    // The socket itself may still report `connected` for several seconds until
    // the transport layer detects the break, but we know the network is gone.
    isConnected.value = false
    connectionError.value = 'Network connection lost'
  })
}

// Register immediately at module load time (singleton).
_registerOnlineListeners()

/**
 * Initialize the Socket.IO connection (singleton).
 * Connects to Frappe's Socket.IO server using the site namespace.
 *
 * We use Manager + socket(namespace) instead of io(url/namespace) to avoid
 * a URL parsing bug in socket.io-client where the namespace path (e.g.
 * "/test.local") collides with the hostname ("test.local"), corrupting
 * the connection URL.
 */
function initSocket() {
  if (socket && socket.connected) {
    return socket
  }

  try {
    const host = window.location.hostname
    const siteName = window.site_name || window.frappe?.boot?.site_name || host
    const socketioPort = Number(
      window.frappe?.boot?.socketio_port || import.meta.env.VITE_SOCKETIO_PORT || 9000
    )
    const directSocket = import.meta.env.DEV || Boolean(window.dev_server)
    const baseUrl = directSocket
      ? `${window.location.protocol}//${host}:${socketioPort}`
      : window.location.origin

    console.debug(`[AI Chatbot] Socket.IO connecting to: ${baseUrl} (namespace: /${siteName})`)

    // Create a Manager for the base URL (no namespace in the URL)
    manager = new Manager(baseUrl, {
      withCredentials: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 10000,
    })

    // Connect to the site-specific namespace separately
    socket = manager.socket(`/${siteName}`)

    socket.on('connect', () => {
      isConnected.value = true
      connectionError.value = null
      reconnectFailed.value = false
      console.debug('[AI Chatbot] Socket.IO connected')
    })

    socket.on('disconnect', (reason) => {
      isConnected.value = false
      console.debug('[AI Chatbot] Socket.IO disconnected:', reason)
    })

    socket.on('connect_error', (error) => {
      isConnected.value = false
      connectionError.value = error.message
      console.warn('[AI Chatbot] Socket.IO connection error:', error.message)
    })

    // Manager-level reconnect lifecycle events
    manager.on('reconnect', () => {
      console.debug('[AI Chatbot] Socket.IO reconnected')
      isConnected.value = true
      reconnectFailed.value = false
      connectionError.value = null
    })

    manager.on('reconnect_attempt', (attempt) => {
      console.debug(`[AI Chatbot] Socket.IO reconnect attempt ${attempt}`)
    })

    manager.on('reconnect_failed', () => {
      console.warn('[AI Chatbot] Socket.IO reconnection failed after all attempts')
      reconnectFailed.value = true
      isConnected.value = false
      connectionError.value = 'Reconnection failed after multiple attempts'
    })

    return socket
  } catch (error) {
    connectionError.value = error.message
    console.error('[AI Chatbot] Failed to init Socket.IO:', error)
    return null
  }
}

/**
 * Get the current socket instance, initializing if needed.
 */
function getSocket() {
  if (!socket || !socket.connected) {
    initSocket()
  }
  return socket
}

/**
 * Subscribe to a realtime event.
 * @param {string} event - Event name (e.g., 'ai_chat_token')
 * @param {Function} callback - Event handler
 */
function on(event, callback) {
  if (socket) {
    socket.on(event, callback)
  }
}

/**
 * Unsubscribe from a realtime event.
 * @param {string} event - Event name
 * @param {Function} [callback] - Specific handler to remove (removes all if omitted)
 */
function off(event, callback) {
  if (socket) {
    if (callback) {
      socket.off(event, callback)
    } else {
      socket.off(event)
    }
  }
}

/**
 * Disconnect the socket.
 */
function disconnect() {
  if (socket) {
    socket.disconnect()
    socket = null
    manager = null
    isConnected.value = false
  }
}

/**
 * Vue composable for Socket.IO connection.
 */
export function useSocket() {
  return {
    isConnected,
    isOnline,
    connectionError,
    reconnectFailed,
    initSocket,
    getSocket,
    on,
    off,
    disconnect,
  }
}
