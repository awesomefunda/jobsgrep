/* JobsGrep frontend */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  const queryEl        = $('query');
  const searchBtn      = $('search-btn');
  const progressPanel  = $('progress-panel');
  const progressBar    = document.querySelector('.progress-bar');
  const stageLabel     = $('progress-stage-label');
  const elapsedEl      = $('elapsed');
  const jobsLiveCount  = $('jobs-live-count');
  const jobsCountNum   = $('jobs-count-num');
  const sourceChips    = $('source-chips');
  const resultsPanel   = $('results-panel');
  const resultsSummary = $('results-summary');
  const jobsBody       = $('jobs-body');
  const downloadBtn    = $('download-btn');
  const toast          = $('toast');

  const historyPanel = $('history-panel');
  const historyList  = $('history-list');

  let currentTaskId  = null;
  let eventSource    = null;
  let elapsedTimer   = null;
  let searchStart    = null;

  const STAGE_ORDER = ['parsing', 'searching', 'scoring', 'reporting'];
  const STAGE_LABELS = {
    parsing:   'Parsing your query…',
    searching: 'Searching job boards…',
    scoring:   'Scoring matches…',
    reporting: 'Building your report…',
  };

  // ─── Populate example query ──────────────────────────────────────────
  const examples = [
    'Staff Software Engineer, remote or Bay Area, Python and distributed systems',
    'Senior ML Engineer, NYC or remote, PyTorch, LLMs',
    'Principal Backend Engineer, Kubernetes, Go, latency-sensitive systems',
    'Senior Data Engineer, dbt, Spark, remote-first company',
  ];
  queryEl.placeholder = examples[Math.floor(Math.random() * examples.length)] + '...';

  // ─── Fetch mode info ─────────────────────────────────────────────────
  fetch('/api/sources').then(r => r.json()).then(sources => {
    const badge = document.querySelector('.mode-badge');
    if (badge) {
      // Infer mode from which sources are present
      const hasJobspy = sources.some(s => s.name === 'jobspy');
      badge.textContent = hasJobspy ? 'LOCAL' : 'CLOUD';
    }
    // Pre-render source chips
    if (sourceChips) {
      sourceChips.innerHTML = sources
        .map(s => `<span class="source-chip" data-source="${s.name}">${s.name}</span>`)
        .join('');
    }
    // Build footer text
    const footerSources = $('footer-sources');
    if (footerSources) {
      footerSources.textContent = sources.map(s => s.name).join(', ');
    }
  }).catch(() => {});

  // ─── Search history ──────────────────────────────────────────────────
  loadHistory();

  $('clear-history-btn').addEventListener('click', async () => {
    await fetch('/api/history', { method: 'DELETE' });
    historyPanel.style.display = 'none';
    historyList.innerHTML = '';
  });

  async function loadHistory() {
    try {
      const r = await fetch('/api/history');
      if (!r.ok) return;
      const items = await r.json();
      renderHistory(items);
    } catch (_) {}
  }

  function renderHistory(items) {
    if (!items || !items.length) { historyPanel.style.display = 'none'; return; }
    historyPanel.style.display = 'block';
    historyList.innerHTML = items.slice(0, 8).map(item => {
      const date = new Date(item.ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      return `<div class="history-item" data-query="${escHtml(item.query)}">
        <span class="history-query">${escHtml(item.query)}</span>
        <span class="history-meta">${item.jobs_scored} matches · ${date}</span>
      </div>`;
    }).join('');
    historyList.querySelectorAll('.history-item').forEach(el => {
      el.addEventListener('click', () => {
        queryEl.value = el.dataset.query;
        queryEl.focus();
      });
    });
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Search button ───────────────────────────────────────────────────
  searchBtn.addEventListener('click', startSearch);
  queryEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) startSearch();
  });

  async function startSearch() {
    const query = queryEl.value.trim();
    if (!query) { showToast('Please enter a job search query.'); return; }

    searchBtn.disabled = true;
    progressPanel.style.display = 'block';
    resultsPanel.style.display = 'none';
    resetProgress();
    startElapsedTimer();

    // POST /api/search
    let taskId;
    try {
      const resp = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      taskId = data.task_id;
      currentTaskId = taskId;
    } catch (e) {
      showToast('Search failed: ' + e.message);
      searchBtn.disabled = false;
      return;
    }

    // SSE stream — pass query so Vercel can re-run search if cross-instance
    openStream(taskId, query);
  }

  function openStream(taskId, query) {
    if (eventSource) eventSource.close();
    const streamUrl = query
      ? `/api/stream/${taskId}?query=${encodeURIComponent(query)}`
      : `/api/stream/${taskId}`;
    eventSource = new EventSource(streamUrl);

    const stages = ['parsing', 'searching', 'scoring', 'reporting'];
    let stageIdx = 0;

    eventSource.addEventListener('progress', e => {
      const data = JSON.parse(e.data);
      const found = data.total_jobs_found || 0;
      const status = data.status || '';

      // Update live job count
      if (found > 0) {
        jobsLiveCount.style.display = 'inline';
        jobsCountNum.textContent = found;
      }

      // Update source chips
      (data.sources_searched || []).forEach(src => {
        const chip = document.querySelector(`.source-chip[data-source="${src}"]`);
        if (chip) chip.classList.add('active');
      });

      // Update per-source detail
      const perSource = data.jobs_per_source || {};
      const parts = Object.entries(perSource).map(([k, v]) => `${k}: ${v}`);
      if (parts.length) {
        const det = $('searching-detail');
        if (det) det.textContent = parts.join(' · ');
      }

      // Drive stage steps + progress bar
      if (status === 'parsing')   activateStage('parsing',   10);
      if (status === 'searching') activateStage('searching', 35);
      if (status === 'scoring')   activateStage('scoring',   70);
      if (status === 'reporting') activateStage('reporting', 90);

      // Update scoring detail
      const scoringDet = $('scoring-detail');
      if (scoringDet && status === 'scoring' && data.progress_message) {
        scoringDet.textContent = data.progress_message;
      }
    });

    eventSource.addEventListener('done', e => {
      eventSource.close();
      stopElapsedTimer();
      const data = JSON.parse(e.data);
      activateStage('reporting', 100, true);
      searchBtn.disabled = false;

      if (data.status === 'complete') {
        showResults(data);
        loadHistory();
      } else {
        showToast('Search failed: ' + (data.error || 'Unknown error'));
      }
    });

    eventSource.addEventListener('error', () => {
      eventSource.close();
      stopElapsedTimer();
      searchBtn.disabled = false;
      // Fall back to polling
      pollStatus(taskId);
    });
  }

  async function pollStatus(taskId) {
    for (let i = 0; i < 120; i++) {
      await sleep(3000);
      try {
        const r = await fetch(`/api/status/${taskId}`);
        const data = await r.json();
        if (data.status === 'complete') { stopElapsedTimer(); showResults(data); return; }
        if (data.status === 'failed')   { stopElapsedTimer(); showToast(data.error || 'Search failed'); return; }
      } catch (_) {}
    }
    showToast('Search timed out.');
  }

  async function showResults(data) {
    progressPanel.style.display = 'none';
    resultsPanel.style.display = 'block';

    resultsSummary.textContent =
      `Found ${data.scored_jobs} matching jobs from ${data.total_jobs} total`;

    // Download button
    if (data.download_url) {
      downloadBtn.href = data.download_url;
      downloadBtn.style.display = 'inline-flex';
    }

    // Preview top 10
    try {
      // We don't have a preview endpoint yet — show a placeholder
      renderPreview(data);
    } catch (_) {}
  }

  function renderPreview(data) {
    // Since we don't have a JSON preview endpoint, show summary stats
    jobsBody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align:center; padding: 2rem; color: var(--muted);">
          <div style="font-size:1.1rem; margin-bottom:0.5rem;">
            ${data.scored_jobs} jobs matched your query
          </div>
          <div style="font-size:0.88rem;">
            Sources: ${(data.sources_searched || []).join(', ')}
          </div>
          <div style="margin-top:1rem">
            <a href="${data.download_url}" class="download-btn" download>
              ⬇ Download Excel Report
            </a>
          </div>
        </td>
      </tr>`;
  }

  // ─── Stage management ────────────────────────────────────────────────
  function resetProgress() {
    progressBar.style.width = '0%';
    stageLabel.textContent = 'Starting…';
    jobsLiveCount.style.display = 'none';
    jobsCountNum.textContent = '0';
    document.querySelectorAll('.stage-step').forEach(el => {
      el.classList.remove('active', 'done');
    });
    document.querySelectorAll('.source-chip').forEach(el => el.classList.remove('active'));
  }

  let _activeStage = null;
  function activateStage(stage, pct, complete = false) {
    progressBar.style.width = pct + '%';
    stageLabel.textContent = complete ? 'Done!' : (STAGE_LABELS[stage] || stage);

    const idx = STAGE_ORDER.indexOf(stage);
    STAGE_ORDER.forEach((s, i) => {
      const el = document.querySelector(`.stage-step[data-stage="${s}"]`);
      if (!el) return;
      el.classList.remove('active', 'done');
      if (complete) {
        el.classList.add('done');
      } else if (i < idx) {
        el.classList.add('done');
      } else if (i === idx) {
        el.classList.add('active');
      }
    });
    _activeStage = stage;
  }

  // ─── Elapsed timer ───────────────────────────────────────────────────
  function startElapsedTimer() {
    searchStart = Date.now();
    elapsedEl.textContent = '0s';
    elapsedTimer = setInterval(() => {
      const secs = Math.floor((Date.now() - searchStart) / 1000);
      elapsedEl.textContent = secs < 60 ? `${secs}s` : `${Math.floor(secs/60)}m ${secs%60}s`;
    }, 1000);
  }
  function stopElapsedTimer() {
    clearInterval(elapsedTimer);
  }

  // ─── Utils ────────────────────────────────────────────────────────────
  function showToast(msg, duration = 5000) {
    toast.textContent = msg;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, duration);
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function scorePill(score) {
    const cls = score >= 0.9 ? 'score-high' : score >= 0.8 ? 'score-mid' : 'score-low';
    return `<span class="score-pill ${cls}">${(score * 100).toFixed(0)}%</span>`;
  }
})();
