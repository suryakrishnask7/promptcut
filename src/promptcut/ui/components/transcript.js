/**
 * components/transcript.js — Synchronized word-by-word interactive transcript.
 *
 * Responsibilities:
 * - Renders words with distinct states:
 *   - Kept: clean text
 *   - Cut: dimmed strikethrough
 *   - Bleeped: yellow highlight or masked asterisks
 *   - Speedup: blue accent
 *   - Low-confidence: subtle wavy underline
 * - Real-time active word highlight tracking video playback (via bus 'timeupdate').
 * - Click word to seek video to that word.
 * - Word hover tools: Cut word, Bleep word, Correct text (inline edit calling PUT endpoint).
 * - Real-time word search / filter bar.
 * - Contains #transcriptContainer for test compatibility.
 */

import { state, setState, on, emit } from '../state.js';
import * as api from '../api.js';

let rootEl = null;
let containerEl = null;
let searchInput = null;
let currentActiveWordId = null;
let searchQuery = '';

/**
 * Initialize the Transcript component.
 * @param {HTMLElement} root
 */
export function initTranscript(root) {
  rootEl = root;
  rootEl.innerHTML = `
    <div class="flex flex-col h-full bg-zinc-950 overflow-hidden">
      <!-- Transcript Header & Search -->
      <div class="flex items-center justify-between px-3.5 py-2 border-b border-zinc-800/80 bg-zinc-900/60 backdrop-blur shrink-0">
        <div class="flex items-center gap-2">
          <span class="text-xs font-medium text-zinc-400">Search spoken text</span>
          <span id="wordCountBadge" class="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400">0 words</span>
        </div>

        <!-- Filter / Search Bar -->
        <div class="relative w-52 sm:w-64">
          <input
            id="transcriptSearch"
            type="text"
            placeholder="Filter words..."
            class="w-full text-xs pl-7 pr-3 py-1 rounded-md bg-zinc-800 border border-zinc-700/70 text-zinc-200 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <svg class="w-3.5 h-3.5 text-zinc-400 absolute left-2 top-2 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
        </div>
      </div>

      <!-- Transcript Container (Mandatory ID for tests) -->
      <div
        id="transcriptContainer"
        class="flex-1 overflow-y-auto p-4 leading-relaxed text-sm text-zinc-300 select-text"
      >
        <!-- Words rendered dynamically -->
      </div>
    </div>
  `;

  containerEl = rootEl.querySelector('#transcriptContainer');
  searchInput = rootEl.querySelector('#transcriptSearch');

  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value.trim().toLowerCase();
    renderWords();
  });

  // Listen to playback time updates from video player
  on('timeupdate', onTimeUpdate);
  on('statechange', render);

  render();
}

/**
 * Handle playback time updates to highlight active word.
 * @param {number} currentTime Output or source time depending on mode
 */
function onTimeUpdate(currentTime) {
  if (!containerEl || !state.transcript?.words) return;

  const targetWordId = findActiveWordId(currentTime);
  if (targetWordId === currentActiveWordId) return;

  // Un-highlight previous
  if (currentActiveWordId != null) {
    const prevEl = containerEl.querySelector(`[data-word-id="${currentActiveWordId}"]`);
    if (prevEl) {
      prevEl.classList.remove('bg-indigo-600', 'text-white', 'dark:bg-indigo-500', 'shadow-xs');
    }
  }

  currentActiveWordId = targetWordId;

  // Highlight new
  if (currentActiveWordId != null) {
    const nextEl = containerEl.querySelector(`[data-word-id="${currentActiveWordId}"]`);
    if (nextEl) {
      nextEl.classList.add('bg-indigo-600', 'text-white', 'dark:bg-indigo-500', 'shadow-xs');
    }
  }
}

/**
 * Map currentTime to a word ID.
 */
function findActiveWordId(time) {
  if (state.videoMode === 'preview' && state.timeline?.segments && state.transcript?.words) {
    const wordsById = new Map(state.transcript.words.map(w => [w.id, w]));

    for (const seg of state.timeline.segments) {
      if (time >= seg.start_time && time <= seg.end_time) {
        const segWords = seg.source_word_ids.map(id => wordsById.get(id)).filter(Boolean);
        if (segWords.length === 0) continue;

        const firstWordStart = segWords[0].start;
        const speed = seg.speed_factor || 1.0;

        for (const w of segWords) {
          const wStart = seg.start_time + Math.max(0, (w.start - firstWordStart) / speed);
          const wEnd = seg.start_time + Math.max(0, (w.end - firstWordStart) / speed);
          if (time >= wStart - 0.05 && time <= wEnd + 0.05) {
            return w.id;
          }
        }
        return segWords[0].id;
      }
    }
  }

  // Fallback to source transcript timings
  const words = state.transcript?.words;
  if (!words) return null;
  for (const w of words) {
    if (time >= w.start && time <= w.end) {
      return w.id;
    }
  }

  return null;
}

/**
 * Main render method.
 */
function render() {
  const badge = rootEl?.querySelector('#wordCountBadge');
  const words = state.transcript?.words || [];
  if (badge) {
    badge.textContent = `${words.length} words`;
  }

  renderWords();
}

/**
 * Render words with state badges and tool popups.
 */
function renderWords() {
  if (!containerEl) return;

  const words = state.transcript?.words;
  if (!words || words.length === 0) {
    containerEl.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center text-center p-6 text-zinc-500">
        <svg class="w-10 h-10 mb-2 opacity-40 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 100-6 3 3 0 000 6z"/>
        </svg>
        <p class="text-sm font-medium text-zinc-300">No transcript available</p>
        <p class="text-xs text-zinc-500 mt-1">Upload a video or audio file to transcribe with AI.</p>
      </div>
    `;
    return;
  }

  // Pre-calculate surviving words, bleeped words, and exact output timestamp for every word
  const survivingWordMap = new Map();
  const bleepedWordSet = new Set(state.timeline?.masked_word_ids || []);
  const speedupWordSet = new Set();
  const wordToOutputTimeMap = new Map();

  const wordsById = new Map(words.map(w => [w.id, w]));

  if (state.timeline?.segments) {
    for (const seg of state.timeline.segments) {
      const segWords = seg.source_word_ids.map(id => wordsById.get(id)).filter(Boolean);
      const firstWordStart = segWords.length > 0 ? segWords[0].start : 0;
      const speed = seg.speed_factor || 1.0;

      for (const wid of seg.source_word_ids) {
        survivingWordMap.set(wid, seg);
        if (seg.is_bleeped) bleepedWordSet.add(wid);
        if (seg.speed_factor && seg.speed_factor !== 1.0) speedupWordSet.add(wid);

        const w = wordsById.get(wid);
        if (w) {
          const offset = Math.max(0, (w.start - firstWordStart) / speed);
          wordToOutputTimeMap.set(wid, seg.start_time + offset);
        }
      }
    }
  }

  containerEl.innerHTML = '';
  const fragment = document.createDocumentFragment();

  // Group by speaker turns or sentences for readability
  let currentSpeaker = null;
  let paragraphEl = null;

  words.forEach((word) => {
    // New speaker header if changed
    if (word.speaker && word.speaker !== currentSpeaker) {
      currentSpeaker = word.speaker;
      const spkLabel = currentSpeaker.startsWith('Speaker') ? currentSpeaker : currentSpeaker;
      const isFirstSpk = currentSpeaker === 'Speaker 1' || currentSpeaker === 'S1';
      const badgeColor = isFirstSpk
        ? 'bg-indigo-950/80 text-indigo-300 border-indigo-700/60'
        : 'bg-emerald-950/80 text-emerald-300 border-emerald-700/60';
      const dotColor = isFirstSpk ? 'bg-indigo-400' : 'bg-emerald-400';

      const speakerHeader = document.createElement('div');
      speakerHeader.className = 'mt-4 mb-2 flex items-center gap-2 select-none';
      speakerHeader.innerHTML = `
        <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-[11px] font-semibold tracking-wide border shadow-xs ${badgeColor}">
          <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
          <span>${escapeHtml(spkLabel)}</span>
        </span>
        <span class="text-[10px] font-mono text-zinc-500">${word.start.toFixed(1)}s</span>
        <div class="flex-1 h-px bg-zinc-800/60"></div>
      `;
      fragment.appendChild(speakerHeader);

      paragraphEl = document.createElement('div');
      paragraphEl.className = 'flex flex-wrap items-center gap-x-1.5 gap-y-1 mb-3';
      fragment.appendChild(paragraphEl);
    }

    if (!paragraphEl) {
      paragraphEl = document.createElement('div');
      paragraphEl.className = 'flex flex-wrap items-center gap-x-1.5 gap-y-1 mb-3';
      fragment.appendChild(paragraphEl);
    }

    // Determine state
    const isSurviving = survivingWordMap.has(word.id);
    const isBleeped = bleepedWordSet.has(word.id);
    const isSpeedup = speedupWordSet.has(word.id);
    const isLowConfidence = (word.confidence ?? 1.0) < 0.70;

    // Search query match
    const isMatch = searchQuery && word.text.toLowerCase().includes(searchQuery);

    const span = document.createElement('span');
    span.setAttribute('data-word-id', word.id);
    span.setAttribute('data-start', word.start);
    span.setAttribute('data-end', word.end);

    // Base styling
    let classes = 'group relative inline-flex items-center px-1 py-0.5 rounded cursor-pointer transition-colors text-sm';

    if (!isSurviving) {
      // Cut
      classes += ' line-through opacity-40 text-zinc-400 bg-zinc-100/60 dark:bg-zinc-800/40 hover:opacity-80';
    } else if (isBleeped) {
      // Bleeped
      classes += ' bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200 border-b-2 border-amber-400';
    } else if (isSpeedup) {
      // Speed
      classes += ' bg-sky-100 text-sky-900 dark:bg-sky-950/60 dark:text-sky-200';
    } else {
      // Normal
      classes += ' hover:bg-zinc-100 dark:hover:bg-zinc-800';
    }

    if (isLowConfidence) {
      classes += ' underline decoration-wavy decoration-rose-400/60';
    }

    if (isMatch) {
      classes += ' ring-2 ring-amber-400 bg-amber-50 dark:bg-amber-900/40';
    }

    span.className = classes;

    // Displayed text (masked with * if bleeped and in timeline masked_word_ids)
    let displayText = word.text;
    if (isBleeped && state.timeline?.masked_word_ids?.includes(word.id)) {
      displayText = '*'.repeat(word.text.length);
    }

    // Notice bottom-full pb-1.5: connects directly to top edge of word so mouse does NOT lose hover
    span.innerHTML = `
      <span class="word-text select-text">${escapeHtml(displayText)}</span>
      <!-- Hover Action Toolbar with buffer to prevent premature closing -->
      <span class="word-toolbar absolute bottom-full left-1/2 -translate-x-1/2 pb-1.5 hidden group-hover:flex flex-col items-center z-30 pointer-events-auto">
        <span class="flex items-center gap-0.5 bg-zinc-900 text-white rounded-md shadow-2xl px-1.5 py-1 text-[11px] select-none border border-zinc-700 whitespace-nowrap">
          <button class="tool-cut px-1.5 py-0.5 rounded hover:bg-rose-500/20 hover:text-rose-300 font-medium transition-colors" title="Cut word">✂️ Cut</button>
          <span class="text-zinc-600">|</span>
          <button class="tool-bleep px-1.5 py-0.5 rounded hover:bg-amber-500/20 hover:text-amber-300 font-medium transition-colors" title="Bleep word">🤫 Bleep</button>
          <span class="text-zinc-600">|</span>
          <button class="tool-copy px-1.5 py-0.5 rounded hover:bg-indigo-500/20 hover:text-indigo-300 font-medium transition-colors" title="Copy word">📋 Copy</button>
          <span class="text-zinc-600">|</span>
          <button class="tool-edit px-1.5 py-0.5 rounded hover:bg-sky-500/20 hover:text-sky-300 font-medium transition-colors" title="Correct word text">✏️ Edit</button>
        </span>
      </span>
    `;

    // Click to seek to exact output time
    span.addEventListener('click', (e) => {
      // If clicking a toolbar action button, don't seek
      if (e.target.closest('button')) return;

      const targetTime = state.videoMode === 'preview'
        ? (wordToOutputTimeMap.get(word.id) ?? word.start)
        : word.start;

      emit('seek_to', targetTime);
    });

    // Right click also opens toolbar without disappearing
    span.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const tb = span.querySelector('.word-toolbar');
      if (tb) {
        tb.classList.toggle('hidden');
        tb.classList.toggle('flex');
      }
    });

    // Tool: Cut
    span.querySelector('.tool-cut')?.addEventListener('click', (e) => {
      e.stopPropagation();
      executeWordAction(`cut word "${word.text}"`);
    });

    // Tool: Bleep
    span.querySelector('.tool-bleep')?.addEventListener('click', (e) => {
      e.stopPropagation();
      executeWordAction(`bleep word "${word.text}"`);
    });

    // Tool: Copy word text
    span.querySelector('.tool-copy')?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(word.text).then(() => {
          emit('toast', { text: `Copied "${word.text}" to clipboard`, type: 'info' });
        }).catch(() => {
          emit('toast', { text: `Word text: ${word.text}`, type: 'info' });
        });
      } else {
        emit('toast', { text: `Word text: ${word.text}`, type: 'info' });
      }
    });

    // Tool: Edit text inline
    span.querySelector('.tool-edit')?.addEventListener('click', (e) => {
      e.stopPropagation();
      startInlineEdit(span, word);
    });

    paragraphEl.appendChild(span);
  });

  containerEl.appendChild(fragment);
}

/**
 * Dispatch quick instruction for a word.
 * @param {string} instruction
 */
async function executeWordAction(instruction) {
  if (!state.sessionId) return;
  emit('instruction_prompt', instruction);
}

/**
 * Replace word text with an inline input to correct recognition errors.
 * @param {HTMLElement} span
 * @param {object} word
 */
function startInlineEdit(span, word) {
  const textEl = span.querySelector('.word-text');
  if (!textEl) return;

  const currentText = word.text;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = currentText;
  input.className = 'w-20 text-xs px-1 py-0.5 rounded border border-indigo-500 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 focus:outline-none';

  span.replaceChild(input, textEl);
  input.focus();
  input.select();

  const commitEdit = async () => {
    const newText = input.value.trim();
    if (newText && newText !== currentText) {
      try {
        await api.correctWord(state.sessionId, word.id, newText);
        word.text = newText;
        emit('toast', { text: `Word corrected to "${newText}"`, type: 'success' });
        // Refresh transcript, timeline, and log to sync captions and timeline
        const [tr, tl, lg] = await Promise.all([
          api.getTranscript(state.sessionId),
          api.getTimeline(state.sessionId),
          api.getLog(state.sessionId),
        ]);
        setState({ transcript: tr, timeline: tl, log: lg });
        emit('video_refresh');
      } catch (err) {
        emit('toast', { text: `Correction failed: ${err.message}`, type: 'error' });
        renderWords();
      }
    } else {
      renderWords();
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitEdit();
    } else if (e.key === 'Escape') {
      renderWords();
    }
  });

  input.addEventListener('blur', () => {
    commitEdit();
  });
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
