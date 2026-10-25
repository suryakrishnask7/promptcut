/**
 * state.js — Shared application state + minimal event bus.
 *
 * WHY: A single source of truth prevents the three panels from diverging.
 * Components subscribe to events instead of polling or holding private copies.
 * No framework needed; the event bus is ~20 lines.
 */

// ── Event bus ────────────────────────────────────────────────────────────────

/** @type {Record<string, Function[]>} */
const _listeners = {};

/** Subscribe to an event. Returns an unsubscribe function. */
export function on(event, fn) {
  (_listeners[event] ||= []).push(fn);
  return () => {
    _listeners[event] = (_listeners[event] || []).filter(f => f !== fn);
  };
}

/** Emit an event to all subscribers. */
export function emit(event, ...args) {
  for (const fn of (_listeners[event] || [])) {
    try { fn(...args); } catch (err) { console.error(`[bus:${event}]`, err); }
  }
}

// ── Application state ────────────────────────────────────────────────────────

export const state = {
  /** @type {string|null} Active session ID (8-char UUID prefix) */
  sessionId: null,

  /** @type {string|null} Human-readable name (filename or "Sample") */
  videoTitle: null,

  /** @type {object|null} Full transcript API response */
  transcript: null,

  /** @type {object|null} Full timeline API response */
  timeline: null,

  /** @type {object|null} Full log API response */
  log: null,

  /** 'preview' | 'original' — which video src is loaded */
  videoMode: /** @type {'preview'|'original'} */ ('preview'),

  /** 'auto' | 'llm_only' | 'rule_only' */
  interpreterMode: 'auto',

  /** Whether the chat composer is waiting for a reply */
  isSending: false,

  /** Whether a transcription / render job is in progress */
  isJobRunning: false,

  /** URL of the rendered mp4 (after render completes) */
  renderUrl: null,

  /** The last instruction response object, for context */
  lastResponse: null,

  // ── Computed helpers ──────────────────────────────────────────────────────

  get canUndo() { return this.log?.can_undo ?? false; },
  get canRedo() { return this.log?.can_redo ?? false; },
  get isReady() { return !!this.sessionId && !!this.transcript && !!this.timeline; },
};

/**
 * Merge a patch into state and emit 'statechange'.
 * @param {Partial<typeof state>} patch
 */
export function setState(patch) {
  Object.assign(state, patch);
  emit('statechange', state);
}
