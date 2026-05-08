"""
Genererer statisk dashboard/index.html fra databasen.

Kjøres etter scraperen:
    python build_dashboard.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper.database import (
    get_latest_snapshots,
    compute_sales_stats,
    get_project_history,
    get_current_units,
    get_recent_changes,
)
from scraper.geocode import get_cached, _NOT_FOUND

OUT_DIR = Path(__file__).parent / "dashboard"
OUT_FILE = OUT_DIR / "index.html"


def build_data() -> dict:
    """Bygger JSON-strukturen for dashbordet."""
    snapshots = get_latest_snapshots()

    projects = []
    for s in snapshots:
        finn_code = s["finn_code"]
        history = get_project_history(finn_code, days=365)
        units = get_current_units(finn_code)
        changes_week = get_recent_changes(finn_code, days_back=7)
        changes_month = get_recent_changes(finn_code, days_back=30)

        # Hent koordinater fra cache (None hvis ikke geocodet eller ikke funnet)
        lat = lng = None
        if s["address"]:
            cached = get_cached(s["address"])
            if cached is not None and cached is not _NOT_FOUND:
                lat = cached["lat"]
                lng = cached["lng"]

        projects.append({
            "finn_code": finn_code,
            "title": s["project_title"],
            "address": s["address"],
            "municipality": s["municipality"],
            "sales_stage": s["sales_stage"],
            "units_total": s["units_total"],
            "units_for_sale": s["units_for_sale"],
            "units_sold": s["units_sold"],
            "avg_price_per_m2": s["avg_price_per_m2"],
            "min_price": s["min_price"],
            "max_price": s["max_price"],
            "url": s["project_url"],
            "scraped_at": s["scraped_at"],
            "sold_last_week": compute_sales_stats(finn_code, 7),
            "sold_last_month": compute_sales_stats(finn_code, 30),
            "sold_last_year": compute_sales_stats(finn_code, 365),
            "history": history,
            "units": units,
            "changes_week": changes_week,
            "changes_month": changes_month,
            "lat": lat,
            "lng": lng,
        })

    projects.sort(key=lambda p: (p["municipality"] or "", p["title"] or ""))

    return {
        "updated": projects[0]["scraped_at"][:10] if projects else None,
        "projects": projects,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="nb">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nyboligprosjekter Trondheim</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --bg: #ffffff;
    --bg-secondary: #f5f4ee;
    --bg-tertiary: #faf9f4;
    --text: #1a1a1a;
    --text-muted: #6b6b6b;
    --text-faint: #999;
    --border: #e5e3dc;
    --accent: #185fa5;
    --success: #0f6e56;
    --warning: #854f0b;
    --danger: #a32d2d;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a1a;
      --bg-secondary: #252525;
      --bg-tertiary: #1e1e1e;
      --text: #e8e8e8;
      --text-muted: #a0a0a0;
      --text-faint: #707070;
      --border: #353535;
      --accent: #85B7EB;
      --success: #5DCAA5;
      --warning: #EF9F27;
      --danger: #F09595;
    }
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 1.5rem;
    line-height: 1.5;
  }
  .container { max-width: 1300px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 500; margin: 0; }
  h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
  h3 { font-size: 14px; font-weight: 500; margin: 0 0 8px; color: var(--text-muted); }
  .header { display: flex; align-items: baseline; justify-content: space-between;
            margin-bottom: 1.5rem; flex-wrap: wrap; gap: 8px; }
  .updated { font-size: 13px; color: var(--text-muted); margin: 4px 0 0; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 12px; margin-bottom: 1.5rem; }
  .stat { background: var(--bg-secondary); border-radius: 8px; padding: 1rem; }
  .stat-label { font-size: 13px; color: var(--text-muted); margin: 0; }
  .stat-value { font-size: 24px; font-weight: 500; margin: 4px 0 0; }
  .card { background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
          padding: 1rem 1.25rem; margin-bottom: 1.5rem; }
  .controls { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .controls select, .controls input {
    font-size: 13px; padding: 6px 10px; background: var(--bg-secondary);
    color: var(--text); border: 1px solid var(--border); border-radius: 6px;
  }
  table { width: 100%; font-size: 13px; border-collapse: collapse; }
  th { text-align: left; padding: 8px 6px; font-weight: 500; color: var(--text-muted);
       border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase;
       letter-spacing: 0.04em; }
  th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td { padding: 10px 6px; border-bottom: 1px solid var(--border); vertical-align: top; }
  td.muni { font-size: 11px; color: var(--text-faint); text-transform: uppercase;
            letter-spacing: 0.04em; }
  .stage-pill { display: inline-block; font-size: 11px; padding: 2px 8px;
                border-radius: 10px; background: var(--bg-secondary);
                color: var(--text-muted); margin-left: 6px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .footer { font-size: 12px; color: var(--text-faint); margin-top: 2rem; text-align: center; }
  .empty { color: var(--text-muted); padding: 2rem; text-align: center; }
  .num-zero { color: var(--text-faint); }
  .expander {
    display: inline-block; width: 18px; height: 18px;
    text-align: center; cursor: pointer;
    color: var(--text-muted);
    font-size: 11px; line-height: 18px;
    user-select: none; transition: transform 0.15s;
  }
  .expander.open { transform: rotate(90deg); }
  tr.detail-row > td {
    padding: 0; background: var(--bg-tertiary);
    border-bottom: 2px solid var(--border);
  }
  .detail-content { padding: 16px 20px; }
  .detail-section { margin-bottom: 18px; }
  .detail-section:last-child { margin-bottom: 0; }
  .unit-table { font-size: 12px; }
  .unit-table th { padding: 6px 8px; }
  .unit-table td { padding: 6px 8px; }
  .download-btn {
    font-size: 12px; padding: 6px 12px; background: var(--bg);
    color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; cursor: pointer; margin-left: 8px;
  }
  .download-btn:hover { background: var(--bg-secondary); }
  .badge {
    display: inline-block; padding: 1px 6px; font-size: 10px;
    border-radius: 8px; margin: 2px;
    background: var(--bg-secondary); color: var(--text-muted);
  }
  .badge-sold { background: rgba(15, 110, 86, 0.15); color: var(--success); }
  .badge-up { background: rgba(163, 45, 45, 0.15); color: var(--danger); }
  .badge-down { background: rgba(15, 110, 86, 0.15); color: var(--success); }
  /* Kart */
  #map-card { display: none; }
  #map-card.open { display: block; }
  #map { height: 500px; border-radius: 8px; border: 1px solid var(--border); }
  .map-toggle {
    font-size: 13px; padding: 6px 12px; background: var(--bg-secondary);
    color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; cursor: pointer;
  }
  .map-toggle:hover { background: var(--bg-tertiary); }
  .map-popup { font-size: 13px; min-width: 180px; }
  .map-popup .title { font-weight: 500; margin-bottom: 4px; }
  .map-popup .muni-pill {
    display: inline-block; font-size: 10px; padding: 1px 6px;
    background: #f0f0f0; color: #555; border-radius: 8px;
    margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .map-popup .stat-row { color: #555; margin: 2px 0; font-size: 12px; }
  .map-popup a { display: inline-block; margin-top: 6px; font-size: 12px; }
  .map-no-coords {
    margin-top: 8px; font-size: 12px; color: var(--text-muted);
  }
  /* Salgsoversikt */
  #sales-card { display: none; }
  #sales-card.open { display: block; }
  .period-btn { font-weight: 400; }
  .period-btn.active {
    background: var(--accent); color: #fff; border-color: var(--accent);
  }
  .sales-section { margin-bottom: 1.5rem; }
  .sales-section:last-child { margin-bottom: 0; }
  .sales-section-title {
    font-size: 13px; font-weight: 500; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.04em;
    margin: 0 0 8px;
  }
  .sales-empty {
    padding: 1rem; text-align: center; color: var(--text-muted);
    font-size: 13px;
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>Nyboligprosjekter Trondheim</h1>
      <p class="updated">Trondheim kommune · Sist oppdatert: <span id="updated-date">—</span></p>
    </div>
  </div>

  <div class="stats" id="stats"></div>

  <div class="card" id="map-card">
    <h2 style="display: flex; align-items: center; justify-content: space-between;">
      <span>Kart</span>
      <span id="map-no-coords-msg" class="map-no-coords"></span>
    </h2>
    <div id="map"></div>
  </div>

  <div class="card" id="sales-card">
    <h2 style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
      <span>Salgsoversikt</span>
      <span style="display: flex; gap: 8px; flex-wrap: wrap;">
        <button class="map-toggle period-btn" data-period="week">Siste uke</button>
        <button class="map-toggle period-btn" data-period="month">Siste måned</button>
        <button class="map-toggle period-btn" data-period="year">Siste 12 mnd</button>
        <button class="download-btn" id="sales-export-btn">Last ned alt (Excel)</button>
      </span>
    </h2>
    <div id="sales-content"></div>
  </div>

  <div class="card">
    <div class="controls">
      <select id="muni-filter">
        <option value="">Alle kommuner</option>
      </select>
      <input id="search" type="search" placeholder="Søk prosjekt..." style="flex: 1; min-width: 180px;">
      <button id="map-toggle" class="map-toggle">Vis kart</button>
      <button id="sales-toggle" class="map-toggle">Vis salgsoversikt</button>
    </div>
    <div style="overflow-x: auto;">
      <table>
        <thead>
          <tr>
            <th style="width: 24px;"></th>
            <th>Prosjekt</th>
            <th class="num">Til salgs</th>
            <th class="num">Solgt</th>
            <th class="num">Pris/m²</th>
            <th class="num">Siste uke</th>
            <th class="num">Siste måned</th>
            <th class="num">Siste 12 mnd</th>
          </tr>
        </thead>
        <tbody id="project-table"></tbody>
      </table>
    </div>
    <div id="empty-msg" class="empty" style="display:none;">Ingen prosjekter matcher filteret.</div>
  </div>

  <p class="footer">
    Genereret automatisk fra Finn.no. Salg og prisendringer estimeres ved diff mellom snapshots.
  </p>
</div>

<script>
const DATA = __DATA_PLACEHOLDER__;

function fmt(n) {
  if (n === null || n === undefined) return '–';
  return new Intl.NumberFormat('nb-NO').format(Math.round(n));
}

function fmtPct(n) {
  if (n === null || n === undefined) return '–';
  const sign = n > 0 ? '+' : '';
  return sign + n.toFixed(1) + ' %';
}

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function projectsFiltered() {
  const muniFilter = document.getElementById('muni-filter').value;
  const search = document.getElementById('search').value.toLowerCase();
  return DATA.projects.filter(p => {
    if (muniFilter && p.municipality !== muniFilter) return false;
    if (search && !(p.title || '').toLowerCase().includes(search)) return false;
    return true;
  });
}

function renderStats() {
  const projects = projectsFiltered();
  const totalProjects = projects.length;
  const totalForSale = projects.reduce((s, p) => s + (p.units_for_sale || 0), 0);
  const totalSoldWeek = projects.reduce((s, p) => s + (p.sold_last_week || 0), 0);
  const totalSoldMonth = projects.reduce((s, p) => s + (p.sold_last_month || 0), 0);

  const muniFilter = document.getElementById('muni-filter').value;
  const scopeLabel = muniFilter || 'totalt';

  document.getElementById('stats').innerHTML = `
    <div class="stat"><p class="stat-label">Prosjekter (${escapeHtml(scopeLabel)})</p><p class="stat-value">${totalProjects}</p></div>
    <div class="stat"><p class="stat-label">Enheter til salgs</p><p class="stat-value">${fmt(totalForSale)}</p></div>
    <div class="stat"><p class="stat-label">Solgt siste uke</p><p class="stat-value" style="color: var(--success);">${fmt(totalSoldWeek)}</p></div>
    <div class="stat"><p class="stat-label">Solgt siste måned</p><p class="stat-value" style="color: var(--success);">${fmt(totalSoldMonth)}</p></div>
  `;
}

function renderTable() {
  const projects = projectsFiltered();
  const body = document.getElementById('project-table');
  body.innerHTML = '';

  for (const p of projects) {
    const stage = p.sales_stage ? `<span class="stage-pill">${escapeHtml(p.sales_stage)}</span>` : '';
    const cls = (n) => (n === 0 ? 'num num-zero' : 'num');
    const hasDetail = (p.units && p.units.length > 0);
    const arrow = hasDetail ? `<span class="expander" data-finn="${p.finn_code}">▶</span>` : '';

    body.insertAdjacentHTML('beforeend', `
      <tr data-finn="${p.finn_code}">
        <td>${arrow}</td>
        <td>
          <div class="muni">${escapeHtml(p.municipality || '')}</div>
          <div><a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">${escapeHtml(p.title || '—')}</a>${stage}</div>
          <div style="font-size:12px;color:var(--text-faint);margin-top:2px;">${escapeHtml(p.address || '')}</div>
        </td>
        <td class="num">${fmt(p.units_for_sale)}</td>
        <td class="num">${fmt(p.units_sold)}</td>
        <td class="num">${p.avg_price_per_m2 ? fmt(p.avg_price_per_m2) : '–'}</td>
        <td class="${cls(p.sold_last_week)}">${fmt(p.sold_last_week)}</td>
        <td class="${cls(p.sold_last_month)}">${fmt(p.sold_last_month)}</td>
        <td class="${cls(p.sold_last_year)}">${fmt(p.sold_last_year)}</td>
      </tr>
    `);
  }

  document.getElementById('empty-msg').style.display = projects.length === 0 ? 'block' : 'none';

  body.querySelectorAll('.expander').forEach(el => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleDetail(el.dataset.finn, el);
    });
  });
}

function toggleDetail(finnCode, expander) {
  const mainRow = document.querySelector(`tr[data-finn="${finnCode}"]`);
  const existing = document.getElementById(`detail-${finnCode}`);
  if (existing) {
    existing.remove();
    expander.classList.remove('open');
    return;
  }

  const project = DATA.projects.find(p => p.finn_code === finnCode);
  if (!project) return;

  const detailRow = document.createElement('tr');
  detailRow.id = `detail-${finnCode}`;
  detailRow.className = 'detail-row';
  detailRow.innerHTML = `<td colspan="8">${renderDetailContent(project)}</td>`;
  mainRow.insertAdjacentElement('afterend', detailRow);
  expander.classList.add('open');

  detailRow.querySelector(`#export-${finnCode}`).addEventListener('click', () => exportToExcel(project));
}

function renderDetailContent(p) {
  const units = (p.units || []).slice().sort((a, b) => {
    if (a.sold !== b.sold) return a.sold - b.sold;
    if ((a.floor || 0) !== (b.floor || 0)) return (a.floor || 0) - (b.floor || 0);
    return (a.unit_id || '').localeCompare(b.unit_id || '');
  });

  const unitsHtml = units.length === 0 ? '<div class="empty">Ingen enhetsdata.</div>' : `
    <table class="unit-table">
      <thead><tr>
        <th>Enhet</th><th class="num">Etasje</th><th class="num">BRA</th>
        <th class="num">Soverom</th><th class="num">Pris</th><th class="num">Pris/m²</th><th>Status</th>
      </tr></thead>
      <tbody>
        ${units.map(u => {
          const ppm = (u.total_price && u.bra_m2) ? Math.round(u.total_price / u.bra_m2) : null;
          const status = u.sold ? '<span class="badge badge-sold">Solgt</span>' : '';
          return `<tr>
            <td>${escapeHtml(u.unit_id)}</td>
            <td class="num">${u.floor ?? '–'}</td>
            <td class="num">${u.bra_m2 ? u.bra_m2 + ' m²' : '–'}</td>
            <td class="num">${u.bedrooms ?? '–'}</td>
            <td class="num">${u.total_price ? fmt(u.total_price) + ' kr' : '–'}</td>
            <td class="num">${ppm ? fmt(ppm) + ' kr' : '–'}</td>
            <td>${status}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;

  const week = p.changes_week || {sold: [], price_changes: []};
  const month = p.changes_month || {sold: [], price_changes: []};

  const renderChanges = (label, ch) => {
    if (!ch.sold.length && !ch.price_changes.length) return '';
    const soldHtml = ch.sold.length ? `
      <div style="margin-top: 8px;"><strong>Solgt:</strong> ${ch.sold.map(s => `
        <span class="badge badge-sold">${escapeHtml(s.unit_id)} · ${fmt(s.last_seen_price)} kr</span>
      `).join(' ')}</div>` : '';
    const priceHtml = ch.price_changes.length ? `
      <div style="margin-top: 8px;"><strong>Prisendring:</strong> ${ch.price_changes.map(c => {
        const cls = c.change_pct > 0 ? 'badge-up' : 'badge-down';
        return `<span class="badge ${cls}">${escapeHtml(c.unit_id)} · ${fmt(c.old_price)} → ${fmt(c.new_price)} (${fmtPct(c.change_pct)})</span>`;
      }).join(' ')}</div>` : '';
    return `<div class="detail-section"><h3>${label}</h3>${soldHtml}${priceHtml}</div>`;
  };

  return `
    <div class="detail-content">
      <div class="detail-section">
        <h3 style="display:inline-block;margin-right:8px;">Enheter</h3>
        <button class="download-btn" id="export-${p.finn_code}">Last ned Excel</button>
        ${unitsHtml}
      </div>
      ${renderChanges('Endringer siste uke', week)}
      ${renderChanges('Endringer siste måned', month)}
    </div>
  `;
}

function exportToExcel(project) {
  const rows = [
    ['Enhet', 'Etasje', 'BRA-i (m²)', 'Soverom', 'Totalpris (kr)', 'Pris/m²', 'Status'],
  ];
  for (const u of (project.units || [])) {
    const ppm = (u.total_price && u.bra_m2) ? Math.round(u.total_price / u.bra_m2) : null;
    rows.push([
      u.unit_id || '',
      u.floor ?? '',
      u.bra_m2 ?? '',
      u.bedrooms ?? '',
      u.total_price ?? '',
      ppm ?? '',
      u.sold ? 'Solgt' : 'Til salgs',
    ]);
  }

  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet(rows);
  ws['!cols'] = [{wch: 10}, {wch: 8}, {wch: 12}, {wch: 8}, {wch: 16}, {wch: 12}, {wch: 12}];
  XLSX.utils.book_append_sheet(wb, ws, 'Enheter');

  const changeRows = [['Type', 'Enhet', 'Detalj', 'Periode']];
  const week = project.changes_week || {sold: [], price_changes: []};
  const month = project.changes_month || {sold: [], price_changes: []};
  for (const s of week.sold) {
    changeRows.push(['Solgt (siste uke)', s.unit_id, fmt(s.last_seen_price) + ' kr', s.disappeared_after]);
  }
  for (const c of week.price_changes) {
    changeRows.push(['Prisendring (siste uke)', c.unit_id, fmt(c.old_price) + ' → ' + fmt(c.new_price) + ' (' + fmtPct(c.change_pct) + ')', c.since]);
  }
  const weekIds = new Set([...week.sold.map(s=>s.unit_id), ...week.price_changes.map(c=>c.unit_id)]);
  for (const s of month.sold) {
    if (!weekIds.has(s.unit_id)) {
      changeRows.push(['Solgt (siste måned)', s.unit_id, fmt(s.last_seen_price) + ' kr', s.disappeared_after]);
    }
  }
  for (const c of month.price_changes) {
    if (!weekIds.has(c.unit_id)) {
      changeRows.push(['Prisendring (siste måned)', c.unit_id, fmt(c.old_price) + ' → ' + fmt(c.new_price) + ' (' + fmtPct(c.change_pct) + ')', c.since]);
    }
  }
  if (changeRows.length > 1) {
    const ws2 = XLSX.utils.aoa_to_sheet(changeRows);
    ws2['!cols'] = [{wch: 28}, {wch: 12}, {wch: 40}, {wch: 14}];
    XLSX.utils.book_append_sheet(wb, ws2, 'Endringer');
  }

  const safeName = (project.title || 'prosjekt').replace(/[^\wæøåÆØÅ-]/g, '_').slice(0, 60);
  XLSX.writeFile(wb, `${safeName}.xlsx`);
}

function init() {
  document.getElementById('updated-date').textContent = DATA.updated || '—';

  const sel = document.getElementById('muni-filter');
  const munis = [...new Set(DATA.projects.map(p => p.municipality).filter(Boolean))].sort();
  for (const m of munis) {
    sel.insertAdjacentHTML('beforeend', `<option value="${m}">${m}</option>`);
  }

  function update() {
    renderStats();
    renderTable();
    if (mapInitialized) renderMapMarkers();
    if (salesOpen) renderSalesContent();
  }

  sel.addEventListener('change', update);
  document.getElementById('search').addEventListener('input', update);

  document.getElementById('map-toggle').addEventListener('click', toggleMap);
  document.getElementById('sales-toggle').addEventListener('click', toggleSales);
  document.getElementById('sales-export-btn').addEventListener('click', exportAllSales);

  // Period-knapper
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentPeriod = btn.dataset.period;
      document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b === btn));
      renderSalesContent();
    });
  });
  // Aktiver "siste uke" som default
  document.querySelector('.period-btn[data-period="week"]').classList.add('active');

  update();
}

let mapInstance = null;
let markerLayer = null;
let mapInitialized = false;

function toggleMap() {
  const card = document.getElementById('map-card');
  const btn = document.getElementById('map-toggle');
  if (card.classList.contains('open')) {
    card.classList.remove('open');
    btn.textContent = 'Vis kart';
    return;
  }
  card.classList.add('open');
  btn.textContent = 'Skjul kart';

  if (!mapInitialized) {
    initMap();
    mapInitialized = true;
  } else {
    // Tving Leaflet til å re-beregne størrelse
    setTimeout(() => mapInstance.invalidateSize(), 100);
  }
  renderMapMarkers();
}

function initMap() {
  // Standard senter: Oslo/Akershus
  mapInstance = L.map('map').setView([59.91, 10.65], 10);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap',
  }).addTo(mapInstance);
  markerLayer = L.layerGroup().addTo(mapInstance);
}

function radiusForUnits(forSale) {
  // Skalér 0-100+ enheter til 6-30 px diameter
  if (!forSale || forSale <= 0) return 6;
  const min = 6, max = 28;
  const scaled = Math.sqrt(forSale) * 3;  // sqrt for å unngå at store dominerer
  return Math.max(min, Math.min(max, scaled));
}

function colorForMuni(muni) {
  // Gi hver kommune en distinkt farge
  const colors = {
    'Asker': '#1e88e5',
    'Bærum': '#43a047',
    'Nordre Follo': '#fb8c00',
    'Ås': '#8e24aa',
  };
  return colors[muni] || '#666';
}

function renderMapMarkers() {
  if (!mapInstance || !markerLayer) return;
  markerLayer.clearLayers();

  const projects = projectsFiltered();
  const withCoords = projects.filter(p => p.lat && p.lng);
  const without = projects.length - withCoords.length;

  document.getElementById('map-no-coords-msg').textContent = without > 0
    ? `${without} prosjekter mangler koordinater`
    : '';

  const bounds = [];
  for (const p of withCoords) {
    const r = radiusForUnits(p.units_for_sale);
    const marker = L.circleMarker([p.lat, p.lng], {
      radius: r,
      fillColor: colorForMuni(p.municipality),
      color: '#fff',
      weight: 1.5,
      opacity: 1,
      fillOpacity: 0.75,
    });
    marker.bindPopup(buildPopupHtml(p));
    marker.addTo(markerLayer);
    bounds.push([p.lat, p.lng]);
  }

  // Zoom til synlige markører
  if (bounds.length > 0) {
    mapInstance.fitBounds(bounds, {padding: [40, 40], maxZoom: 13});
  }
}

function buildPopupHtml(p) {
  const ppm = p.avg_price_per_m2 ? fmt(p.avg_price_per_m2) + ' kr' : '–';
  return `
    <div class="map-popup">
      <span class="muni-pill">${escapeHtml(p.municipality || '')}</span>
      <div class="title">${escapeHtml(p.title || '—')}</div>
      <div class="stat-row">${fmt(p.units_for_sale)} til salgs · ${fmt(p.units_sold)} solgt</div>
      <div class="stat-row">Pris/m²: ${ppm}</div>
      <div class="stat-row">Solgt siste uke: ${fmt(p.sold_last_week)}</div>
      <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">Åpne i Finn</a>
    </div>
  `;
}

document.addEventListener('DOMContentLoaded', init);

// === SALGSOVERSIKT ===

let salesOpen = false;
let currentPeriod = 'week';

function toggleSales() {
  const card = document.getElementById('sales-card');
  const btn = document.getElementById('sales-toggle');
  salesOpen = !card.classList.contains('open');
  card.classList.toggle('open', salesOpen);
  btn.textContent = salesOpen ? 'Skjul salgsoversikt' : 'Vis salgsoversikt';
  if (salesOpen) renderSalesContent();
}

function aggregateSales(periodKey) {
  // periodKey: 'week' | 'month' | 'year'
  const projects = projectsFiltered();
  const sold = [];
  const priceChanges = [];

  for (const p of projects) {
    const ch = (periodKey === 'week') ? p.changes_week
             : (periodKey === 'month') ? p.changes_month
             : null;
    // For "year" har vi ikke data ennå — bygg på siste måned hvis det finnes
    const source = ch || p.changes_month || {sold: [], price_changes: []};

    for (const s of source.sold) {
      sold.push({
        ...s,
        project_title: p.title,
        municipality: p.municipality,
        project_url: p.url,
      });
    }
    for (const c of source.price_changes) {
      priceChanges.push({
        ...c,
        project_title: p.title,
        municipality: p.municipality,
        project_url: p.url,
      });
    }
  }

  // Sorter: sold etter pris desc, prisendring etter |%| desc
  sold.sort((a, b) => (b.last_seen_price || 0) - (a.last_seen_price || 0));
  priceChanges.sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0));

  return {sold, priceChanges};
}

function renderSalesContent() {
  const {sold, priceChanges} = aggregateSales(currentPeriod);
  const container = document.getElementById('sales-content');

  if (sold.length === 0 && priceChanges.length === 0) {
    container.innerHTML = `<div class="sales-empty">
      Ingen registrerte salg eller prisendringer i perioden.<br>
      <small style="color: var(--text-faint);">Salg detekteres ved sammenligning mellom snapshots — krever minst to dager med data.</small>
    </div>`;
    return;
  }

  let html = '';

  if (sold.length > 0) {
    html += `<div class="sales-section">
      <h3 class="sales-section-title">Solgt (${sold.length})</h3>
      <table class="unit-table" style="width:100%;">
        <thead><tr>
          <th>Prosjekt</th><th>Enhet</th>
          <th class="num">BRA</th><th class="num">Etasje</th><th class="num">Soverom</th>
          <th class="num">Pris</th><th class="num">Pris/m²</th><th>Periode</th>
        </tr></thead>
        <tbody>
          ${sold.map(s => {
            const ppm = (s.last_seen_price && s.bra_m2) ? Math.round(s.last_seen_price / s.bra_m2) : null;
            const periode = s.disappeared_after && s.disappeared_before
              ? `${s.disappeared_after} → ${s.disappeared_before}`
              : (s.disappeared_after || '');
            return `<tr>
              <td><div class="muni" style="font-size:10px;">${escapeHtml(s.municipality || '')}</div>
                  <a href="${escapeHtml(s.project_url)}" target="_blank" rel="noopener">${escapeHtml(s.project_title || '')}</a></td>
              <td>${escapeHtml(s.unit_id)}</td>
              <td class="num">${s.bra_m2 ? s.bra_m2 + ' m²' : '–'}</td>
              <td class="num">${s.floor ?? '–'}</td>
              <td class="num">${s.bedrooms ?? '–'}</td>
              <td class="num">${s.last_seen_price ? fmt(s.last_seen_price) + ' kr' : '–'}</td>
              <td class="num">${ppm ? fmt(ppm) + ' kr' : '–'}</td>
              <td style="font-size: 11px; color: var(--text-muted);">${escapeHtml(periode)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
  }

  if (priceChanges.length > 0) {
    html += `<div class="sales-section">
      <h3 class="sales-section-title">Prisendringer (${priceChanges.length})</h3>
      <table class="unit-table" style="width:100%;">
        <thead><tr>
          <th>Prosjekt</th><th>Enhet</th>
          <th class="num">BRA</th><th class="num">Etasje</th>
          <th class="num">Gammel pris</th><th class="num">Ny pris</th><th class="num">Endring</th>
        </tr></thead>
        <tbody>
          ${priceChanges.map(c => {
            const cls = c.change_pct > 0 ? 'change-up' : 'change-down';
            return `<tr>
              <td><div class="muni" style="font-size:10px;">${escapeHtml(c.municipality || '')}</div>
                  <a href="${escapeHtml(c.project_url)}" target="_blank" rel="noopener">${escapeHtml(c.project_title || '')}</a></td>
              <td>${escapeHtml(c.unit_id)}</td>
              <td class="num">${c.bra_m2 ? c.bra_m2 + ' m²' : '–'}</td>
              <td class="num">${c.floor ?? '–'}</td>
              <td class="num">${fmt(c.old_price)} kr</td>
              <td class="num">${fmt(c.new_price)} kr</td>
              <td class="num ${cls}">${fmtPct(c.change_pct)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
  }

  container.innerHTML = html;
}

function exportAllSales() {
  const wb = XLSX.utils.book_new();

  for (const [periodKey, periodLabel] of [['week', 'Siste uke'], ['month', 'Siste måned'], ['year', 'Siste 12 mnd']]) {
    const {sold, priceChanges} = aggregateSales(periodKey);

    if (sold.length > 0) {
      const rows = [['Kommune', 'Prosjekt', 'Enhet', 'BRA (m²)', 'Etasje', 'Soverom', 'Pris (kr)', 'Pris/m² (kr)', 'Periode start', 'Periode slutt']];
      for (const s of sold) {
        const ppm = (s.last_seen_price && s.bra_m2) ? Math.round(s.last_seen_price / s.bra_m2) : '';
        rows.push([
          s.municipality || '',
          s.project_title || '',
          s.unit_id,
          s.bra_m2 || '',
          s.floor ?? '',
          s.bedrooms ?? '',
          s.last_seen_price ?? '',
          ppm,
          s.disappeared_after || '',
          s.disappeared_before || '',
        ]);
      }
      const ws = XLSX.utils.aoa_to_sheet(rows);
      ws['!cols'] = [{wch: 14}, {wch: 36}, {wch: 10}, {wch: 10}, {wch: 8}, {wch: 8}, {wch: 14}, {wch: 12}, {wch: 12}, {wch: 12}];
      XLSX.utils.book_append_sheet(wb, ws, `Solgt - ${periodLabel}`);
    }

    if (priceChanges.length > 0) {
      const rows = [['Kommune', 'Prosjekt', 'Enhet', 'BRA (m²)', 'Etasje', 'Gammel pris (kr)', 'Ny pris (kr)', 'Endring %']];
      for (const c of priceChanges) {
        rows.push([
          c.municipality || '',
          c.project_title || '',
          c.unit_id,
          c.bra_m2 || '',
          c.floor ?? '',
          c.old_price ?? '',
          c.new_price ?? '',
          c.change_pct?.toFixed(1) ?? '',
        ]);
      }
      const ws = XLSX.utils.aoa_to_sheet(rows);
      ws['!cols'] = [{wch: 14}, {wch: 36}, {wch: 10}, {wch: 10}, {wch: 8}, {wch: 16}, {wch: 16}, {wch: 12}];
      XLSX.utils.book_append_sheet(wb, ws, `Prisendring - ${periodLabel}`);
    }
  }

  if (wb.SheetNames.length === 0) {
    alert('Ingen endringer å eksportere.');
    return;
  }

  XLSX.writeFile(wb, `salgsoversikt_${DATA.updated || 'ukjent'}.xlsx`);
}
</script>
</body>
</html>
"""


def main():
    OUT_DIR.mkdir(exist_ok=True)
    data = build_data()
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False))
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Skrev {OUT_FILE} ({OUT_FILE.stat().st_size:,} bytes)")
    print(f"  {len(data['projects'])} prosjekter, oppdatert {data['updated']}")


if __name__ == "__main__":
    main()
