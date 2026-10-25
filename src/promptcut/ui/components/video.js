/**
 * components/video.js — HTML5 video player and interactive multi-segment timeline.
 *
 * Responsibilities:
 * - HTML5 video player with id="videoPlayer".
 * - Source mode toggle: Proxy (480p preview) vs Original (full resolution).
 * - Render button: triggers full-quality render job, polls status, provides download.
 * - Multi-segment custom timeline bar beneath video with:
 *   - Visual segments (normal, bleeped [yellow], speedup [blue], muted [orange]).
 *   - Click to seek playhead.
 *   - Playhead scrubber synced with video.currentTime.
 * - Emits 'timeupdate' on event bus for transcript synchronization.
 * - Keyboard shortcuts: Space (play/pause), J (-5s), K (pause/play), L (+5s).
 */

import { state, setState, on, emit } from '../state.js';
import * as api from '../api.js';

let rootEl = null;
let videoEl = null;
let timelineBarEl = null;
let playheadEl = null;
let timeDisplayEl = null;
let playPauseBtn = null;
let modeToggleBtn = null;
let renderBtn = null;

let isRendering = false;
let renderJobId = null;

/**
 * Initialize the Video component.
 * @param {HTMLElement} root
 */
export function initVideo(root) {
  rootEl = root;
  rootEl.innerHTML = `
    <div class="flex flex-col bg-zinc-950 rounded-xl overflow-hidden border border-zinc-800 shadow-md">
      <!-- Top Video Player Bar -->
      <div class="relative w-full aspect-video max-h-[44vh] bg-black flex items-center justify-center group overflow-hidden select-none">
        <video
          id="videoPlayer"
          class="w-full h-full object-contain"
          playsinline
          preload="metadata"
        >
          Your browser does not support HTML5 video.
        </video>

        <!-- Live Subtitle Overlay -->
        <div
          id="videoCaptionOverlay"
          class="absolute inset-x-0 bottom-6 pointer-events-none flex flex-col items-center justify-center px-4 transition-all duration-75 z-10"
        >
          <div
            id="videoCaptionText"
            class="hidden max-w-[90%] text-center text-white font-bold tracking-wide uppercase px-3.5 py-1.5 rounded-md bg-black/75 shadow-xl backdrop-blur-xs select-none transition-transform"
            style="font-size: 20px; line-height: 1.35; text-shadow: -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 2px 4px rgba(0,0,0,0.8);"
          ></div>
        </div>

        <!-- Floating Empty State Overlay (when no session) -->
        <div id="videoEmptyState" class="absolute inset-0 flex flex-col items-center justify-center p-6 text-center bg-zinc-950/90 backdrop-blur-xs">
          <svg class="w-12 h-12 text-zinc-600 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
          </svg>
          <p class="text-sm font-medium text-zinc-300">No Video Loaded</p>
          <p class="text-xs text-zinc-500 mt-1 max-w-xs">Upload a video or audio file to begin editing.</p>
        </div>

        <!-- Big Play Button Overlay on Hover/Paused -->
        <button
          id="bigPlayBtn"
          class="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity focus:outline-none"
          aria-label="Play/Pause"
        >
          <div class="w-14 h-14 rounded-full bg-indigo-600/90 text-white flex items-center justify-center shadow-lg hover:scale-105 transition-transform">
            <svg id="bigPlayIcon" class="w-7 h-7 translate-x-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <svg id="bigPauseIcon" class="w-7 h-7 hidden" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
          </div>
        </button>
      </div>

      <!-- Controls & Progress Toolbar -->
      <div class="p-3 bg-zinc-900 border-t border-zinc-800 space-y-2">
        <!-- Timeline Bar (Click to seek) -->
        <div class="space-y-1">
          <div
            id="timelineBar"
            class="relative w-full h-4 bg-zinc-800 rounded cursor-pointer overflow-hidden select-none"
            title="Click to seek"
            role="slider"
            aria-label="Video timeline"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow="0"
          >
            <!-- Timeline segment blocks rendered dynamically -->
            <div id="timelineSegmentsContainer" class="absolute inset-0 flex"></div>

            <!-- Playhead scrubber line -->
            <div
              id="timelinePlayhead"
              class="absolute top-0 bottom-0 w-0.5 bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)] pointer-events-none transition-all duration-75"
              style="left: 0%;"
            ></div>
          </div>

          <!-- Timeline Legend & Time Info -->
          <div class="flex items-center justify-between text-[11px] text-zinc-400 font-mono">
            <div id="timeDisplay">00:00.0 / 00:00.0</div>
            <div class="flex items-center gap-2.5 text-[10px]">
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded bg-indigo-500"></span>Kept</span>
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded bg-amber-400"></span>Bleeped</span>
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded bg-sky-400"></span>Speed</span>
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded bg-zinc-700"></span>Cut</span>
            </div>
          </div>
        </div>

        <!-- Buttons Row -->
        <div class="flex items-center justify-between pt-1">
          <div class="flex items-center gap-2">
            <!-- Play/Pause Button -->
            <button
              id="playPauseBtn"
              class="p-1.5 rounded-lg bg-zinc-800 text-zinc-200 hover:bg-zinc-700 transition-colors disabled:opacity-40"
              title="Play/Pause (Space or K)"
              disabled
              aria-label="Play or pause video"
            >
              <svg id="playIcon" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z"/>
              </svg>
              <svg id="pauseIcon" class="w-4 h-4 hidden" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
              </svg>
            </button>

            <!-- Skip -5s -->
            <button
              id="skipBackBtn"
              class="p-1.5 rounded-lg bg-zinc-800/60 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              title="Jump back 5s (J)"
              aria-label="Rewind 5 seconds"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.334 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z"/>
              </svg>
            </button>

            <!-- Skip +5s -->
            <button
              id="skipForwardBtn"
              class="p-1.5 rounded-lg bg-zinc-800/60 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              title="Jump forward 5s (L)"
              aria-label="Forward 5 seconds"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.933 12.8a1 1 0 000-1.6L6.6 7.2A1 1 0 005 8v8a1 1 0 001.6.8l5.333-4zM19.933 12.8a1 1 0 000-1.6l-5.333-4A1 1 0 0013 8v8a1 1 0 001.6.8l5.333-4z"/>
              </svg>
            </button>

            <!-- Source Mode Toggle (Proxy vs Original) -->
            <div class="h-4 w-px bg-zinc-800 mx-1"></div>
            <button
              id="videoModeToggle"
              class="px-2 py-1 rounded text-xs font-mono bg-zinc-800 text-zinc-300 hover:bg-zinc-700 border border-zinc-700 transition-colors flex items-center gap-1.5"
              title="Toggle between proxy (fast preview) and original source stream"
              aria-label="Toggle proxy or original video source"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
              <span id="videoModeLabel">Proxy (480p)</span>
            </button>
          </div>

          <!-- Full Render Action -->
          <div class="flex items-center gap-2">
            <button
              id="renderBtn"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-colors flex items-center gap-1.5 disabled:opacity-40 shadow-sm"
              disabled
              title="Render high quality output video with all edits"
            >
              <svg id="renderIcon" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
              </svg>
              <svg id="renderSpinner" class="w-3.5 h-3.5 animate-spin hidden" viewBox="0 0 24 24" fill="none">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span id="renderBtnLabel">Render Final</span>
            </button>

            <!-- Download Button (visible when rendered) -->
            <a
              id="downloadBtn"
              href="#"
              download="rendered.mp4"
              class="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition-colors hidden items-center gap-1"
              title="Download rendered video"
            >
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              <span>Download</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  `;

  videoEl = rootEl.querySelector('#videoPlayer');
  timelineBarEl = rootEl.querySelector('#timelineBar');
  playheadEl = rootEl.querySelector('#timelinePlayhead');
  timeDisplayEl = rootEl.querySelector('#timeDisplay');
  playPauseBtn = rootEl.querySelector('#playPauseBtn');
  modeToggleBtn = rootEl.querySelector('#videoModeToggle');
  renderBtn = rootEl.querySelector('#renderBtn');

  // Video event listeners
  videoEl.addEventListener('timeupdate', onVideoTimeUpdate);
  videoEl.addEventListener('play', () => {
    initAudioBleep();
    updatePlayPauseUI();
  });
  videoEl.addEventListener('pause', () => {
    stopLiveBleep();
    updatePlayPauseUI();
  });
  videoEl.addEventListener('ended', () => {
    stopLiveBleep();
    updatePlayPauseUI();
  });
  videoEl.addEventListener('seeking', () => {
    stopLiveBleep();
  });
  videoEl.addEventListener('error', () => {
    stopLiveBleep();
    console.warn('Video stream error on preview mode, falling back to original');
    if (state.videoMode === 'preview') {
      setState({ videoMode: 'original' });
      reloadVideoSrc();
    }
  });

  // Play / Pause toggles
  playPauseBtn.addEventListener('click', togglePlayPause);
  rootEl.querySelector('#bigPlayBtn')?.addEventListener('click', togglePlayPause);

  // Skip controls
  rootEl.querySelector('#skipBackBtn')?.addEventListener('click', () => seekRelative(-5));
  rootEl.querySelector('#skipForwardBtn')?.addEventListener('click', () => seekRelative(5));

  // Toggle Proxy vs Original
  modeToggleBtn.addEventListener('click', toggleVideoMode);

  // Render Action
  renderBtn.addEventListener('click', handleStartRender);

  // Timeline seek on click
  timelineBarEl.addEventListener('click', onTimelineClick);

  // Global Keyboard shortcuts (Space, J, K, L)
  window.addEventListener('keydown', onKeyDown);

  // Bus listeners
  on('seek_to', (time) => seekTo(time));
  on('video_refresh', () => reloadVideoSrc());
  on('statechange', render);

  render();
}

/** Toggle video mode (proxy vs original) */
function toggleVideoMode() {
  const newMode = state.videoMode === 'preview' ? 'original' : 'preview';
  setState({ videoMode: newMode });
  reloadVideoSrc();
  emit('toast', { text: `Switched stream to: ${newMode === 'preview' ? 'Proxy (480p)' : 'Original'}`, type: 'info' });
}

/** Reload video source keeping current playback position */
function reloadVideoSrc() {
  if (!state.sessionId || !videoEl) return;
  const currentTime = videoEl.currentTime || 0;
  const isPlaying = !videoEl.paused;

  const url = api.getVideoUrl(state.sessionId, state.videoMode, Date.now());
  videoEl.src = url;

  videoEl.onloadedmetadata = () => {
    videoEl.currentTime = Math.min(currentTime, videoEl.duration || 0);
    if (isPlaying) {
      videoEl.play().catch(() => {});
    }
  };
}

/** Seek to absolute time */
export function seekTo(seconds) {
  if (videoEl && !isNaN(seconds)) {
    const target = Math.max(0, seconds);
    try {
      if (videoEl.duration && !isNaN(videoEl.duration) && videoEl.duration > 0) {
        videoEl.currentTime = Math.min(target, videoEl.duration);
      } else {
        videoEl.currentTime = target;
      }
      stopLiveBleep();
      updateLiveCaptions(videoEl.currentTime);
      updateLiveAudioEffects(videoEl.currentTime);
    } catch (e) {
      console.warn('Seek error:', e);
    }
  }
}

/** Seek relative offset (+5s or -5s) */
function seekRelative(offset) {
  if (videoEl) {
    seekTo(videoEl.currentTime + offset);
  }
}

/** Toggle Play / Pause */
function togglePlayPause() {
  if (!videoEl || !videoEl.src) return;
  if (videoEl.paused || videoEl.ended) {
    videoEl.play().catch(err => console.error('Play failed:', err));
  } else {
    videoEl.pause();
  }
}

function updatePlayPauseUI() {
  const isPaused = videoEl.paused || videoEl.ended;
  const playIcon = rootEl?.querySelector('#playIcon');
  const pauseIcon = rootEl?.querySelector('#pauseIcon');
  const bigPlayIcon = rootEl?.querySelector('#bigPlayIcon');
  const bigPauseIcon = rootEl?.querySelector('#bigPauseIcon');

  if (playIcon && pauseIcon) {
    if (isPaused) {
      playIcon.classList.remove('hidden');
      pauseIcon.classList.add('hidden');
    } else {
      playIcon.classList.add('hidden');
      pauseIcon.classList.remove('hidden');
    }
  }

  if (bigPlayIcon && bigPauseIcon) {
    if (isPaused) {
      bigPlayIcon.classList.remove('hidden');
      bigPauseIcon.classList.add('hidden');
    } else {
      bigPlayIcon.classList.add('hidden');
      bigPauseIcon.classList.remove('hidden');
    }
  }
}

/** Handle timeupdate from video */
function onVideoTimeUpdate() {
  if (!videoEl) return;
  const current = videoEl.currentTime || 0;
  const duration = videoEl.duration || state.timeline?.total_duration || 0;

  // Update playhead
  if (playheadEl && duration > 0) {
    const pct = Math.min(100, Math.max(0, (current / duration) * 100));
    playheadEl.style.left = `${pct}%`;
    timelineBarEl.setAttribute('aria-valuenow', Math.round(pct).toString());
  }

  // Update time display
  if (timeDisplayEl) {
    timeDisplayEl.textContent = `${formatDuration(current)} / ${formatDuration(duration)}`;
  }

  // Update live subtitle overlay instantly
  updateLiveCaptions(current);

  // Update live audio censor beep and mute instantly
  updateLiveAudioEffects(current);

  // Emit to bus so Transcript can highlight active word
  emit('timeupdate', current);
}



/** Handle timeline click to seek */
function onTimelineClick(e) {
  if (!videoEl || !timelineBarEl) return;
  const rect = timelineBarEl.getBoundingClientRect();
  const clickX = e.clientX - rect.left;
  const ratio = Math.max(0, Math.min(1, clickX / rect.width));
  const targetDuration = videoEl.duration || state.timeline?.total_duration || 0;
  if (targetDuration > 0) {
    seekTo(ratio * targetDuration);
  }
}

/** Handle render button click */
async function handleStartRender() {
  if (!state.sessionId || isRendering) return;
  isRendering = true;
  updateRenderUI(true, 'Rendering...');

  try {
    const res = await api.startRender(state.sessionId);

    // Only mark completed if status is explicitly completed/succeeded
    if (res.status === 'completed' || res.status === 'complete' || res.status === 'succeeded') {
      isRendering = false;
      const downloadUrl = api.getVideoUrl(state.sessionId, 'rendered', Date.now());
      setState({ renderUrl: downloadUrl });
      updateRenderUI(false, 'Re-render');

      const dlBtn = rootEl?.querySelector('#downloadBtn');
      if (dlBtn) {
        dlBtn.href = downloadUrl;
        dlBtn.classList.remove('hidden');
        dlBtn.classList.add('inline-flex');
      }

      emit('toast', { text: 'Render completed! Click Download to view.', type: 'success' });
      return;
    }

    if (res.job_id) {
      renderJobId = res.job_id;
      emit('toast', { text: 'Rendering started in background...', type: 'info' });
      pollRenderJob(renderJobId);
    }
  } catch (err) {
    isRendering = false;
    updateRenderUI(false, 'Render Final');
    emit('toast', { text: `Render failed: ${err.message}`, type: 'error' });
  }
}

/** Poll render background job */
async function pollRenderJob(jobId) {
  try {
    const job = await api.getJob(jobId);
    if (job.status === 'completed' || job.status === 'complete' || job.status === 'succeeded') {
      isRendering = false;
      const downloadUrl = api.getVideoUrl(state.sessionId, 'rendered', Date.now());
      setState({ renderUrl: downloadUrl });
      updateRenderUI(false, 'Re-render');

      const dlBtn = rootEl?.querySelector('#downloadBtn');
      if (dlBtn) {
        dlBtn.href = downloadUrl;
        dlBtn.classList.remove('hidden');
        dlBtn.classList.add('inline-flex');
      }

      emit('toast', { text: 'Render completed! Click Download to view.', type: 'success' });
    } else if (job.status === 'failed') {
      isRendering = false;
      updateRenderUI(false, 'Render Final');
      emit('toast', { text: `Render failed: ${job.error || 'Unknown error'}`, type: 'error' });
    } else {
      const progVal = job.progress ? (job.progress <= 1 ? Math.round(job.progress * 100) : Math.round(job.progress)) : '';
      const prog = progVal !== '' ? ` ${progVal}%` : '';
      updateRenderUI(true, `Rendering${prog}...`);
      setTimeout(() => pollRenderJob(jobId), 1000);
    }
  } catch (err) {
    isRendering = false;
    updateRenderUI(false, 'Render Final');
    console.error('Job polling failed:', err);
  }
}

function updateRenderUI(loading, labelText) {
  if (!renderBtn) return;
  renderBtn.disabled = loading || !state.sessionId;
  const icon = renderBtn.querySelector('#renderIcon');
  const spinner = renderBtn.querySelector('#renderSpinner');
  const label = renderBtn.querySelector('#renderBtnLabel');

  if (icon && spinner && label) {
    label.textContent = labelText;
    if (loading) {
      icon.classList.add('hidden');
      spinner.classList.remove('hidden');
    } else {
      icon.classList.remove('hidden');
      spinner.classList.add('hidden');
    }
  }
}

/** Global hotkeys for Video */
function onKeyDown(e) {
  if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;

  if (e.code === 'Space') {
    e.preventDefault();
    togglePlayPause();
  } else if (e.key.toLowerCase() === 'j') {
    e.preventDefault();
    seekRelative(-5);
  } else if (e.key.toLowerCase() === 'k') {
    e.preventDefault();
    togglePlayPause();
  } else if (e.key.toLowerCase() === 'l') {
    e.preventDefault();
    seekRelative(5);
  }
}

/** Main state render */
function render() {
  const hasSession = !!state.sessionId;

  // Empty state overlay
  const emptyOverlay = rootEl?.querySelector('#videoEmptyState');
  if (emptyOverlay) {
    if (hasSession) {
      emptyOverlay.classList.add('hidden');
    } else {
      emptyOverlay.classList.remove('hidden');
    }
  }

  // Load video if not already loaded or session changed
  if (hasSession && videoEl && (!videoEl.src || !videoEl.src.includes(state.sessionId))) {
    reloadVideoSrc();
  }

  if (playPauseBtn) playPauseBtn.disabled = !hasSession;
  if (renderBtn && !isRendering) renderBtn.disabled = !hasSession;

  // Update mode label
  const modeLabel = rootEl?.querySelector('#videoModeLabel');
  if (modeLabel) {
    modeLabel.textContent = state.videoMode === 'preview' ? 'Proxy (480p)' : 'Original';
  }

  // Download button state
  const dlBtn = rootEl?.querySelector('#downloadBtn');
  if (dlBtn) {
    if (state.renderUrl) {
      dlBtn.href = state.renderUrl;
      dlBtn.classList.remove('hidden');
      dlBtn.classList.add('inline-flex');
    }
  }

  // Render multi-segment timeline bar
  renderTimelineSegments();

  // Sync live subtitle overlay with current position
  updateLiveCaptions(videoEl?.currentTime || 0);
}

/** Draw colored segment blocks on timeline bar */
function renderTimelineSegments() {
  const container = rootEl?.querySelector('#timelineSegmentsContainer');
  if (!container) return;

  const segments = state.timeline?.segments || [];
  const totalDur = state.timeline?.total_duration || 0;

  if (segments.length === 0 || totalDur <= 0) {
    container.innerHTML = '<div class="w-full h-full bg-zinc-800"></div>';
    return;
  }

  container.innerHTML = '';
  segments.forEach(seg => {
    const dur = seg.end_time - seg.start_time;
    const pct = (dur / totalDur) * 100;

    const block = document.createElement('div');
    block.style.width = `${pct}%`;
    block.className = 'h-full border-r border-zinc-950/40 relative';

    // Segment color based on edits
    if (seg.is_bleeped) {
      block.className += ' bg-amber-400';
      block.title = `Bleeped Segment (${dur.toFixed(1)}s)`;
    } else if (seg.speed_factor && seg.speed_factor !== 1.0) {
      block.className += ' bg-sky-500 timeline-hatched';
      block.title = `${seg.speed_factor}x Speed Segment (${dur.toFixed(1)}s)`;
    } else if (seg.is_muted) {
      block.className += ' bg-orange-500';
      block.title = `Muted Segment (${dur.toFixed(1)}s)`;
    } else {
      block.className += ' bg-indigo-500';
      block.title = `Segment ${seg.segment_id} (${dur.toFixed(1)}s)`;
    }

    container.appendChild(block);
  });
}

/**
 * Render real-time subtitle overlay matching ASS burn-in styling.
 * Guaranteed 0ms instant preview without waiting for FFmpeg re-encodes.
 * @param {number} current Current playback seconds
 */
function updateLiveCaptions(current) {
  const overlayEl = rootEl?.querySelector('#videoCaptionOverlay');
  const textEl = rootEl?.querySelector('#videoCaptionText');
  if (!overlayEl || !textEl) return;

  const captions = state.timeline?.captions || [];
  if (!captions.length) {
    textEl.classList.add('hidden');
    return;
  }

  // Find active caption covering current playback time
  const activeCap = captions.find(c => current >= c.start_time && current <= c.end_time);
  if (!activeCap) {
    textEl.classList.add('hidden');
    return;
  }

  // Vertical position
  const pos = (activeCap.position || 'bottom').toLowerCase();
  if (pos === 'top') {
    overlayEl.className = 'absolute inset-x-0 top-6 pointer-events-none flex flex-col items-center justify-center px-4 transition-all duration-75 z-10';
  } else if (pos === 'middle' || pos === 'center') {
    overlayEl.className = 'absolute inset-x-0 top-1/2 -translate-y-1/2 pointer-events-none flex flex-col items-center justify-center px-4 transition-all duration-75 z-10';
  } else {
    overlayEl.className = 'absolute inset-x-0 bottom-6 pointer-events-none flex flex-col items-center justify-center px-4 transition-all duration-75 z-10';
  }

  // Styling & Color matching ASS burned-in output
  const color = activeCap.color || '#FFFFFF';
  textEl.style.color = color;
  textEl.style.fontStyle = (activeCap.font_style || 'normal') === 'italic' ? 'italic' : 'normal';
  if (activeCap.font_size) {
    const pxSize = Math.max(14, Math.min(32, Math.round(activeCap.font_size * 0.65)));
    textEl.style.fontSize = `${pxSize}px`;
  } else {
    textEl.style.fontSize = '20px';
  }

  textEl.textContent = activeCap.text;
  textEl.classList.remove('hidden');
}

function formatDuration(secs) {
  if (isNaN(secs) || secs < 0) return '00:00.0';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  const ds = Math.floor((secs % 1) * 10);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${ds}`;
}

// ── Web Audio API Real-time Censor Bleep & Mute Synthesis ────────────────────

let audioCtx = null;
let bleepOscillator = null;
let bleepGain = null;
let isCurrentlyBleeping = false;
let isCurrentlyMuting = false;

function initAudioBleep() {
  if (audioCtx) {
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {});
    }
    return;
  }
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    audioCtx = new AudioContextClass();
    bleepOscillator = audioCtx.createOscillator();
    bleepGain = audioCtx.createGain();

    bleepOscillator.type = 'sine';
    bleepOscillator.frequency.value = 1000; // 1000 Hz standard censor bleep tone
    bleepGain.gain.setValueAtTime(0, audioCtx.currentTime);

    bleepOscillator.connect(bleepGain);
    bleepGain.connect(audioCtx.destination);
    bleepOscillator.start();
  } catch (e) {
    console.warn('Web Audio censor beep init failed:', e);
  }
}

function startLiveBleep() {
  initAudioBleep();
  if (isCurrentlyBleeping) return;
  isCurrentlyBleeping = true;
  if (videoEl) videoEl.muted = true;
  if (bleepGain && audioCtx) {
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    bleepGain.gain.setValueAtTime(0.35, audioCtx.currentTime);
  }
}

function stopLiveBleep() {
  if (isCurrentlyBleeping) {
    isCurrentlyBleeping = false;
    if (bleepGain && audioCtx) {
      bleepGain.gain.setValueAtTime(0, audioCtx.currentTime);
    }
    if (videoEl && !isCurrentlyMuting) {
      videoEl.muted = false;
    }
  }
  if (isCurrentlyMuting) {
    isCurrentlyMuting = false;
    if (videoEl) videoEl.muted = false;
  }
}

function startLiveMute() {
  if (isCurrentlyBleeping) {
    isCurrentlyBleeping = false;
    if (bleepGain && audioCtx) {
      bleepGain.gain.setValueAtTime(0, audioCtx.currentTime);
    }
  }
  if (!isCurrentlyMuting) {
    isCurrentlyMuting = true;
    if (videoEl) videoEl.muted = true;
  }
}

/**
 * Sync real-time Web Audio censor bleep & mute during preview playback.
 * Automatically ducks audio and plays 1000Hz censor bleep during bleeped timeline segments.
 * @param {number} currentTime
 */
function updateLiveAudioEffects(currentTime) {
  if (!videoEl || videoEl.paused || videoEl.ended) {
    stopLiveBleep();
    return;
  }

  const segments = state.timeline?.segments || [];
  if (!segments.length) {
    stopLiveBleep();
    return;
  }

  // Find active segment covering current playback time
  const activeSeg = segments.find(s => currentTime >= s.start_time && currentTime < s.end_time);

  if (activeSeg && activeSeg.is_bleeped) {
    startLiveBleep();
  } else if (activeSeg && activeSeg.is_muted) {
    startLiveMute();
  } else {
    stopLiveBleep();
  }
}


