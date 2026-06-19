/* JobsGrep dashboard — freshness, charts, downloads, sources, share.
   Independent of app.js (search). All lookups are null-safe. */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  const RANKING_PROMPT =
    "You are my job-search assistant. Below (or attached) is a spreadsheet of open " +
    "jobs with columns: Company, Title, Level, Location, Remote, Salary, Posted, " +
    "Source, URL. My resume follows.\n\n" +
    "1. Score every job 0-100 for fit with my resume and preferences.\n" +
    "2. Return a ranked table: Rank | Company | Title | Level | Location | Score | reason.\n" +
    "3. For the top 10, add why I fit, my biggest gap, and a 2-sentence tailored hook.\n" +
    "4. Flag any that look like a stretch or likely need a referral.\n\n" +
    "MY PREFERENCES (edit): remote? = ; locations = ; target level = ; must-have tech = .\n\n" +
    "MY RESUME:\n[PASTE YOUR RESUME HERE]";

  const PALETTE = [
    '#6366f1', '#22d3ee', '#34d399', '#fbbf24', '#f472b6',
    '#a78bfa', '#fb923c', '#4ade80', '#60a5fa', '#f87171', '#c084fc', '#2dd4bf',
  ];

  // ─── helpers ───────────────────────────────────────────────────────────
  function relTime(unixSeconds) {
    if (!unixSeconds) return null;
    const secs = Math.floor(Date.now() / 1000 - unixSeconds);
    if (secs < 60) return 'just now';
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins} minute${mins !== 1 ? 's' : ''} ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs} hour${hrs !== 1 ? 's' : ''} ago`;
    const days = Math.floor(hrs / 24);
    return `${days} day${days !== 1 ? 's' : ''} ago`;
  }
  const fmt = n => (n || 0).toLocaleString();
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // ─── Chart.js defaults for dark theme ───────────────────────────────────
  if (window.Chart) {
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = 'rgba(148,163,184,0.12)';
    Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  }

  function makeChart(id, config) {
    const el = $(id);
    if (!el || !window.Chart) return;
    new Chart(el, config);
  }

  function pie(id, rows, doughnut) {
    if (!rows || !rows.length) return;
    makeChart(id, {
      type: doughnut ? 'doughnut' : 'pie',
      data: {
        labels: rows.map(r => r.label),
        datasets: [{ data: rows.map(r => r.count), backgroundColor: PALETTE, borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { boxWidth: 12, padding: 8, font: { size: 11 } } } },
      },
    });
  }

  function bar(id, rows, horizontal, countAxisTitle) {
    if (!rows || !rows.length) return;
    // The value (count) axis is x for horizontal bars, y for vertical bars.
    const countTitle = countAxisTitle
      ? { display: true, text: countAxisTitle, color: '#94a3b8', font: { size: 11 } }
      : { display: false };
    makeChart(id, {
      type: 'bar',
      data: {
        labels: rows.map(r => r.label),
        datasets: [{ data: rows.map(r => r.count), backgroundColor: '#6366f1', borderRadius: 4 }],
      },
      options: {
        indexAxis: horizontal ? 'y' : 'x',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: !horizontal }, title: horizontal ? countTitle : { display: false } },
          y: { grid: { display: horizontal }, title: horizontal ? { display: false } : countTitle },
        },
      },
    });
  }

  // ─── render ─────────────────────────────────────────────────────────────
  function absDate(unixSeconds) {
    if (!unixSeconds) return null;
    return new Date(unixSeconds * 1000).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
    });
  }

  function renderFreshness(stats) {
    const txt = $('freshness-text');
    if (!txt) return;
    const date = absDate(stats.last_updated);
    const rel = relTime(stats.last_updated);
    const live = (stats.sources || []).filter(s => s.enabled && s.job_count > 0).length;
    txt.textContent = date
      ? `Last scraped ${date} (${rel}) · ${fmt(stats.total_jobs)} jobs from ${live} sources`
      : `${fmt(stats.total_jobs)} jobs cached`;
  }

  function renderMode(stats) {
    const badge = document.querySelector('.mode-badge');
    if (badge && stats.mode) badge.textContent = stats.mode;
  }

  function renderHeroStats(stats) {
    if ($('stat-jobs')) $('stat-jobs').textContent = fmt(stats.total_jobs);
    if ($('stat-sources')) {
      $('stat-sources').textContent = (stats.sources || []).filter(s => s.enabled).length;
    }
    if ($('stat-remote')) {
      const r = stats.remote || {};
      const total = (r.remote || 0) + (r.onsite || 0);
      $('stat-remote').textContent = total ? Math.round(100 * (r.remote || 0) / total) + '%' : '—';
    }
  }

  function renderCharts(stats) {
    pie('chart-roles', stats.by_role_family, false);
    const r = stats.remote || {};
    pie('chart-remote', [
      { label: 'Remote-friendly', count: r.remote || 0 },
      { label: 'Onsite', count: r.onsite || 0 },
    ], true);
    bar('chart-levels', stats.by_level, false, 'Number of jobs');
    bar('chart-locations', (stats.by_location || []).slice(0, 10), true);
    bar('chart-companies', (stats.top_companies || []).slice(0, 10), true);
  }

  function renderDownloads(stats) {
    const grid = $('download-grid');
    if (!grid) return;
    const fams = stats.by_role_family || [];
    if (!fams.length) {
      grid.innerHTML = '<div class="dl-empty">No job packs yet — data is refreshing. Check back soon.</div>';
      return;
    }
    let html = fams.map(f => `
      <div class="dl-card">
        <div class="dl-card-top">
          <span class="dl-name">${esc(f.label)}</span>
          <span class="dl-count">${fmt(f.count)}</span>
        </div>
        <div class="dl-links">
          <a class="dl-dl" href="/api/packs/${esc(f.slug)}">⬇ Download Excel</a>
          <a class="dl-view" href="/categories/${esc(f.slug)}">Details →</a>
        </div>
      </div>`).join('');
    html += `
      <a class="dl-card dl-card-all" href="/api/packs/all">
        <div class="dl-card-top">
          <span class="dl-name">Everything</span>
          <span class="dl-count">${fmt(stats.total_jobs)}</span>
        </div>
        <span class="dl-action">⬇ Download full workbook</span>
      </a>`;
    grid.innerHTML = html;
  }

  function sourceType(t) {
    if (t === 'scraper') return { label: 'Scraper', cls: 'badge-warn' };
    if (t === 'official_api') return { label: 'Licensed / Key', cls: 'badge-info' };
    if (t === 'community_api') return { label: 'Community', cls: 'badge-info' };
    return { label: 'Public API', cls: 'badge-ok' };
  }

  function renderSources(stats) {
    const grid = $('sources-grid');
    if (!grid) return;
    // Only show sources that actually have jobs — avoids confusing "0 jobs" cards
    // from sources whose hardcoded company lists are stale.
    const sources = (stats.sources || [])
      .filter(s => s.job_count > 0)
      .sort((a, b) => b.job_count - a.job_count);
    if (!sources.length) { grid.innerHTML = '<div class="dl-empty">Sources are refreshing — check back shortly.</div>'; return; }
    grid.innerHTML = sources.map(s => {
      const t = sourceType(s.type);
      const status = s.enabled ? '' : '<span class="src-off">disabled in this mode</span>';
      return `<div class="src-card ${s.enabled ? '' : 'src-card-off'}">
        <div class="src-head">
          <span class="src-name">${esc(s.name)}</span>
          <span class="badge ${t.cls}">${t.label}</span>
        </div>
        <div class="src-desc">${esc(s.description || '')}</div>
        <div class="src-foot">
          <span class="src-count">${fmt(s.job_count)} jobs</span>
          ${status}
        </div>
      </div>`;
    }).join('');
  }

  function wireButtons() {
    const copyBtn = $('copy-prompt');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(RANKING_PROMPT);
          const orig = copyBtn.textContent;
          copyBtn.textContent = '✓ Copied!';
          setTimeout(() => { copyBtn.textContent = orig; }, 1800);
        } catch (_) {}
      });
    }

    const url = 'https://jobsgrep.com/';
    const text = 'Every open tech job in one free spreadsheet — then rank them against your resume with your own AI:';
    const x = $('share-x');
    if (x) x.href = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`;
    const ln = $('share-ln');
    if (ln) ln.href = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`;
    const copyLink = $('share-copy');
    if (copyLink) {
      copyLink.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(url);
          const orig = copyLink.textContent;
          copyLink.textContent = '✓ Link copied!';
          setTimeout(() => { copyLink.textContent = orig; }, 1800);
        } catch (_) {}
      });
    }
  }

  // ─── boot ────────────────────────────────────────────────────────────────
  wireButtons();
  fetch('/api/stats')
    .then(r => r.json())
    .then(stats => {
      renderFreshness(stats);
      renderMode(stats);
      renderHeroStats(stats);
      renderCharts(stats);
      renderDownloads(stats);
      renderSources(stats);
    })
    .catch(() => {
      const txt = $('freshness-text');
      if (txt) txt.textContent = 'Job data temporarily unavailable';
    });
})();
