/**
 * components/log.js — Edit history and undo/redo panel.
 *
 * Responsibilities:
 * - Displays the chronological list of all applied operations.
 * - Shows sequence number, operation type badge, target word summary, and interpreted_by badge.
 * - Dims operations that are currently undone (future ops relative to pointer).
 * - Global Undo (Ctrl+Z) and Redo (Ctrl+Shift+Z) controls with disabled states.
 */

import { state, setState, on, emit } from '../state.js';
import * as api from '../api.js';

let rootEl = null;
let opsListEl = null;
let undoBtn = null;
let redoBtn = null;
let opCountBadge = null;

/**
 * Initialize the Log component.
 * @param {HTMLElement} root
 */
export function initLog(root) {
  rootEl = root;
  rootEl.innerHTML = `
    <div class="flex flex-col h-full bg-zinc-950">
      <!-- Panel Header -->
      <div class="flex items-center justify-between px-3.5 py-2 border-b border-zinc-800/80 bg-zinc-900/60 backdrop-blur shrink-0">
        <div class="flex items-center gap-1.5">
          <svg class="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          <span class="text-xs font-semibold uppercase tracking-wider text-zinc-300">Edit Log</span>
          <span id="opCountBadge" class="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400">0</span>
        </div>

        <!-- Global Undo / Redo buttons -->
        <div class="flex items-center gap-1">
          <button
            id="globalUndoBtn"
            class="p-1.5 rounded text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            title="Undo (Ctrl+Z)"
            disabled
            aria-label="Undo last action"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a5 5 0 015 5v2M3 10l6 6m-6-6l6-6"/>
            </svg>
          </button>

          <button
            id="globalRedoBtn"
            class="p-1.5 rounded text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
            title="Redo (Ctrl+Shift+Z)"
            disabled
            aria-label="Redo undone action"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 10h-10a5 5 0 00-5 5v2m15-7l-6 6m6-6l-6-6"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Operations List Scroll Area -->
      <div id="opsListContainer" class="flex-1 overflow-y-auto p-3 space-y-2">
        <!-- Rendered dynamically -->
      </div>
    </div>
  `;

  opsListEl = rootEl.querySelector('#opsListContainer');
  undoBtn = rootEl.querySelector('#globalUndoBtn');
  redoBtn = rootEl.querySelector('#globalRedoBtn');
  opCountBadge = rootEl.querySelector('#opCountBadge');

  undoBtn.addEventListener('click', handleUndo);
  redoBtn.addEventListener('click', handleRedo);

  // Global hotkeys for Undo/Redo
  window.addEventListener('keydown', (e) => {
    const isCtrlOrCmd = e.ctrlKey || e.metaKey;
    if (isCtrlOrCmd && e.key.toLowerCase() === 'z') {
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        return; // Let standard text undo work in text fields
      }
      e.preventDefault();
      if (e.shiftKey) {
        if (state.canRedo) handleRedo();
      } else {
        if (state.canUndo) handleUndo();
      }
    }
  });

  on('statechange', render);
  render();
}

/** Trigger Undo via API */
async function handleUndo() {
  if (!state.sessionId || !state.canUndo) return;
  try {
    await api.undo(state.sessionId);
    const [tl, tr, lg] = await Promise.all([
      api.getTimeline(state.sessionId),
      api.getTranscript(state.sessionId),
      api.getLog(state.sessionId),
    ]);
    setState({ timeline: tl, transcript: tr, log: lg });
    emit('toast', { text: 'Undid operation', type: 'info' });
    emit('video_refresh');
  } catch (err) {
    emit('toast', { text: `Undo failed: ${err.message}`, type: 'error' });
  }
}

/** Trigger Redo via API */
async function handleRedo() {
  if (!state.sessionId || !state.canRedo) return;
  try {
    await api.redo(state.sessionId);
    const [tl, tr, lg] = await Promise.all([
      api.getTimeline(state.sessionId),
      api.getTranscript(state.sessionId),
      api.getLog(state.sessionId),
    ]);
    setState({ timeline: tl, transcript: tr, log: lg });
    emit('toast', { text: 'Redid operation', type: 'info' });
    emit('video_refresh');
  } catch (err) {
    emit('toast', { text: `Redo failed: ${err.message}`, type: 'error' });
  }
}

/** Render edit log list and button states */
function render() {
  const canUndo = state.canUndo;
  const canRedo = state.canRedo;

  if (undoBtn) undoBtn.disabled = !canUndo;
  if (redoBtn) redoBtn.disabled = !canRedo;

  const ops = state.log?.ops || [];
  const pointer = state.log?.pointer ?? 0;

  if (opCountBadge) {
    opCountBadge.textContent = `${pointer}/${ops.length}`;
  }

  if (!opsListEl) return;

  if (ops.length === 0) {
    opsListEl.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center text-center p-4 text-zinc-400 dark:text-zinc-500">
        <svg class="w-8 h-8 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
        </svg>
        <p class="text-xs">No edits made yet.</p>
        <p class="text-[11px] mt-0.5 opacity-75">Your instructions and cuts will appear here.</p>
      </div>
    `;
    return;
  }

  opsListEl.innerHTML = '';

  ops.forEach((op, idx) => {
    const isApplied = idx < pointer;
    const isCurrentHead = idx === pointer - 1;

    const opCard = document.createElement('div');
    opCard.className = `p-2.5 rounded-lg border text-xs transition-all ${
      isApplied
        ? isCurrentHead
          ? 'bg-zinc-50 dark:bg-zinc-800/80 border-indigo-400 dark:border-indigo-500 shadow-sm'
          : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800'
        : 'opacity-40 bg-zinc-100/50 dark:bg-zinc-900/30 border-dashed border-zinc-300 dark:border-zinc-800'
    }`;

    // Op type styling & title
    let opTitle = 'Cut';
    let opBadgeClass = 'text-rose-600 bg-rose-50 dark:bg-rose-950/60 dark:text-rose-300 border-rose-200 dark:border-rose-900';

    if (op.op_type === 'bleep_words') {
      opTitle = 'Bleep';
      opBadgeClass = 'text-amber-600 bg-amber-50 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200 dark:border-amber-900';
    } else if (op.op_type === 'add_caption') {
      opTitle = 'Caption';
      opBadgeClass = 'text-sky-600 bg-sky-50 dark:bg-sky-950/60 dark:text-sky-300 border-sky-200 dark:border-sky-900';
    } else if (op.op_type === 'speed_change') {
      opTitle = `${op.speed || 1}x Speed`;
      opBadgeClass = 'text-purple-600 bg-purple-50 dark:bg-purple-950/60 dark:text-purple-300 border-purple-200 dark:border-purple-900';
    } else if (op.op_type === 'keep_range') {
      opTitle = 'Keep Only';
      opBadgeClass = 'text-emerald-600 bg-emerald-50 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900';
    }

    // Target text snippet summary
    const wordSnippet = getWordsSnippet(op);

    opCard.innerHTML = `
      <div class="flex items-center justify-between gap-1 mb-1">
        <div class="flex items-center gap-1.5">
          <span class="font-mono text-[10px] text-zinc-400 font-semibold">#${idx + 1}</span>
          <span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${opBadgeClass}">
            <span>${opTitle}</span>
          </span>
        </div>
        ${op.interpreted_by ? `
          <span class="text-[9px] font-mono text-zinc-400">
            ${op.interpreted_by === 'rule' ? 'rule' : 'llm'}
          </span>
        ` : ''}
      </div>

      ${wordSnippet ? `
        <div class="text-[11px] text-zinc-600 dark:text-zinc-300 font-mono truncate" title="${escapeHtml(wordSnippet)}">
          ${escapeHtml(wordSnippet)}
        </div>
      ` : ''}

      ${isCurrentHead ? `
        <div class="mt-1.5 flex items-center gap-1 text-[10px] text-indigo-600 dark:text-indigo-400 font-semibold">
          <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-ping"></span>
          <span>Current Position</span>
        </div>
      ` : ''}
    `;

    opsListEl.appendChild(opCard);
  });
}

/**
 * Extract human-readable text snippet for an operation based on transcript word_ids.
 * @param {object} op
 * @returns {string}
 */
function getWordsSnippet(op) {
  if (op.caption_text) {
    return `"${op.caption_text}"`;
  }

  const wordIds = op.word_ids;
  if (!wordIds || wordIds.length === 0) return '';

  const words = state.transcript?.words;
  if (!words || words.length === 0) {
    return `Words [${wordIds.join(', ')}]`;
  }

  const wordMap = new Map(words.map(w => [w.id, w.text]));
  const matchedTexts = wordIds.map(id => wordMap.get(id)).filter(Boolean);

  if (matchedTexts.length > 0) {
    const textPreview = matchedTexts.slice(0, 4).join(' ');
    const more = matchedTexts.length > 4 ? ` +${matchedTexts.length - 4}` : '';
    return `"${textPreview}${more}"`;
  }

  return `Words [${wordIds.join(', ')}]`;
}

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
