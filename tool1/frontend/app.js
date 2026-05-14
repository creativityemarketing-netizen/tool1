// ── State ────────────────────────────────────────────────────────────────────
let currentJobId = null;
let bulkVideos = [];
let singleVideoInfo = null;
let activeSSE = null;

// ── Theme ─────────────────────────────────────────────────────────────────────
(function initTheme() {
  const saved = localStorage.getItem('tikgrab-theme') || 'dark';
  applyTheme(saved);
})();

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.querySelectorAll('.theme-toggle-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.themeVal === theme);
  });
  localStorage.setItem('tikgrab-theme', theme);
}

document.getElementById('theme-toggle').addEventListener('click', e => {
  const opt = e.target.closest('.theme-toggle-option');
  if (opt) applyTheme(opt.dataset.themeVal);
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function getCookiefile() {
  return document.getElementById('settings-cookies').value.trim() || null;
}

function fmtNum(n) {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function fmtDate(d) {
  if (!d || d.length < 8) return '—';
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
}

function thumbUrl(url) {
  if (!url) return '';
  return `/api/thumbnail?url=${encodeURIComponent(url)}`;
}

function fmtDur(s) {
  if (s == null) return '—';
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function showAlert(elId, msg, type = 'error') {
  const el = document.getElementById(elId);
  el.textContent = msg;
  el.className = `alert alert-${type} visible`;
}

function hideAlert(elId) {
  const el = document.getElementById(elId);
  el.className = 'alert';
}

function setProgress(barId, textId, pct, label) {
  document.getElementById(barId).style.width = pct + '%';
  document.getElementById(textId).textContent = label;
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');

  });
});

// ── Single Download ───────────────────────────────────────────────────────────
document.getElementById('btn-fetch-info').addEventListener('click', async () => {
  const url = document.getElementById('single-url').value.trim();
  if (!url) return showAlert('single-alert', 'Please enter a TikTok URL.', 'error');

  hideAlert('single-alert');
  const btn = document.getElementById('btn-fetch-info');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Fetching…';

  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, cookiefile: getCookiefile() }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to fetch info');
    }
    singleVideoInfo = await res.json();
    renderSinglePreview(singleVideoInfo, url);
  } catch (e) {
    showAlert('single-alert', e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Fetch Info';
  }
});

function renderSinglePreview(info, url) {
  document.getElementById('preview-thumb').src = info.thumbnail || '';
  document.getElementById('preview-title').textContent = info.title || 'Untitled';
  document.getElementById('stat-user').textContent = '👤 ' + (info.uploader || '—');
  document.getElementById('stat-duration').textContent = '⏱ ' + fmtDur(info.duration);
  document.getElementById('stat-views').textContent = '👁 ' + fmtNum(info.view_count);
  document.getElementById('stat-likes').textContent = '❤️ ' + fmtNum(info.like_count);
  document.getElementById('stat-date').textContent = '📅 ' + fmtDate(info.upload_date);

  const preview = document.getElementById('single-preview');
  preview.classList.add('visible');

  const dlBtn = document.getElementById('btn-download-single');
  dlBtn.disabled = false;
  dlBtn.dataset.url = url;
}

document.getElementById('btn-download-single').addEventListener('click', async () => {
  const url = document.getElementById('single-url').value.trim();
  if (!url) return;

  const btn = document.getElementById('btn-download-single');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Downloading…';
  hideAlert('single-alert');

  try {
    const res = await fetch('/api/download/single', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, cookiefile: getCookiefile() }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Download failed');
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `tiktok_${Date.now()}.mp4`;
    triggerDownload(blob, filename);
    showAlert('single-alert', 'Download complete!', 'success');
  } catch (e) {
    showAlert('single-alert', e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⬇ Download HD (No Watermark)';
  }
});

// ── Bulk Fetch ────────────────────────────────────────────────────────────────
document.getElementById('btn-bulk-fetch').addEventListener('click', async () => {
  const username = document.getElementById('bulk-username').value.trim();
  if (!username) return showAlert('bulk-alert', 'Please enter a TikTok username.', 'error');

  hideAlert('bulk-alert');
  if (activeSSE) { activeSSE.close(); activeSSE = null; }

  const btn = document.getElementById('btn-bulk-fetch');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting…';

  // Collect filters
  const dateFrom = (document.getElementById('filter-date-from').value || '').replace(/-/g, '');
  const dateTo = (document.getElementById('filter-date-to').value || '').replace(/-/g, '');
  const minDur = parseInt(document.getElementById('filter-min-dur').value) || null;
  const maxDur = parseInt(document.getElementById('filter-max-dur').value) || null;
  const kwRaw = document.getElementById('filter-keywords').value.trim();
  const keywords = kwRaw ? kwRaw.split(',').map(k => k.trim()).filter(Boolean) : null;
  const maxVideos = parseInt(document.getElementById('bulk-max').value) || 0; // 0 = all

  // Show progress
  const progressWrap = document.getElementById('bulk-progress-wrap');
  progressWrap.classList.add('visible');
  setProgress('bulk-progress-bar', 'bulk-progress-text', 0, 'Starting fetch…');
  document.getElementById('bulk-results-card').style.display = 'none';

  try {
    const res = await fetch('/api/bulk/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username,
        max_videos: maxVideos,
        date_from: dateFrom || null,
        date_to: dateTo || null,
        min_duration: minDur,
        max_duration: maxDur,
        keywords,
        cookiefile: getCookiefile(),
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to start fetch');
    }
    const { job_id } = await res.json();
    currentJobId = job_id;
    btn.innerHTML = '<span class="spinner"></span> Fetching…';
    startSSE(job_id);
  } catch (e) {
    showAlert('bulk-alert', e.message, 'error');
    progressWrap.classList.remove('visible');
    btn.disabled = false;
    btn.textContent = '🔍 Fetch Videos';
  }
});

function startSSE(jobId) {
  const es = new EventSource(`/api/bulk/${jobId}/progress`);
  activeSSE = es;

  es.onmessage = (e) => {
    const data = JSON.parse(e.data);

    if (data.type === 'progress') {
      const pct = data.total > 0 ? Math.round((data.fetched / data.total) * 100) : 0;
      const label = `Fetched ${data.fetched}${data.total ? ' / ' + data.total : ''} videos${data.title ? ' — ' + data.title.slice(0, 60) : ''}`;
      setProgress('bulk-progress-bar', 'bulk-progress-text', pct, label);
    }

    if (data.type === 'done') {
      es.close();
      activeSSE = null;
      bulkVideos = data.videos || [];
      setProgress('bulk-progress-bar', 'bulk-progress-text', 100, `Done — ${bulkVideos.length} videos fetched`);
      renderBulkResults(bulkVideos);

      const btn = document.getElementById('btn-bulk-fetch');
      btn.disabled = false;
      btn.textContent = '🔍 Fetch Videos';
    }

    if (data.type === 'error') {
      es.close();
      activeSSE = null;
      showAlert('bulk-alert', data.message || 'An error occurred', 'error');
      document.getElementById('bulk-progress-wrap').classList.remove('visible');
      const btn = document.getElementById('btn-bulk-fetch');
      btn.disabled = false;
      btn.textContent = '🔍 Fetch Videos';
    }
  };

  es.onerror = () => {
    es.close();
    activeSSE = null;
    // Check status via polling fallback
    setTimeout(() => pollJobStatus(jobId), 1000);
  };
}

async function pollJobStatus(jobId) {
  try {
    const res = await fetch(`/api/bulk/${jobId}/status`);
    if (!res.ok) return;
    const job = await res.json();
    if (job.status === 'done') {
      bulkVideos = job.videos || [];
      setProgress('bulk-progress-bar', 'bulk-progress-text', 100, `Done — ${bulkVideos.length} videos`);
      renderBulkResults(bulkVideos);
    } else if (job.status === 'error') {
      showAlert('bulk-alert', job.error || 'Unknown error', 'error');
    } else {
      setTimeout(() => pollJobStatus(jobId), 2000);
    }
  } catch (_) {}
  const btn = document.getElementById('btn-bulk-fetch');
  btn.disabled = false;
  btn.textContent = '🔍 Fetch Videos';
}

// ── Render Results Table ──────────────────────────────────────────────────────
function renderBulkResults(videos) {
  document.getElementById('result-count').textContent = videos.length;
  const tbody = document.getElementById('results-tbody');
  tbody.innerHTML = '';

  videos.forEach(v => {
    const tr = document.createElement('tr');
    const thumb = v.thumbnail ? thumbUrl(v.thumbnail) : '';
    tr.innerHTML = `
      <td><input type="checkbox" class="row-chk" data-id="${v.id}" /></td>
      <td class="thumb-cell">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy" onerror="this.style.display='none'" />` : '<div style="width:48px;height:64px;background:var(--surface2);border-radius:4px;"></div>'}
      </td>
      <td class="title-cell" title="${(v.title || '').replace(/"/g, '&quot;')}">${v.title || '—'}</td>
      <td>${fmtDate(v.upload_date)}</td>
      <td>${fmtDur(v.duration)}</td>
      <td>${fmtNum(v.view_count)}</td>
      <td>${fmtNum(v.like_count)}</td>
    `;
    tbody.appendChild(tr);
  });

  document.getElementById('bulk-results-card').style.display = 'block';
  document.getElementById('chk-all-header').checked = false;
}

// Select all / none
document.getElementById('chk-all-header').addEventListener('change', e => {
  document.querySelectorAll('.row-chk').forEach(c => c.checked = e.target.checked);
});
document.getElementById('btn-select-all').addEventListener('click', () => {
  document.querySelectorAll('.row-chk').forEach(c => c.checked = true);
  document.getElementById('chk-all-header').checked = true;
});
document.getElementById('btn-select-none').addEventListener('click', () => {
  document.querySelectorAll('.row-chk').forEach(c => c.checked = false);
  document.getElementById('chk-all-header').checked = false;
});

function getSelectedIds() {
  return [...document.querySelectorAll('.row-chk:checked')].map(c => c.dataset.id);
}

// ── Bulk Download Selected ────────────────────────────────────────────────────
document.getElementById('btn-download-selected').addEventListener('click', async () => {
  const ids = getSelectedIds();
  if (!ids.length) return showAlert('bulk-alert', 'Select at least one video.', 'warning');
  if (!currentJobId) return;

  const btn = document.getElementById('btn-download-selected');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Downloading…';
  hideAlert('bulk-alert');

  const dlWrap = document.getElementById('download-progress-wrap');
  dlWrap.classList.add('visible');
  setProgress('dl-progress-bar', 'dl-progress-text', 0, `Downloading ${ids.length} video(s)…`);

  try {
    const res = await fetch('/api/bulk/download-selected', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: currentJobId, video_ids: ids, cookiefile: getCookiefile() }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Download failed');
    }
    setProgress('dl-progress-bar', 'dl-progress-text', 80, 'Packaging ZIP…');
    const blob = await res.blob();
    setProgress('dl-progress-bar', 'dl-progress-text', 100, 'Done!');
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="?([^"]+)"?/);
    triggerDownload(blob, match ? match[1] : 'tiktok_videos.zip');
    showAlert('bulk-alert', `Downloaded ${ids.length} video(s) successfully.`, 'success');
  } catch (e) {
    showAlert('bulk-alert', e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '⬇ Download Selected';
    setTimeout(() => dlWrap.classList.remove('visible'), 3000);
  }
});

// ── ZIP Selected (already-downloaded files) ───────────────────────────────────
document.getElementById('btn-zip-selected').addEventListener('click', async () => {
  const ids = getSelectedIds();
  if (!ids.length) return showAlert('bulk-alert', 'Select at least one video.', 'warning');
  if (!currentJobId) return;

  const btn = document.getElementById('btn-zip-selected');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Zipping…';

  try {
    const res = await fetch('/api/zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: currentJobId, video_ids: ids }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'ZIP failed');
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="?([^"]+)"?/);
    triggerDownload(blob, match ? match[1] : 'tiktok_videos.zip');
  } catch (e) {
    showAlert('bulk-alert', e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '🗜 ZIP Selected';
  }
});

// ── Export from bulk results ──────────────────────────────────────────────────
async function doExport(format) {
  if (!bulkVideos.length) return;
  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videos: bulkVideos, format }),
    });
    if (!res.ok) throw new Error('Export failed');
    const blob = await res.blob();
    const ext = format === 'json' ? 'json' : 'csv';
    triggerDownload(blob, `tiktok_metadata.${ext}`);
  } catch (e) {
    showAlert('bulk-alert', e.message, 'error');
  }
}

document.getElementById('btn-export-csv-bulk').addEventListener('click', () => doExport('csv'));
document.getElementById('btn-export-json-bulk').addEventListener('click', () => doExport('json'));

