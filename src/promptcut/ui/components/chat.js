/**
 * components/chat.js — Conversational editing interface.
 *
 * Responsibilities:
 * - Renders user prompt messages and rich system response cards:
 *   - Applied: green badge, summary, duration delta, affected words, inline Undo button.
 *   - Clarify: amber badge, question, clickable suggestion pills, follow-up composer.
 *   - Rejected: red badge, reason, suggested rephrasing.
 * - Displays `interpreted_by` pill (🤖 LLM, ⚡ Rule, 💾 Cache).
 * - Manages composer textarea: auto-resize, Enter to send, Shift+Enter for newline.
 * - Interpreter mode selector (auto / llm_only / rule_only).
 */

import { state, setState, on, emit } from '../state.js';
import * as api from '../api.js';

let messagesContainer = null;
let chatInput = null;
let sendBtn = null;
let modeSelect = null;

// Local message history list
// { id, role: 'user'|'system', text, response?: object, timestamp: Date }
const messageHistory = [];

/**
 * Initialize the Chat component on DOM elements.
 * @param {HTMLElement} root
 */
export function initChat(root) {
  root.innerHTML = `
    <div class="flex flex-col h-full bg-zinc-950">
      <!-- Chat Header -->
      <div class="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/80 backdrop-blur shrink-0">
        <div class="flex items-center gap-2">
          <svg class="w-4 h-4 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
          </svg>
          <span class="text-xs font-semibold uppercase tracking-wider text-zinc-300">Instructions</span>
        </div>

        <!-- Interpreter Mode Selector -->
        <div class="flex items-center gap-1.5" title="Switch how instructions are interpreted">
          <label for="interpreterModeSelect" class="text-[11px] text-zinc-400">Mode:</label>
          <select id="interpreterModeSelect" class="text-xs bg-zinc-800 text-zinc-300 rounded px-2 py-1 border border-zinc-700 focus:outline-none focus:ring-1 focus:ring-indigo-500">
            <option value="auto">Auto (Smart)</option>
            <option value="llm_only">LLM Only</option>
            <option value="rule_only">Rule Only</option>
          </select>
        </div>
      </div>

      <!-- Messages Scroll Area -->
      <div id="chatMessages" class="flex-1 overflow-y-auto p-4 space-y-4" aria-live="polite">
        <!-- Rendered dynamically -->
      </div>

      <!-- Chat Composer -->
      <div class="p-3 border-t border-zinc-800 bg-zinc-900/60 shrink-0">
        <form id="chatForm" class="flex flex-col gap-2">
          <div class="relative flex items-end">
            <textarea
              id="chatInput"
              rows="1"
              placeholder="e.g. 'Cut out um and uh' or 'Bleep the swear words' (Press / to focus)"
              class="w-full resize-none rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 pr-10 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 max-h-32 transition-all"
              disabled
            ></textarea>

            <button
              id="chatSendBtn"
              type="submit"
              class="absolute right-1.5 bottom-1.5 p-1.5 rounded-md bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 disabled:hover:bg-indigo-600 transition-colors"
              title="Send instruction (Enter)"
              disabled
              aria-label="Send instruction"
            >
              <svg id="sendIcon" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/>
              </svg>
              <svg id="sendSpinner" class="w-4 h-4 animate-spin hidden" viewBox="0 0 24 24" fill="none">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            </button>
          </div>
          <div class="flex items-center justify-between text-[11px] text-zinc-400 px-1">
            <span>Press <kbd class="px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-800 text-[10px] font-mono">Enter</kbd> to send, <kbd class="px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-800 text-[10px] font-mono">Shift+Enter</kbd> for newline</span>
            <span id="charCount" class="font-mono"></span>
          </div>
        </form>
      </div>
    </div>
  `;

  messagesContainer = root.querySelector('#chatMessages');
  chatInput = root.querySelector('#chatInput');
  sendBtn = root.querySelector('#chatSendBtn');
  modeSelect = root.querySelector('#interpreterModeSelect');

  // Event handlers
  root.querySelector('#chatForm').addEventListener('submit', (e) => {
    e.preventDefault();
    handleSend();
  });

  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  // Auto-resize textarea
  chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 128)}px`;
    updateCharCount();
  });

  // Mode change
  modeSelect.addEventListener('change', async (e) => {
    const newMode = e.target.value;
    setState({ interpreterMode: newMode });
    if (state.sessionId) {
      try {
        await api.setInterpreterMode(state.sessionId, newMode);
        emit('toast', { text: `Interpreter mode set to: ${newMode}`, type: 'info' });
      } catch (err) {
        console.error('Failed to sync interpreter mode:', err);
      }
    }
  });

  // Global key "/" to focus
  window.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== chatInput && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
      e.preventDefault();
      chatInput.focus();
    }
  });

  // Listen for state changes
  on('statechange', render);

  // Initial render
  render();
}

/** Auto scroll messages container to bottom */
function scrollToBottom() {
  if (messagesContainer) {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

/** Update char count */
function updateCharCount() {
  const countEl = document.getElementById('charCount');
  if (countEl && chatInput) {
    const len = chatInput.value.length;
    countEl.textContent = len > 0 ? `${len} chars` : '';
  }
}

/**
 * Handle sending user instruction.
 * @param {string} [instructionOverride]
 * @param {object} [contextOverride]
 */
export async function handleSend(instructionOverride = null, contextOverride = null) {
  const text = (instructionOverride || chatInput.value).trim();
  if (!text || state.isSending || !state.sessionId) return;

  if (!instructionOverride) {
    chatInput.value = '';
    chatInput.style.height = 'auto';
    updateCharCount();
  }

  // Push user message
  messageHistory.push({
    id: `msg-${Date.now()}`,
    role: 'user',
    text,
    timestamp: new Date(),
  });

  setState({ isSending: true });
  renderMessages();
  scrollToBottom();

  try {
    const response = await api.sendInstruction(
      state.sessionId,
      text,
      contextOverride || state.lastResponse?.clarification_context || null,
      state.interpreterMode
    );

    // Save response
    messageHistory.push({
      id: `res-${Date.now()}`,
      role: 'system',
      response,
      timestamp: new Date(),
    });

    setState({ lastResponse: response });

    // Refresh timeline, transcript, and log if applied
    if (response.type === 'applied' || response.status === 'applied') {
      const [tl, tr, lg] = await Promise.all([
        api.getTimeline(state.sessionId),
        api.getTranscript(state.sessionId),
        api.getLog(state.sessionId),
      ]);
      setState({ timeline: tl, transcript: tr, log: lg });
      emit('toast', { text: `Applied: ${response.message || response.summary || 'Operation succeeded'}`, type: 'success' });
      emit('video_refresh');
    }
  } catch (err) {
    messageHistory.push({
      id: `err-${Date.now()}`,
      role: 'system',
      response: {
        type: 'rejected',
        reason: err.message || 'Unknown network error',
        interpreted_by: 'system',
      },
      timestamp: new Date(),
    });
    emit('toast', { text: `Failed: ${err.message}`, type: 'error' });
  } finally {
    setState({ isSending: false });
    renderMessages();
    scrollToBottom();
    chatInput?.focus();
  }
}

/**
 * Render the whole chat panel state.
 */
function render() {
  const isEnabled = !!state.sessionId && !state.isSending;

  if (chatInput) {
    chatInput.disabled = !isEnabled;
    if (isEnabled) {
      chatInput.placeholder = "e.g. 'Cut silence', 'Bleep wonder', 'Remove fillers' (Press Enter to send)...";
    } else {
      chatInput.placeholder = 'Upload a video to start editing with AI prompts...';
    }
  }

  if (sendBtn) {
    sendBtn.disabled = !isEnabled || state.isSending;
    const sendIcon = document.getElementById('sendIcon');
    const sendSpinner = document.getElementById('sendSpinner');
    if (sendIcon && sendSpinner) {
      if (state.isSending) {
        sendIcon.classList.add('hidden');
        sendSpinner.classList.remove('hidden');
      } else {
        sendIcon.classList.remove('hidden');
        sendSpinner.classList.add('hidden');
      }
    }
  }

  if (modeSelect && modeSelect.value !== state.interpreterMode) {
    modeSelect.value = state.interpreterMode;
  }

  renderMessages();
}

/**
 * Render the message history list.
 */
function renderMessages() {
  if (!messagesContainer) return;

  if (messageHistory.length === 0) {
    messagesContainer.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center text-center p-6 text-zinc-400 dark:text-zinc-500">
        <div class="w-12 h-12 rounded-full bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center mb-3">
          <svg class="w-6 h-6 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>
          </svg>
        </div>
        <p class="text-sm font-medium text-zinc-700 dark:text-zinc-300">Prompt-Driven Video Editor</p>
        <p class="text-xs mt-1 max-w-xs">Type what you want to edit in natural language, or try one of the quick suggestions below.</p>

        <div class="mt-4 flex flex-wrap gap-1.5 justify-center max-w-xs">
          <button class="sample-prompt text-xs px-2.5 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 transition-colors border border-zinc-200 dark:border-zinc-700" data-prompt="remove filler words like um and uh">
            ✂️ Remove fillers
          </button>
          <button class="sample-prompt text-xs px-2.5 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 transition-colors border border-zinc-200 dark:border-zinc-700" data-prompt="bleep swear words">
            🤫 Bleep profanity
          </button>
          <button class="sample-prompt text-xs px-2.5 py-1 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 transition-colors border border-zinc-200 dark:border-zinc-700" data-prompt="add caption 'Pricing Breakdown' from 0:02 to 0:05">
            📝 Add caption
          </button>
        </div>
      </div>
    `;

    // Attach listeners to suggestion chips
    messagesContainer.querySelectorAll('.sample-prompt').forEach(btn => {
      btn.addEventListener('click', () => {
        const prompt = btn.getAttribute('data-prompt');
        if (chatInput && state.sessionId) {
          chatInput.value = prompt;
          handleSend();
        }
      });
    });
    return;
  }

  messagesContainer.innerHTML = '';
  for (const msg of messageHistory) {
    if (msg.role === 'user') {
      const el = document.createElement('div');
      el.className = 'flex justify-end';
      el.innerHTML = `
        <div class="max-w-[85%] rounded-2xl rounded-tr-sm bg-indigo-600 text-white px-3.5 py-2 text-sm shadow-sm select-text cursor-text">
          <p class="whitespace-pre-wrap leading-relaxed select-text">${escapeHtml(msg.text)}</p>
          <div class="mt-1 text-[10px] text-indigo-200 text-right font-mono">${formatTime(msg.timestamp)}</div>
        </div>
      `;
      messagesContainer.appendChild(el);
    } else {
      const card = createResponseCard(msg.response, msg.timestamp);
      messagesContainer.appendChild(card);
    }
  }
}

/**
 * Build rich system response card.
 * @param {object} res
 * @param {Date} timestamp
 */
function createResponseCard(res, timestamp) {
  const container = document.createElement('div');
  container.className = 'flex justify-start select-text';

  const type = res.type || res.status || 'applied';
  const interpretedBy = res.interpreted_by || 'llm';

  // Badge configuration
  let badgeIcon = '🤖';
  let badgeLabel = 'LLM';
  let badgeColor = 'bg-violet-100 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300 border-violet-200 dark:border-violet-800';
  let badgeTooltip = 'Interpreted via Groq LLM model';

  if (interpretedBy === 'rule') {
    badgeIcon = '⚡';
    badgeLabel = 'Rule';
    badgeColor = 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800';
    badgeTooltip = 'Resolved instantly via deterministic rule engine';
  } else if (interpretedBy === 'cache') {
    badgeIcon = '💾';
    badgeLabel = 'Cache';
    badgeColor = 'bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300 border-blue-200 dark:border-blue-800';
    badgeTooltip = 'Loaded from local deterministic hash cache';
  }

  const badgeHtml = `
    <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold font-mono border ${badgeColor}" title="${badgeTooltip}">
      <span>${badgeIcon}</span>
      <span>${badgeLabel}</span>
    </span>
  `;

  // 1. APPLIED CARD
  if (type === 'applied') {
    const deltaText = res.duration_delta != null ? `${res.duration_delta > 0 ? '+' : ''}${res.duration_delta.toFixed(2)}s` : '';
    const beforeDur = res.duration_before != null ? `${res.duration_before.toFixed(1)}s` : '';
    const afterDur = res.duration_after != null ? `${res.duration_after.toFixed(1)}s` : '';

    let wordsHtml = '';
    if (res.affected_words && res.affected_words.length > 0) {
      const chips = res.affected_words.slice(0, 8).map(w =>
        `<span class="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[11px] font-mono text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700">"${escapeHtml(w)}"</span>`
      ).join('');
      const more = res.affected_words.length > 8 ? `<span class="text-[11px] text-zinc-400 self-center">+${res.affected_words.length - 8} more</span>` : '';
      wordsHtml = `<div class="mt-2 flex flex-wrap gap-1 items-center">${chips}${more}</div>`;
    }

    container.innerHTML = `
      <div class="max-w-[90%] rounded-xl border border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/40 dark:bg-emerald-950/20 p-3 shadow-sm text-sm">
        <div class="flex items-center justify-between gap-2 mb-1.5">
          <div class="flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
            <span class="font-semibold text-emerald-800 dark:text-emerald-300 text-xs uppercase tracking-wide">Applied</span>
          </div>
          <div class="flex items-center gap-1.5">
            ${badgeHtml}
            <span class="text-[10px] text-zinc-400 font-mono">${formatTime(timestamp)}</span>
          </div>
        </div>

        <p class="text-zinc-800 dark:text-zinc-200 font-medium">${escapeHtml(res.message || res.summary || 'Operation executed successfully.')}</p>

        ${(beforeDur && afterDur) ? `
          <div class="mt-2 flex items-center gap-2 text-xs font-mono text-zinc-600 dark:text-zinc-400 bg-white/60 dark:bg-zinc-900/40 px-2 py-1 rounded border border-emerald-100 dark:border-emerald-950">
            <span>Duration:</span>
            <span>${beforeDur} → ${afterDur}</span>
            <span class="font-bold ${res.duration_delta < 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-zinc-600'}">(${deltaText})</span>
          </div>
        ` : ''}

        ${wordsHtml}

        <div class="mt-3 flex items-center justify-between pt-2 border-t border-emerald-100 dark:border-emerald-950 text-xs">
          <span class="text-[11px] text-zinc-400">Policy: ${escapeHtml(res.time_policy || 'current')}</span>
          <button class="inline-undo-btn text-xs font-medium text-emerald-700 dark:text-emerald-400 hover:text-emerald-800 hover:underline flex items-center gap-1">
            <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a5 5 0 015 5v2M3 10l6 6m-6-6l6-6"/>
            </svg>
            Undo
          </button>
        </div>
      </div>
    `;

    container.querySelector('.inline-undo-btn')?.addEventListener('click', async () => {
      try {
        await api.undo(state.sessionId);
        const [tl, tr, lg] = await Promise.all([
          api.getTimeline(state.sessionId),
          api.getTranscript(state.sessionId),
          api.getLog(state.sessionId),
        ]);
        setState({ timeline: tl, transcript: tr, log: lg });
        emit('toast', { text: 'Undid last operation', type: 'info' });
        emit('video_refresh');
      } catch (err) {
        emit('toast', { text: `Undo failed: ${err.message}`, type: 'error' });
      }
    });

    return container;
  }

  // 2. CLARIFY CARD
  if (type === 'clarify' || type === 'needs_clarification') {
    let optionsHtml = '';
    const choices = res.options || res.choices || [];
    if (choices.length > 0) {
      const pills = choices.map((opt, i) =>
        `<button class="clarify-choice-btn text-xs text-left px-2.5 py-1.5 rounded-lg bg-white dark:bg-zinc-900 border border-amber-200 dark:border-amber-800/60 text-amber-900 dark:text-amber-200 hover:bg-amber-100/60 dark:hover:bg-amber-900/40 transition-colors shadow-2xs" data-choice="${escapeHtml(typeof opt === 'string' ? opt : opt.text || opt.label || '')}">
          ${escapeHtml(typeof opt === 'string' ? opt : opt.text || opt.label || '')}
        </button>`
      ).join('');
      optionsHtml = `<div class="mt-2.5 flex flex-col gap-1.5">${pills}</div>`;
    }

    container.innerHTML = `
      <div class="max-w-[90%] rounded-xl border border-amber-300 dark:border-amber-900/60 bg-amber-50/50 dark:bg-amber-950/20 p-3 shadow-sm text-sm">
        <div class="flex items-center justify-between gap-2 mb-1.5">
          <div class="flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></span>
            <span class="font-semibold text-amber-800 dark:text-amber-300 text-xs uppercase tracking-wide">Needs Clarification</span>
          </div>
          <div class="flex items-center gap-1.5">
            ${badgeHtml}
            <span class="text-[10px] text-zinc-400 font-mono">${formatTime(timestamp)}</span>
          </div>
        </div>

        <p class="text-zinc-800 dark:text-zinc-200 font-medium">${escapeHtml(res.question || res.message || 'Please clarify your instruction.')}</p>

        ${optionsHtml}

        <div class="mt-3 flex items-center gap-1.5 pt-2 border-t border-amber-200/60 dark:border-amber-950">
          <input
            type="text"
            placeholder="Type your response or click an option above..."
            class="clarify-reply-input flex-1 text-xs px-2.5 py-1.5 rounded bg-white dark:bg-zinc-900 border border-amber-200 dark:border-amber-800 text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:ring-1 focus:ring-amber-500"
          />
          <button class="clarify-reply-btn px-2.5 py-1.5 text-xs font-semibold rounded bg-amber-600 hover:bg-amber-500 text-white transition-colors">
            Reply
          </button>
        </div>
      </div>
    `;

    // Click choice pill
    container.querySelectorAll('.clarify-choice-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const choice = btn.getAttribute('data-choice');
        handleSend(choice, res);
      });
    });

    // Custom input reply
    const input = container.querySelector('.clarify-reply-input');
    const sendReplyBtn = container.querySelector('.clarify-reply-btn');
    const doReply = () => {
      const val = input.value.trim();
      if (val) {
        handleSend(val, res);
      }
    };
    sendReplyBtn?.addEventListener('click', doReply);
    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') doReply();
    });

    return container;
  }

  // 3. REJECTED CARD
  const reason = res.reason || res.message || 'Instruction could not be performed.';
  const suggestion = res.suggestion || res.rephrasing || null;

  container.innerHTML = `
    <div class="max-w-[90%] rounded-xl border border-rose-300 dark:border-rose-900/60 bg-rose-50/40 dark:bg-rose-950/20 p-3 shadow-sm text-sm">
      <div class="flex items-center justify-between gap-2 mb-1.5">
        <div class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-rose-500"></span>
          <span class="font-semibold text-rose-800 dark:text-rose-300 text-xs uppercase tracking-wide">Rejected</span>
        </div>
        <div class="flex items-center gap-1.5">
          ${badgeHtml}
          <span class="text-[10px] text-zinc-400 font-mono">${formatTime(timestamp)}</span>
        </div>
      </div>

      <p class="text-zinc-800 dark:text-zinc-200 text-xs font-medium leading-relaxed">${escapeHtml(reason)}</p>

      ${suggestion ? `
        <div class="mt-2.5 p-2 rounded-lg bg-white/70 dark:bg-zinc-900/60 border border-rose-100 dark:border-rose-950 text-xs">
          <span class="text-[10px] uppercase font-bold text-zinc-400 tracking-wider">Suggested Rephrasing:</span>
          <p class="mt-0.5 text-zinc-700 dark:text-zinc-300 italic font-mono text-[11px]">"${escapeHtml(suggestion)}"</p>
          <button class="try-suggestion-btn mt-1 text-[11px] font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
            Try this instruction →
          </button>
        </div>
      ` : ''}

      <div class="mt-2 text-[10px] text-zinc-400 font-mono text-right">
        Policy: ${escapeHtml(res.time_policy || 'current')}
      </div>
    </div>
  `;

  if (suggestion) {
    container.querySelector('.try-suggestion-btn')?.addEventListener('click', () => {
      handleSend(suggestion);
    });
  }

  return container;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str ?? '');
  return str.replace(/[&<>'"]/g, tag => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  }[tag] || tag));
}

function formatTime(d) {
  if (!(d instanceof Date)) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
