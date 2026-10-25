/**
 * app.js — Main application entry point for PromptCut SPA.
 *
 * Responsibilities:
 * - Bootstraps components into the 2-column studio layout.
 * - Enforces dark mode throughout the application.
 * - Manages session loading: File upload & video transcription polling.
 * - Tab switching between full-height Transcript and Edit History.
 * - Global toast notification dispatcher.
 * - Connects cross-component workflows.
 */

import { state, setState, on, emit } from './state.js';
import * as api from './api.js';

import { initChat, handleSend } from './components/chat.js?v=7';
import { initLog } from './components/log.js?v=7';
import { initVideo } from './components/video.js?v=7';
import { initTranscript } from './components/transcript.js?v=7';

// ── Theme Management (Dark Mode Only) ───────────────────────────────────────

function initTheme() {
  document.documentElement.classList.add('dark');
  localStorage.setItem('promptcut-theme', 'dark');
}

// ── Studio Inspector Tabs (Transcript / Edit History) ─────────────────────────

function initTabs() {
  const tabTranscriptBtn = document.getElementById('tabTranscriptBtn');
  const tabLogBtn = document.getElementById('tabLogBtn');
  const transcriptPanel = document.getElementById('transcriptPanel');
  const logPanel = document.getElementById('logPanel');
  const tabWordCount = document.getElementById('tabWordCount');
  const tabLogCount = document.getElementById('tabLogCount');

  if (!tabTranscriptBtn || !tabLogBtn) return;

  function setTab(active) {
    if (active === 'transcript') {
      transcriptPanel?.classList.remove('hidden');
      logPanel?.classList.add('hidden');
      tabTranscriptBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-zinc-800 border border-zinc-700/60 shadow-xs flex items-center gap-2 transition-all cursor-pointer';
      tabLogBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 flex items-center gap-2 transition-all cursor-pointer';
    } else {
      logPanel?.classList.remove('hidden');
      transcriptPanel?.classList.add('hidden');
      tabLogBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-zinc-800 border border-zinc-700/60 shadow-xs flex items-center gap-2 transition-all cursor-pointer';
      tabTranscriptBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 flex items-center gap-2 transition-all cursor-pointer';
    }
  }

  tabTranscriptBtn.addEventListener('click', () => setTab('transcript'));
  tabLogBtn.addEventListener('click', () => setTab('log'));

  // Update count badges whenever state changes
  on('statechange', (s) => {
    if (tabWordCount && s.transcript?.words) {
      tabWordCount.textContent = s.transcript.words.length;
    }
    if (tabLogCount && s.log?.operations) {
      tabLogCount.textContent = s.log.operations.length;
    }
  });
}

// ── Global Toast Notifications ───────────────────────────────────────────────

function initToasts() {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  on('toast', ({ text, type = 'info' }) => {
    const toast = document.createElement('div');
    toast.className = `px-3.5 py-2.5 rounded-lg text-xs font-medium shadow-lg flex items-center gap-2 transition-all duration-200 translate-y-2 opacity-0 pointer-events-auto border ${
      type === 'success'
        ? 'bg-emerald-950/90 text-emerald-200 border-emerald-800'
        : type === 'error'
        ? 'bg-rose-950/90 text-rose-200 border-rose-800'
        : 'bg-zinc-900/90 text-zinc-100 border-zinc-700'
    }`;

    let icon = '';
    if (type === 'success') icon = '✓';
    if (type === 'error') icon = '✕';

    toast.innerHTML = `
      ${icon ? `<span class="font-bold">${icon}</span>` : ''}
      <span class="flex-1">${text}</span>
    `;

    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
    });

    setTimeout(() => {
      toast.classList.add('opacity-0', 'translate-y-2');
      setTimeout(() => toast.remove(), 200);
    }, 3500);
  });
}

// ── Video File Upload & Polling ──────────────────────────────────────────────

async function handleFileUpload(file) {
  if (!file) return;

  const titleEl = document.getElementById('sessionTitle');
  const badge = document.getElementById('sessionStatusBadge');

  if (titleEl) titleEl.textContent = `Uploading "${file.name}"...`;
  if (badge) {
    badge.textContent = '0%';
    badge.className = 'px-2 py-0.5 rounded-md text-[10px] font-mono bg-indigo-950/80 text-indigo-300 border border-indigo-700/60';
    badge.classList.remove('hidden');
  }

  emit('toast', { text: `Uploading "${file.name}"...`, type: 'info' });

  try {
    // Stage 1: Uploading video file with live byte percentage
    const res = await api.createSession(file, (percent) => {
      if (badge) badge.textContent = `Upload ${percent}%`;
    });

    if (badge) badge.textContent = 'AI Transcribing...';
    if (titleEl) titleEl.textContent = `Transcribing "${file.name}"...`;
    emit('toast', { text: 'Video uploaded! Transcribing speech with AI...', type: 'info' });

    // Stage 2: Long-video transcription job polling
    await pollJobAndInitialize(res.session_id, res.job_id, file.name);
  } catch (err) {
    if (badge) {
      badge.textContent = 'Failed';
      badge.className = 'px-2 py-0.5 rounded-md text-[10px] font-mono bg-rose-950/80 text-rose-300 border border-rose-700/60';
    }
    emit('toast', { text: `Error: ${err.message}`, type: 'error' });
  }
}

/**
 * Poll job status until complete, supporting up to 1+ hour videos without premature timeout.
 */
async function pollJobAndInitialize(sessionId, jobId, title) {
  const maxAttempts = 1800; // 1800 * 800ms = 24 minutes max
  let attempts = 0;
  const badge = document.getElementById('sessionStatusBadge');

  while (attempts < maxAttempts) {
    attempts++;
    const job = await api.getJob(jobId);

    if (job.status === 'completed' || job.status === 'complete' || job.status === 'succeeded') {
      const [tr, tl, lg] = await Promise.all([
        api.getTranscript(sessionId),
        api.getTimeline(sessionId),
        api.getLog(sessionId),
      ]);

      setState({
        sessionId,
        videoTitle: title,
        transcript: tr,
        timeline: tl,
        log: lg,
      });

      updateSessionHeader();
      emit('toast', { text: `Session ready! Transcribed ${tr.words?.length || 0} words accurately.`, type: 'success' });
      return;
    }

    if (job.status === 'failed') {
      throw new Error(job.error || 'Transcription job failed.');
    }

    if (badge && job.progress != null && job.progress > 0) {
      badge.textContent = `Transcribing ${job.progress}%`;
    }

    await new Promise(r => setTimeout(r, 800));
  }

  throw new Error('Transcription timed out after 24 minutes. Please try again or check server logs.');
}

function updateSessionHeader() {
  const badge = document.getElementById('sessionStatusBadge');
  const titleEl = document.getElementById('sessionTitle');
  if (badge && state.sessionId) {
    const wordCount = state.transcript?.words?.length || 0;
    badge.textContent = `${wordCount} words • ID: ${state.sessionId.slice(0, 8)}`;
    badge.className = 'px-2 py-0.5 rounded-md text-[10px] font-mono bg-zinc-800 text-zinc-300 border border-zinc-700/60';
    badge.classList.remove('hidden');
  }
  if (titleEl && state.videoTitle) {
    titleEl.textContent = state.videoTitle;
  }
}

// ── Application Initialization ───────────────────────────────────────────────

function init() {
  initTheme();
  initTabs();
  initToasts();

  // Mount panels into 2-column layout
  const chatRoot = document.getElementById('chatPanel');
  const logRoot = document.getElementById('logPanel');
  const videoRoot = document.getElementById('videoPanel');
  const transcriptRoot = document.getElementById('transcriptPanel');

  if (chatRoot) initChat(chatRoot);
  if (logRoot) initLog(logRoot);
  if (videoRoot) initVideo(videoRoot);
  if (transcriptRoot) initTranscript(transcriptRoot);

  // Upload action button
  const fileInput = document.getElementById('videoFileInput');
  const uploadBtn = document.getElementById('uploadVideoBtn');
  const sampleBtn = document.getElementById('sampleVideoBtn');

  uploadBtn?.addEventListener('click', () => {
    fileInput?.click();
  });

  fileInput?.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (file) handleFileUpload(file);
    fileInput.value = '';
  });

  sampleBtn?.addEventListener('click', async () => {
    emit('toast', { text: 'Loading worked example sample session...', type: 'info' });
    const badge = document.getElementById('sessionStatusBadge');
    const titleEl = document.getElementById('sessionTitle');
    if (badge) {
      badge.textContent = 'Loading...';
      badge.className = 'px-2 py-0.5 rounded-md text-[10px] font-mono bg-indigo-950/80 text-indigo-300 border border-indigo-700/60';
      badge.classList.remove('hidden');
    }
    if (titleEl) titleEl.textContent = 'Loading Sample Presentation...';
    try {
      const res = await api.createSampleSession();
      await pollJobAndInitialize(res.session_id, res.job_id, 'Sample Presentation (Worked Example)');
    } catch (err) {
      emit('toast', { text: `Failed to load sample: ${err.message}`, type: 'error' });
      if (badge) badge.textContent = 'Failed';
    }
  });

  // Listen for instructions sent from word tools
  on('instruction_prompt', (text) => {
    handleSend(text);
  });

  console.log('PromptCut UI initialized successfully in dark mode.');
}

// Kick off when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
