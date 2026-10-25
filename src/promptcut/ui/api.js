/**
 * api.js — Thin fetch wrapper for all /api/v1/* endpoints.
 *
 * WHY:
 * 1. Isolates network logic, headers, and error parsing in one place.
 * 2. If backend endpoints evolve or change prefixes, only this file changes.
 * 3. Components never need to know raw URLs, query params, or JSON serialization details.
 */

const BASE_URL = '/api/v1';

/**
 * Standard fetch helper that unwraps JSON and throws structured errors.
 * @param {string} path
 * @param {RequestInit} [options]
 */
async function request(path, options = {}) {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      'Accept': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let errDetail = `${response.status} ${response.statusText}`;
    try {
      const errJson = await response.json();
      if (errJson && errJson.detail) {
        errDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // Body wasn't JSON, retain statusText
    }
    const error = new Error(errDetail);
    error.status = response.status;
    throw error;
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return null;
  }

  return response.json();
}

// ── Session & Job Lifecycle ──────────────────────────────────────────────────

/**
 * Create a new demo session using the bundled sample video.
 * @returns {Promise<{session_id: string, job_id: string}>}
 */
export async function createSampleSession() {
  return request('/sample-session', { method: 'POST' });
}

/**
 * Create a session from a local video file path or an uploaded File object.
 * Supports real-time upload progress tracking for large video files.
 * @param {File|string} fileOrPath
 * @param {(percent: number) => void} [onProgress]
 * @returns {Promise<{session_id: string, job_id: string}>}
 */
export async function createSession(fileOrPath, onProgress = null) {
  if (fileOrPath instanceof File) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append('file', fileOrPath);

      xhr.open('POST', `${BASE_URL}/sessions`);
      xhr.setRequestHeader('Accept', 'application/json');

      if (xhr.upload && onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const percent = Math.round((e.loaded / e.total) * 100);
            onProgress(percent);
          }
        };
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch (err) {
            reject(err);
          }
        } else {
          let msg = `Upload failed: HTTP ${xhr.status}`;
          try {
            const data = JSON.parse(xhr.responseText);
            if (data?.detail) msg = data.detail;
            else if (data?.error?.message) msg = data.error.message;
          } catch {}
          reject(new Error(msg));
        }
      };

      xhr.onerror = () => reject(new Error('Network error during video upload.'));
      xhr.send(formData);
    });
  }

  return request('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_path: fileOrPath }),
  });
}

/**
 * Poll job status until complete or failed.
 * @param {string} jobId
 * @returns {Promise<{id: string, status: 'queued'|'running'|'complete'|'failed', progress?: number, error?: string}>}
 */
export async function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}

// ── State Retrieval ──────────────────────────────────────────────────────────

/**
 * Retrieve the full transcript for a session.
 * @param {string} sessionId
 */
export async function getTranscript(sessionId) {
  return request(`/sessions/${sessionId}/transcript`);
}

/**
 * Retrieve the current output timeline segments.
 * @param {string} sessionId
 */
export async function getTimeline(sessionId) {
  return request(`/sessions/${sessionId}/timeline`);
}

/**
 * Retrieve the operation log including undo/redo availability.
 * @param {string} sessionId
 */
export async function getLog(sessionId) {
  return request(`/sessions/${sessionId}/log`);
}

// ── Editing Operations ───────────────────────────────────────────────────────

/**
 * Send a natural language instruction to the session interpreter.
 * @param {string} sessionId
 * @param {string} instruction
 * @param {object|null} [context] Optional follow-up context for clarifying responses
 * @param {'auto'|'llm_only'|'rule_only'|null} [mode]
 * @returns {Promise<{type: 'applied'|'clarify'|'rejected', interpreted_by?: string, [key: string]: any}>}
 */
export async function sendInstruction(sessionId, instruction, context = null, mode = null) {
  const body = {
    text: instruction,
    instruction: instruction,
    interpreter_mode: mode || 'auto',
    mode: mode || 'auto',
  };
  if (context) {
    if (typeof context === 'string') {
      body.answer = context;
    } else {
      body.context = context;
    }
  }

  return request(`/sessions/${sessionId}/instructions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Undo the last applied operation.
 * @param {string} sessionId
 */
export async function undo(sessionId) {
  return request(`/sessions/${sessionId}/undo`, { method: 'POST' });
}

/**
 * Redo the last undone operation.
 * @param {string} sessionId
 */
export async function redo(sessionId) {
  return request(`/sessions/${sessionId}/redo`, { method: 'POST' });
}

/**
 * Correct a word's recognized text directly in the transcript.
 * @param {string} sessionId
 * @param {string|number} wordId
 * @param {string} newText
 */
export async function correctWord(sessionId, wordId, newText) {
  return request(`/sessions/${sessionId}/transcript/words/${wordId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: newText }),
  });
}

/**
 * Set the interpreter routing mode ('auto', 'llm_only', 'rule_only').
 * @param {string} sessionId
 * @param {'auto'|'llm_only'|'rule_only'} mode
 */
export async function setInterpreterMode(sessionId, mode) {
  return request(`/sessions/${sessionId}/interpreter-mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
}

// ── Rendering & Media URLs ───────────────────────────────────────────────────

/**
 * Trigger full-quality rendering in the background.
 * @param {string} sessionId
 * @returns {Promise<{job_id: string}>}
 */
export async function startRender(sessionId) {
  return request(`/sessions/${sessionId}/render`, { method: 'POST' });
}

/**
 * Helper to get video stream URL with cache-busting timestamp or hash.
 * @param {string} sessionId
 * @param {'preview'|'original'|'rendered'} type
 * @param {string|number} [cacheBuster]
 */
export function getVideoUrl(sessionId, type = 'preview', cacheBuster = Date.now()) {
  const filename = type === 'original' ? 'original.mp4' : type === 'rendered' ? 'rendered.mp4' : 'preview.mp4';
  return `${BASE_URL}/sessions/${sessionId}/${filename}?v=${cacheBuster}`;
}
