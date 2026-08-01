"""Self-contained, offline HTML report generator for agent-helper campaigns.

Design constraints (see requirements in
``docs/project/agent_helper_benchmark.md``):

- Fully self-contained: all CSS/JS is inlined, no CDN/font/network
  dependency, safe to open directly from disk (``file://``) or from a static
  file server.
- All untrusted content (task names, model identifiers, notes, error
  messages, artifact paths, reviewer notes -- anything that ultimately came
  from a model or a user-provided fixture) is HTML-escaped via :func:`esc`
  before being interpolated into markup. Never use an f-string to inject
  untrusted text directly into HTML.
- Bilingual DE/EN content with a small JS toggle button; both languages are
  present in the markup (``.i18n-de`` / ``.i18n-en``) so the page degrades
  gracefully to German (the default) if JavaScript is disabled.
- The most recent campaigns (``expand_latest``, default 2) are expanded by
  default via native ``<details open>``; older campaigns are collapsed
  (``<details>`` without ``open``) but remain fully present and selectable
  in the same document, with no server round-trip needed to view them.
- Tables get lightweight, dependency-free client-side sort (click a header)
  and filter (a text input above the table) -- no charting/table library.
- Artifact links only render as a clickable relative ``<a href>`` when the
  path looks like a safe, campaign-relative path; anything that looks like
  an absolute filesystem path is shown as escaped plain text instead, never
  as a clickable link (see :func:`render_artifact_link`).
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional, Sequence

from . import charts
from .model_inventory import FeasibilityProjection, ModelSpec
from .report_style import (
    acceptance_css_class as _acceptance_class,
    bi,
    esc,
    fmt,
    scaling_css_class as _scaling_class,
    status_css_class as _status_class,
    tier_css_class as _tier_class,
)
from .schema import NA, AggregateRecord, CapacityProfileRecord, SampleRecord

REPORT_TEMPLATE_VERSION = "agent-helper-report-v1"


def render_artifact_link(path: Optional[str]) -> str:
    """Render a campaign-relative artifact path as a safe link, or escaped text.

    A path is only linked if it does not look like an absolute filesystem
    path (no drive letter, no leading ``/`` or ``\\``, no ``..`` traversal
    above the campaign directory). Backslashes are normalized to forward
    slashes for portability. Anything else is shown as plain escaped text
    with a short caveat, never as a clickable link.
    """

    if not path:
        return NA
    normalized = path.replace("\\", "/")
    looks_absolute = (
        normalized.startswith("/")
        or (len(normalized) > 1 and normalized[1] == ":")
        or normalized.startswith("..")
        or "://" in normalized
    )
    if looks_absolute:
        return f'{esc(path)} <span class="hint">(absolute path, not linked)</span>'
    return f'<a href="{esc(normalized)}" rel="noopener noreferrer">{esc(normalized)}</a>'


@dataclasses.dataclass
class CampaignReportData:
    """One campaign's data as needed by the report (already loaded, in-memory)."""

    campaign_id: str
    generated_at: str
    title: Optional[str] = None
    aggregates: Sequence[AggregateRecord] = ()
    samples: Sequence[SampleRecord] = ()
    #: Post-quality-gate concurrency/capacity-profile rows (concurrency
    #: levels 1/2/4 against the SAME already-accepted local model), if any
    #: were measured for this campaign. Empty by default -- older
    #: campaigns/CSVs created before this feature existed render with no
    #: capacity-profile section rather than an error.
    capacity_profiles: Sequence[CapacityProfileRecord] = ()


# ---------------------------------------------------------------------------
# Static assets (CSS/JS) -- inlined, no external dependency
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f5f6f8;
  --panel: #ffffff;
  --text: #1a2230;
  --muted: #5b6472;
  --border: #dde1e7;
  --accent: #2b6cb0;
  --accent-2: #2f855a;
  --warn: #c05621;
  --bad: #c53030;
  --tier1: #2f855a;
  --tier2: #c05621;
  --tier3: #c53030;
  --tier-na: #718096;
  --tier-notusable: #742a2a;
  --tier-provisional: #2b6cb0;
  --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10141c;
    --panel: #1a212c;
    --text: #e6e9ef;
    --muted: #9aa5b1;
    --border: #2c3644;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.45;
  padding: 0 0 3rem 0;
}
a { color: var(--accent); }
.container { max-width: 1180px; margin: 0 auto; padding: 0 1.25rem; }
header.app-header {
  background: linear-gradient(135deg, #1a365d, #2b6cb0 60%, #2f855a);
  color: #fff;
  padding: 2rem 0 1.5rem 0;
  margin-bottom: 1.5rem;
}
header.app-header h1 { margin: 0 0 0.25rem 0; font-size: 1.7rem; }
header.app-header .subtitle { opacity: 0.92; font-size: 0.95rem; }
.controls { display: flex; gap: 0.5rem; align-items: center; margin-top: 1rem; flex-wrap: wrap; }
button.lang-toggle, button.print-btn, button.fullscreen-btn {
  background: rgba(255,255,255,0.15);
  border: 1px solid rgba(255,255,255,0.5);
  color: #fff;
  padding: 0.35rem 0.9rem;
  border-radius: 999px;
  cursor: pointer;
  font-size: 0.85rem;
}
button.lang-toggle:hover, button.print-btn:hover, button.fullscreen-btn:hover { background: rgba(255,255,255,0.3); }
button.fullscreen-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.shortcut-hint { opacity: 0.85; font-size: 0.78rem; }
.shortcut-hint kbd {
  background: rgba(255,255,255,0.2);
  border: 1px solid rgba(255,255,255,0.5);
  border-radius: 4px;
  padding: 0.05rem 0.35rem;
  font-family: inherit;
}
section.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.25rem 1.5rem 1.25rem;
  margin-bottom: 1.5rem;
}
section.panel > h2 { margin-top: 0; }
h2 .badge, h3 .badge {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  vertical-align: middle;
  margin-left: 0.5rem;
}
.exec-summary ul { padding-left: 1.2rem; }
.filter-bar { margin-bottom: 0.6rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
.filter-bar input[type="search"] {
  flex: 1 1 240px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
}
table.data-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.86rem;
}
table.data-table caption { text-align: left; color: var(--muted); font-size: 0.8rem; margin-bottom: 0.4rem; }
table.data-table th, table.data-table td {
  border-bottom: 1px solid var(--border);
  padding: 0.4rem 0.55rem;
  text-align: left;
  vertical-align: top;
}
table.data-table thead th {
  position: sticky;
  top: 0;
  background: var(--panel);
  cursor: pointer;
  white-space: nowrap;
}
table.data-table thead th:hover { color: var(--accent); }
table.data-table thead th.sorted-asc::after { content: " \\25B2"; font-size: 0.7em; }
table.data-table thead th.sorted-desc::after { content: " \\25BC"; font-size: 0.7em; }
.table-scroll { max-height: 480px; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.tier { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.75rem; color: #fff; }
.tier-1 { background: var(--tier1); }
.tier-2 { background: var(--tier2); }
.tier-3 { background: var(--tier3); }
.tier-na { background: var(--tier-na); }
.tier-notusable { background: var(--tier-notusable); font-weight: 700; }
.tier-provisional { background: var(--tier-provisional); }
.tier-measured { background: var(--tier1); }
.tier-projection { background: var(--tier2); }
.tier-unknown { background: var(--tier-na); }
.status-success { color: var(--accent-2); font-weight: 600; }
.status-error, .status-timeout { color: var(--bad); font-weight: 600; }
.status-skipped { color: var(--muted); }
.hint { color: var(--muted); font-size: 0.8rem; }
.hint-warning { color: var(--bad); font-weight: 600; }
details.campaign { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 1rem; background: var(--panel); }
details.campaign > summary {
  cursor: pointer;
  padding: 0.85rem 1.1rem;
  font-weight: 600;
  list-style: none;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  align-items: center;
}
details.campaign > summary::-webkit-details-marker { display: none; }
details.campaign > summary::before { content: "\\25B8"; margin-right: 0.5rem; }
details.campaign[open] > summary::before { content: "\\25BE"; }
details.campaign .campaign-body { padding: 0 1.1rem 1.1rem 1.1rem; }
.chip { display: inline-block; background: var(--bg); border: 1px solid var(--border); border-radius: 999px; padding: 0.05rem 0.55rem; font-size: 0.75rem; color: var(--muted); margin-left: 0.4rem; }
svg.chart { width: 100%; height: auto; background: var(--panel); margin-bottom: 0.35rem; }
svg.chart text { fill: var(--text); font-size: 11px; }
svg.chart .axis { stroke: var(--muted); stroke-width: 1; }
svg.chart .gridline { stroke: var(--border); stroke-width: 1; }
svg.heatmap-chart, svg.critical-path-chart, svg.feasibility-chart { overflow: visible; }
.legend-list { list-style: none; padding: 0; margin: 0.5rem 0 0 0; font-size: 0.82rem; }
.legend-list li { display: flex; align-items: center; gap: 0.4rem; margin: 0.15rem 0; }
.legend-dot { width: 0.7rem; height: 0.7rem; border-radius: 50%; display: inline-block; flex: none; }
h3, h4 { color: var(--text); }
h4 { font-size: 0.95rem; margin: 1rem 0 0.4rem 0; color: var(--muted); }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
  gap: 0.7rem;
  margin: 0.6rem 0 1.1rem 0;
}
.kpi-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
}
.kpi-card-warn { border-left-color: var(--bad); }
.kpi-value { font-size: 1.5rem; font-weight: 700; line-height: 1.1; }
.kpi-label { color: var(--muted); font-size: 0.75rem; margin-top: 0.2rem; }
.i18n-en { display: none; }
html[data-lang="en"] .i18n-de { display: none; }
html[data-lang="en"] .i18n-en { display: inline; }
footer.app-footer { text-align: center; color: var(--muted); font-size: 0.8rem; margin-top: 2rem; }
.methodology dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 1rem; }
.methodology dt { font-weight: 600; }
@media (max-width: 640px) {
  header.app-header h1 { font-size: 1.3rem; }
  table.data-table { font-size: 0.78rem; }
}
@media print {
  button.lang-toggle, button.print-btn, button.fullscreen-btn, .shortcut-hint, .filter-bar { display: none; }
  details.campaign { break-inside: avoid; }
  details.campaign:not([open]) > *:not(summary) { display: block !important; }
  svg.chart, .kpi-grid { break-inside: avoid; }
}
"""

_JS = """
(function () {
  "use strict";

  function currentLang() {
    return document.documentElement.getAttribute("data-lang") || "de";
  }

  function setLang(lang) {
    document.documentElement.setAttribute("data-lang", lang);
    var btn = document.getElementById("langToggle");
    if (btn) {
      btn.textContent = lang === "de" ? "EN" : "DE";
      btn.setAttribute("aria-label", lang === "de" ? "Switch to English" : "Auf Deutsch umschalten");
    }
  }

  function initLangToggle() {
    var btn = document.getElementById("langToggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      setLang(currentLang() === "de" ? "en" : "de");
    });
    setLang(currentLang());
  }

  function toggleLang() {
    setLang(currentLang() === "de" ? "en" : "de");
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen();
      }
    } else if (document.exitFullscreen) {
      document.exitFullscreen();
    }
  }

  function initFullscreenToggle() {
    var btn = document.getElementById("fullscreenBtn");
    if (!btn) return;
    if (!document.documentElement.requestFullscreen) {
      btn.disabled = true;
      btn.title = "Fullscreen not supported by this viewer";
      return;
    }
    btn.addEventListener("click", toggleFullscreen);
  }

  function isTypingTarget(el) {
    if (!el || !el.tagName) return false;
    var tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }

  function initKeyboardShortcuts() {
    // Offline-report keyboard navigation: L toggles language, F toggles
    // fullscreen, P prints -- mirrored from the presentation-deck pattern
    // used elsewhere in this workspace. Ignored while typing in a filter
    // input/textarea, and ignored with any modifier key held (so browser
    // shortcuts like Ctrl+F / Ctrl+P keep working normally).
    document.addEventListener("keydown", function (evt) {
      if (evt.altKey || evt.ctrlKey || evt.metaKey || evt.shiftKey) return;
      if (isTypingTarget(evt.target)) return;
      var key = (evt.key || "").toLowerCase();
      if (key === "l") {
        toggleLang();
      } else if (key === "f") {
        toggleFullscreen();
      } else if (key === "p") {
        evt.preventDefault();
        window.print();
      }
    });
  }

  function cellSortValue(cell) {
    var raw = cell.getAttribute("data-sort-value");
    if (raw === null) raw = cell.textContent.trim();
    var num = parseFloat(raw.replace(/,/g, ""));
    if (!isNaN(num) && /^-?[0-9.]+$/.test(raw.trim())) return num;
    return raw.toLowerCase();
  }

  function sortTableBy(table, colIndex, ascending) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var va = cellSortValue(a.cells[colIndex]);
      var vb = cellSortValue(b.cells[colIndex]);
      if (va < vb) return ascending ? -1 : 1;
      if (va > vb) return ascending ? 1 : -1;
      return 0;
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
  }

  function attachSorting(table) {
    var headers = table.tHead ? Array.prototype.slice.call(table.tHead.rows[0].cells) : [];
    headers.forEach(function (th, index) {
      th.setAttribute("tabindex", "0");
      th.setAttribute("role", "button");
      var toggleSort = function () {
        var ascending = !th.classList.contains("sorted-asc");
        headers.forEach(function (h) { h.classList.remove("sorted-asc", "sorted-desc"); });
        th.classList.add(ascending ? "sorted-asc" : "sorted-desc");
        sortTableBy(table, index, ascending);
      };
      th.addEventListener("click", toggleSort);
      th.addEventListener("keydown", function (evt) {
        if (evt.key === "Enter" || evt.key === " ") {
          evt.preventDefault();
          toggleSort();
        }
      });
    });
  }

  function attachFilter(input) {
    var targetId = input.getAttribute("data-filter-target");
    var table = document.getElementById(targetId);
    if (!table) return;
    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      var rows = table.tBodies[0] ? table.tBodies[0].rows : [];
      Array.prototype.forEach.call(rows, function (row) {
        var hay = row.textContent.toLowerCase();
        row.style.display = needle === "" || hay.indexOf(needle) !== -1 ? "" : "none";
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initLangToggle();
    initFullscreenToggle();
    initKeyboardShortcuts();
    Array.prototype.forEach.call(document.querySelectorAll("table.sortable"), attachSorting);
    Array.prototype.forEach.call(document.querySelectorAll(".table-filter"), attachFilter);

    var printBtn = document.getElementById("printBtn");
    if (printBtn) printBtn.addEventListener("click", function () { window.print(); });
  });
})();
"""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
# (``_tier_class``/``_status_class``/``_acceptance_class``/``_scaling_class``
# and ``_TIER_COLORS`` now live in ``report_style.py`` and are imported above
# so both this module and ``charts.py`` share one definition.)


def _evidence_stage_label(evidence_stage: Optional[str]) -> str:
    """Bilingual, human-readable label for AggregateRecord.evidence_stage --
    used by the leaderboard's "Evidence stage" column so a reader never
    confuses "passed a smoke gate" with "validated suitable for daily-runner
    delegation" (see rubric.evidence_stage()/pilot-review remediation)."""

    labels = {
        "gate_only": "Gate bestanden (nur) / Gate-passed (only)",
        "partial_suite": "Teil-Suite / Partial suite",
        "full_suite": "Voll-Suite / Full suite",
    }
    return labels.get(evidence_stage or "full_suite", str(evidence_stage))


def _render_leaderboard_table(
    aggregates: Sequence[AggregateRecord], table_id: str
) -> str:
    """Render the ranked leaderboard -- hard-gated ("not-usable") model-runs
    are never listed here (see :func:`_render_excluded_table`): speed/TPS
    must never let an unusable model appear ranked alongside usable ones.
    """

    ranked = [a for a in aggregates if not a.hard_gate_failed]
    if not ranked:
        return (
            f'<p class="hint">{bi("Keine Aggregatdaten vorhanden.", "No aggregate data available.")}</p>'
        )
    ordered = sorted(
        ranked,
        key=lambda a: (a.overall_score is None, -(a.overall_score or 0.0)),
    )
    rows = []
    for agg in ordered:
        rows.append(
            "<tr>"
            f'<td>{esc(agg.track)}</td>'
            f'<td>{esc(agg.provider)}</td>'
            f'<td>{esc(agg.backend)}</td>'
            f'<td>{esc(agg.model)}</td>'
            f'<td data-sort-value="{fmt(agg.overall_score)}">{fmt(agg.overall_score)}</td>'
            f'<td data-sort-value="{fmt(agg.deterministic_score_mean)}">{fmt(agg.deterministic_score_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.reviewer_score_mean)}">{fmt(agg.reviewer_score_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.collaboration_score_mean)}">{fmt(agg.collaboration_score_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.success_rate_percent)}">{fmt(agg.success_rate_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.elapsed_seconds_mean)}">{fmt(agg.elapsed_seconds_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.tokens_per_second_mean)}">{fmt(agg.tokens_per_second_mean)}</td>'
            f'<td><span class="tier {_tier_class(agg.suitability_tier)}">{esc(agg.suitability_tier)}</span></td>'
            f'<td>{esc(_evidence_stage_label(agg.evidence_stage))}</td>'
            f'<td>{esc(agg.recommendation)}</td>'
            "</tr>"
        )
    excluded_count = len(aggregates) - len(ranked)
    excluded_note = ""
    if excluded_count:
        excluded_note = (
            f'<p class="hint hint-warning">{bi("Hinweis", "Note")}: '
            f'{bi(f"{excluded_count} Modell-Lauf/Laeufe wurden wegen des harten Akzeptanz-Gates ausgeschlossen und erscheinen nicht in diesem Ranking (siehe Abschnitt \'Ausgeschlossen\' unten).", f"{excluded_count} model-run(s) were excluded by the hard acceptance gate and do not appear in this ranking (see the \'Excluded\' section below).")}</p>'
        )
    return (
        excluded_note
        + f'<div class="filter-bar"><input type="search" class="table-filter" '
        f'data-filter-target="{esc(table_id)}" placeholder="'
        f'{esc("Filtern nach Modell, Backend, Track ...")}" aria-label="'
        f'{esc("Leaderboard filtern")}"></div>'
        f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Track", "Track")}</th>'
        f'<th>{bi("Anbieter", "Provider")}</th>'
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Gesamt-Score", "Overall score")}</th>'
        f'<th>{bi("Deterministisch", "Deterministic")}</th>'
        f'<th>{bi("Reviewer", "Reviewer")}</th>'
        f'<th>{bi("Kollaboration", "Collaboration")}</th>'
        f'<th>{bi("Erfolgsquote", "Success rate")}</th>'
        f'<th>{bi("Ø Laufzeit (s)", "Avg elapsed (s)")}</th>'
        f'<th>{bi("Ø Tokens/s", "Avg tokens/s")}</th>'
        f'<th>{bi("Eignung", "Suitability")}</th>'
        f'<th>{bi("Evidenzstufe", "Evidence stage")}</th>'
        f'<th>{bi("Empfehlung", "Recommendation")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_excluded_table(
    aggregates: Sequence[AggregateRecord], table_id: str
) -> str:
    """Render model-runs excluded by the hard acceptance gate.

    This is a deliberately separate section from the leaderboard: speed must
    never compensate for unusable quality, so an excluded candidate must
    never be visually ranked next to (or above) an accepted, usable one --
    regardless of how fast or how high a raw composite score it produced.
    """

    excluded = [a for a in aggregates if a.hard_gate_failed]
    if not excluded:
        return (
            f'<p class="hint">{bi("Keine Modell-Laeufe wurden ausgeschlossen -- alle bestanden das harte Akzeptanz-Gate.", "No model-runs were excluded -- all passed the hard acceptance gate.")}</p>'
        )
    ordered = sorted(excluded, key=lambda a: (a.backend, a.model))
    rows = []
    for agg in ordered:
        rows.append(
            "<tr>"
            f'<td>{esc(agg.track)}</td>'
            f'<td>{esc(agg.backend)}</td>'
            f'<td>{esc(agg.model)}</td>'
            f'<td data-sort-value="{fmt(agg.acceptance_rate_percent)}">{fmt(agg.acceptance_rate_percent)}%</td>'
            f'<td data-sort-value="{agg.unsafe_sample_count}">{agg.unsafe_sample_count}</td>'
            f'<td data-sort-value="{agg.unresolved_task_count}">{agg.unresolved_task_count}</td>'
            f'<td data-sort-value="{fmt(agg.elapsed_seconds_mean)}">{fmt(agg.elapsed_seconds_mean)}</td>'
            f'<td>{esc(agg.hard_gate_reasons) if agg.hard_gate_reasons else NA}</td>'
            "</tr>"
        )
    return (
        f'<p class="hint hint-warning">{bi("Diese Modell-Laeufe sind NICHT NUTZBAR und werden unabhaengig von Geschwindigkeit/Tokens-pro-Sekunde vom Ranking ausgeschlossen.", "These model-runs are NOT USABLE and are excluded from ranking regardless of speed/tokens-per-second.")}</p>'
        f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Track", "Track")}</th>'
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Akzeptanzquote", "Acceptance rate")}</th>'
        f'<th>{bi("Unsichere Proben", "Unsafe samples")}</th>'
        f'<th>{bi("Nie akzeptierte Tasks", "Never-accepted tasks")}</th>'
        f'<th>{bi("Ø Laufzeit (s), nur zur Information", "Avg elapsed (s), informational only")}</th>'
        f'<th>{bi("Ausschlussgrund", "Exclusion reason")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_samples_table(samples: Sequence[SampleRecord], table_id: str) -> str:
    if not samples:
        return f'<p class="hint">{bi("Keine Sample-Daten vorhanden.", "No sample data available.")}</p>'
    rows = []
    for s in samples:
        reasons_title = f' title="{esc(s.acceptance_reasons)}"' if s.acceptance_reasons else ""
        rows.append(
            "<tr>"
            f'<td>{esc(s.task_id)}</td>'
            f'<td>{esc(s.task_name)}</td>'
            f'<td>{esc(s.task_category)}</td>'
            f'<td>{esc(s.track)}</td>'
            f'<td>{esc(s.backend)}</td>'
            f'<td>{esc(s.model)}</td>'
            f'<td class="{_status_class(s.status)}">{esc(s.status)}</td>'
            f'<td data-sort-value="{esc(s.acceptance_status)}"><span class="tier {_acceptance_class(s.acceptance_status)}"{reasons_title}>{esc(s.acceptance_status)}</span></td>'
            f'<td data-sort-value="{fmt(s.elapsed_seconds)}">{fmt(s.elapsed_seconds)}</td>'
            f'<td data-sort-value="{fmt(s.tokens_per_second)}">{fmt(s.tokens_per_second)}</td>'
            f'<td data-sort-value="{fmt(s.deterministic_score)}">{fmt(s.deterministic_score)}</td>'
            f'<td data-sort-value="{fmt(s.reviewer_score)}">{fmt(s.reviewer_score)}</td>'
            f'<td data-sort-value="{fmt(s.composite_score)}">{fmt(s.composite_score)}</td>'
            f'<td>{s.retry_count}/{s.iteration_index}</td>'
            f'<td>{render_artifact_link(s.artifact_path)}</td>'
            f'<td>{esc(s.reviewer_notes) if s.reviewer_notes else NA}</td>'
            "</tr>"
        )
    return (
        f'<div class="filter-bar"><input type="search" class="table-filter" '
        f'data-filter-target="{esc(table_id)}" placeholder="'
        f'{esc("Filtern nach Task, Modell, Status ...")}" aria-label="'
        f'{esc("Sample-Tabelle filtern")}"></div>'
        f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Task-ID", "Task ID")}</th>'
        f'<th>{bi("Task-Name", "Task name")}</th>'
        f'<th>{bi("Kategorie", "Category")}</th>'
        f'<th>{bi("Track", "Track")}</th>'
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Status", "Status")}</th>'
        f'<th>{bi("Akzeptanz", "Acceptance")}</th>'
        f'<th>{bi("Laufzeit (s)", "Elapsed (s)")}</th>'
        f'<th>{bi("Tokens/s", "Tokens/s")}</th>'
        f'<th>{bi("Determ.", "Determ.")}</th>'
        f'<th>{bi("Reviewer", "Reviewer")}</th>'
        f'<th>{bi("Gesamt", "Composite")}</th>'
        f'<th>{bi("Retry/Iter.", "Retry/Iter.")}</th>'
        f'<th>{bi("Artefakt", "Artifact")}</th>'
        f'<th>{bi("Notizen", "Notes")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_errors_table(samples: Sequence[SampleRecord], table_id: str) -> str:
    errors = [s for s in samples if s.status in ("error", "timeout")]
    if not errors:
        return f'<p class="hint">{bi("Keine Fehler in diesem Lauf.", "No errors in this run.")}</p>'
    rows = []
    for s in errors:
        rows.append(
            "<tr>"
            f'<td>{esc(s.task_id)}</td>'
            f'<td>{esc(s.backend)}</td>'
            f'<td>{esc(s.model)}</td>'
            f'<td class="{_status_class(s.status)}">{esc(s.status)}</td>'
            f'<td>{esc(s.system_error_code) if s.system_error_code else NA}</td>'
            f'<td>{esc(s.system_error_message) if s.system_error_message else NA}</td>'
            "</tr>"
        )
    return (
        f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Task-ID", "Task ID")}</th>'
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Status", "Status")}</th>'
        f'<th>{bi("Fehlercode", "Error code")}</th>'
        f'<th>{bi("Fehlermeldung", "Error message")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_resource_table(aggregates: Sequence[AggregateRecord], table_id: str) -> str:
    if not aggregates:
        return f'<p class="hint">{bi("Keine Ressourcendaten vorhanden.", "No resource data available.")}</p>'
    rows = []
    for agg in aggregates:
        rows.append(
            "<tr>"
            f'<td>{esc(agg.backend)}</td>'
            f'<td>{esc(agg.model)}</td>'
            f'<td data-sort-value="{fmt(agg.cpu_avg_percent_mean)}">{fmt(agg.cpu_avg_percent_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.cpu_max_percent_max)}">{fmt(agg.cpu_max_percent_max)}</td>'
            f'<td data-sort-value="{fmt(agg.ram_avg_mb_mean)}">{fmt(agg.ram_avg_mb_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.ram_max_mb_max)}">{fmt(agg.ram_max_mb_max)}</td>'
            f'<td data-sort-value="{fmt(agg.gpu_avg_percent_mean)}">{fmt(agg.gpu_avg_percent_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.gpu_max_percent_max)}">{fmt(agg.gpu_max_percent_max)}</td>'
            f'<td data-sort-value="{fmt(agg.vram_avg_mb_mean)}">{fmt(agg.vram_avg_mb_mean)}</td>'
            f'<td data-sort-value="{fmt(agg.vram_max_mb_max)}">{fmt(agg.vram_max_mb_max)}</td>'
            "</tr>"
        )
    return (
        f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("CPU Ø%", "CPU avg%")}</th>'
        f'<th>{bi("CPU Max%", "CPU max%")}</th>'
        f'<th>{bi("RAM Ø MB", "RAM avg MB")}</th>'
        f'<th>{bi("RAM Max MB", "RAM max MB")}</th>'
        f'<th>{bi("GPU Ø%", "GPU avg%")}</th>'
        f'<th>{bi("GPU Max%", "GPU max%")}</th>'
        f'<th>{bi("VRAM Ø MB", "VRAM avg MB")}</th>'
        f'<th>{bi("VRAM Max MB", "VRAM max MB")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_phase_timing_table(aggregates: Sequence[AggregateRecord], table_id: str) -> str:
    """Render the phase-attribution breakdown: exclusive critical-path
    percentages (always summing to ~100% by construction, see
    :mod:`agent_helper_eval.phase_timing`) plus the separate, non-exclusive
    utilization ratios and overlap/unaccounted-time diagnostics.

    Model-runs with ``phase_timing_sample_count == 0`` (no instrumented
    samples -- always true for Copilot task-agent references, whose
    internal timings are not observable) are shown with an explicit
    "not instrumented" row rather than being silently omitted.
    """

    instrumented = [a for a in aggregates if a.phase_timing_sample_count > 0]
    not_instrumented = [a for a in aggregates if a.phase_timing_sample_count == 0]
    if not aggregates:
        return f'<p class="hint">{bi("Keine Aggregatdaten vorhanden.", "No aggregate data available.")}</p>'
    rows = []
    for agg in instrumented:
        rows.append(
            "<tr>"
            f'<td>{esc(agg.backend)}</td>'
            f'<td>{esc(agg.model)}</td>'
            f'<td data-sort-value="{agg.phase_timing_sample_count}">{agg.phase_timing_sample_count}</td>'
            f'<td data-sort-value="{fmt(agg.llm_critical_path_percent)}">{fmt(agg.llm_critical_path_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.tool_critical_path_percent)}">{fmt(agg.tool_critical_path_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.queue_idle_critical_path_percent)}">{fmt(agg.queue_idle_critical_path_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.orchestration_critical_path_percent)}">{fmt(agg.orchestration_critical_path_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.phase_timing_overlap_ratio_percent)}">{fmt(agg.phase_timing_overlap_ratio_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.phase_timing_unaccounted_ratio_percent)}">{fmt(agg.phase_timing_unaccounted_ratio_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.model_busy_percent)}">{fmt(agg.model_busy_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.gpu_active_percent)}">{fmt(agg.gpu_active_percent)}%</td>'
            f'<td data-sort-value="{fmt(agg.tool_runner_busy_percent)}">{fmt(agg.tool_runner_busy_percent)}%</td>'
            "</tr>"
        )
    for agg in not_instrumented:
        rows.append(
            "<tr class=\"na-row\">"
            f'<td>{esc(agg.backend)}</td>'
            f'<td>{esc(agg.model)}</td>'
            f'<td data-sort-value="0">0</td>'
            f'<td colspan="9">{bi("Nicht instrumentiert (z. B. GitHub Copilot Task-Agent: interne Timings nicht beobachtbar) &mdash; N/A statt geschaetzt.", "Not instrumented (e.g. GitHub Copilot task agent: internal timings not observable) &mdash; N/A, never estimated.")}</td>'
            "</tr>"
        )
    methodology_text = bi(
        "Methodik: Die vier exklusiven Kritischer-Pfad-Anteile (LLM, Tools/Tests, "
        "Warteschlange/Leerlauf, Orchestrierung) werden gegen ihre eigene Summe "
        "normiert und ergeben daher immer exakt 100%, unabhaengig von Ueberlappung. "
        "'Ueberlappung' und 'Nicht erfasst' sind separate Diagnosewerte (nicht Teil "
        "der 100%), die die naive Summe der Phasenzeiten mit der tatsaechlichen "
        "Gesamtlaufzeit vergleichen. Die Auslastungsquoten (Modell/GPU/Tool-Runner) "
        "sind NICHT exklusiv und koennen bei echter Nebenlaeufigkeit ueber 100% liegen.",
        "Methodology: the four exclusive critical-path shares (LLM, tools/tests, "
        "queue/idle, orchestration) are normalized against their own sum and "
        "therefore always add up to exactly 100%, regardless of overlap. "
        "'Overlap' and 'Unaccounted' are separate diagnostics (not part of the "
        "100%) comparing the naive phase-time sum to actual total wall time. The "
        "utilization ratios (model/GPU/tool-runner busy%) are NOT exclusive and "
        "can legitimately exceed 100% under real concurrency.",
    )
    methodology = f'<p class="hint">{methodology_text}</p>'
    return (
        methodology
        + f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Instrumentierte Samples", "Instrumented samples")}</th>'
        f'<th>{bi("LLM %", "LLM %")}</th>'
        f'<th>{bi("Tools/Tests %", "Tools/tests %")}</th>'
        f'<th>{bi("Warteschlange/Leerlauf %", "Queue/idle %")}</th>'
        f'<th>{bi("Orchestrierung %", "Orchestration %")}</th>'
        f'<th>{bi("Ueberlappung %", "Overlap %")}</th>'
        f'<th>{bi("Nicht erfasst %", "Unaccounted %")}</th>'
        f'<th>{bi("Modell-Auslastung %", "Model busy %")}</th>'
        f'<th>{bi("GPU-Auslastung %", "GPU active %")}</th>'
        f'<th>{bi("Tool-Runner-Auslastung %", "Tool runner busy %")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_capacity_profile_table(
    capacity_profiles: Sequence[CapacityProfileRecord], table_id: str
) -> str:
    """Render the post-quality-gate concurrency/capacity-profile table
    (concurrency levels 1/2/4 against the SAME already hard-gate-accepted
    local model).

    Every row rendered here already satisfied both mandatory preflight
    checks (:data:`preflight_quality_gate_passed` and
    :data:`preflight_memory_safety_checked` are schema-enforced ``True`` --
    see :func:`agent_helper_eval.schema.validate_capacity_profile`); this is
    restated explicitly in the caveat below since it is the single most
    important safety property of this section.
    """

    if not capacity_profiles:
        empty_text = bi(
            "Kein Kapazitaets-/Nebenlaeufigkeitsprofil fuer diese Kampagne vorhanden "
            "(wird erst nach dem harten Akzeptanz-Gate und einer expliziten "
            "Speicher-Sicherheitspruefung erhoben).",
            "No capacity/concurrency profile available for this campaign (only "
            "collected after the hard acceptance gate and an explicit "
            "memory-safety check).",
        )
        return f'<p class="hint">{empty_text}</p>'
    ordered = sorted(capacity_profiles, key=lambda c: (c.backend, c.model, c.concurrency_level))
    rows = []
    for cp in ordered:
        concurrency_cell = str(cp.concurrency_level)
        if cp.concurrency_level == 1:
            # Concurrency=1 is always the mandatory scaling baseline every
            # higher concurrency level's efficiency_percent is normalized
            # against (see capacity_profile.compute_throughput_efficiency_percent)
            # -- labeled explicitly here so the "double baseline" (model
            # comparison vs. concurrency-scaling comparison) is visible in
            # the table itself, not only in the methodology text.
            concurrency_cell += f' <span class="chip">{bi("Baseline", "baseline")}</span>'
        rows.append(
            "<tr>"
            f'<td>{esc(cp.backend)}</td>'
            f'<td>{esc(cp.model)}</td>'
            f'<td data-sort-value="{cp.concurrency_level}">{concurrency_cell}</td>'
            f'<td data-sort-value="{fmt(cp.aggregate_tokens_per_second)}">{fmt(cp.aggregate_tokens_per_second)}</td>'
            f'<td data-sort-value="{fmt(cp.per_request_tokens_per_second_mean)}">{fmt(cp.per_request_tokens_per_second_mean)}</td>'
            f'<td data-sort-value="{fmt(cp.per_request_tokens_per_second_p95)}">{fmt(cp.per_request_tokens_per_second_p95)}</td>'
            f'<td data-sort-value="{fmt(cp.queue_seconds_mean)}">{fmt(cp.queue_seconds_mean)}</td>'
            f'<td data-sort-value="{fmt(cp.latency_seconds_p95)}">{fmt(cp.latency_seconds_p95)}</td>'
            f'<td data-sort-value="{fmt(cp.gpu_avg_percent)}">{fmt(cp.gpu_avg_percent)}</td>'
            f'<td data-sort-value="{fmt(cp.vram_avg_mb)}">{fmt(cp.vram_avg_mb)}</td>'
            f'<td data-sort-value="{fmt(cp.vram_headroom_mb)}">{fmt(cp.vram_headroom_mb)}</td>'
            f'<td data-sort-value="{cp.error_count}">{cp.error_count}</td>'
            f'<td data-sort-value="{fmt(cp.throughput_efficiency_percent)}">{fmt(cp.throughput_efficiency_percent)}%</td>'
            f'<td><span class="tier {_scaling_class(cp.scaling_classification)}">{esc(cp.scaling_classification)}</span></td>'
            f'<td data-sort-value="{fmt(cp.time_to_accepted_result_seconds)}">{fmt(cp.time_to_accepted_result_seconds)}</td>'
            "</tr>"
        )
    caveat_text = bi(
        "Hinweis: Jede hier gezeigte Zeile hat bereits das harte Akzeptanz-Gate bei "
        "Einzel-Anfrage bestanden und wurde vor der Nebenlaeufigkeitsmessung explizit "
        "auf Speichersicherheit (VRAM-Freiraum) geprueft -- siehe "
        "can_start_concurrency_profile(). Ziel: klaeren, ob mehr gleichzeitige lokale "
        "Helfer-Agenten den Durchsatz wirklich verbessern oder nur zu "
        "Warteschlangen/Contention (Thrashing) fuehren.",
        "Note: every row shown here already passed the hard acceptance gate at "
        "single-request concurrency and was explicitly checked for memory safety "
        "(VRAM headroom) before concurrency profiling started -- see "
        "can_start_concurrency_profile(). Purpose: determine whether more "
        "simultaneous local helper agents genuinely improve throughput, or merely "
        "cause queueing/contention (thrashing).",
    )
    caveat = f'<p class="hint">{caveat_text}</p>'
    return (
        caveat
        + f'<div class="table-scroll"><table class="data-table sortable" id="{esc(table_id)}">'
        "<thead><tr>"
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Nebenlaeufigkeit", "Concurrency")}</th>'
        f'<th>{bi("Gesamt Tokens/s", "Aggregate tokens/s")}</th>'
        f'<th>{bi("Ø Tokens/s je Anfrage", "Mean tokens/s per request")}</th>'
        f'<th>{bi("P95 Tokens/s je Anfrage", "P95 tokens/s per request")}</th>'
        f'<th>{bi("Ø Warteschlange (s)", "Mean queue (s)")}</th>'
        f'<th>{bi("P95 Latenz (s)", "P95 latency (s)")}</th>'
        f'<th>{bi("GPU Ø%", "GPU avg%")}</th>'
        f'<th>{bi("VRAM Ø MB", "VRAM avg MB")}</th>'
        f'<th>{bi("VRAM-Freiraum MB", "VRAM headroom MB")}</th>'
        f'<th>{bi("Fehler", "Errors")}</th>'
        f'<th>{bi("Effizienz %", "Efficiency %")}</th>'
        f'<th>{bi("Skalierung", "Scaling")}</th>'
        f'<th>{bi("Zeit bis akzeptiertes Ergebnis (s)", "Time to accepted result (s)")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


# NOTE: the quality-vs-speed chart (with Pareto frontier) and every other
# inline-SVG visualization now live in ``charts.py`` (see
# ``charts.render_quality_speed_chart`` and friends), which is imported at
# the top of this module. Kept out of this file so this module stays about
# page/table assembly, not chart math.


def _campaign_summary_chips(data: CampaignReportData) -> str:
    tracks = sorted({a.track for a in data.aggregates}) or sorted({s.track for s in data.samples})
    benchmark_sets = sorted({a.benchmark_set for a in data.aggregates}) or sorted(
        {s.benchmark_set for s in data.samples}
    )
    chips = [f'<span class="chip">{esc(t)}</span>' for t in tracks]
    chips += [f'<span class="chip">{esc(b)}</span>' for b in benchmark_sets]
    chips.append(f'<span class="chip">{len(data.samples)} samples</span>')
    chips.append(f'<span class="chip">{len(data.aggregates)} model-runs</span>')
    if data.capacity_profiles:
        chips.append(f'<span class="chip">{len(data.capacity_profiles)} capacity-profile rows</span>')
    return "".join(chips)


def _render_campaign(data: CampaignReportData, open_by_default: bool, index: int) -> str:
    open_attr = " open" if open_by_default else ""
    safe_suffix = f"c{index}"
    body = []
    body.append(f'<h3>{bi("Modell-Leaderboard", "Model leaderboard")}</h3>')
    body.append(_render_leaderboard_table(data.aggregates, f"leaderboard-{safe_suffix}"))
    body.append(f'<h3>{bi("Ausgeschlossen (hartes Akzeptanz-Gate)", "Excluded (hard acceptance gate)")}</h3>')
    body.append(_render_excluded_table(data.aggregates, f"excluded-{safe_suffix}"))
    body.append(f'<h3>{bi("Akzeptanz-Gate-Trichter", "Acceptance-gate funnel")}</h3>')
    body.append(charts.render_quality_gate_funnel(data.samples, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Qualitaet vs. Geschwindigkeit", "Quality vs. speed")}</h3>')
    body.append(charts.render_quality_speed_chart(data.aggregates))
    body.append(f'<h3>{bi("Zeit bis akzeptiertes Ergebnis", "Time to accepted result")}</h3>')
    body.append(charts.render_time_to_accepted_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Task-Qualitaet je Modell (Heatmap)", "Task quality by model (heatmap)")}</h3>')
    body.append(charts.render_task_model_heatmap(data.samples, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Latenz-Perzentile (P50/P95/P99)", "Latency percentiles (P50/P95/P99)")}</h3>')
    body.append(charts.render_latency_percentile_chart(data.aggregates, data.samples, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Ressourcenmetriken", "Resource metrics")}</h3>')
    body.append(_render_resource_table(data.aggregates, f"resources-{safe_suffix}"))
    body.append(f'<h4>{bi("CPU/GPU-Auslastung", "CPU/GPU utilization")}</h4>')
    body.append(charts.render_cpu_gpu_utilization_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h4>{bi("RAM/VRAM-Nutzung", "RAM/VRAM usage")}</h4>')
    body.append(charts.render_ram_vram_utilization_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Phasen-Attribution &amp; kritischer Pfad", "Phase attribution &amp; critical path")}</h3>')
    body.append(_render_phase_timing_table(data.aggregates, f"phase-timing-{safe_suffix}"))
    body.append(charts.render_critical_path_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h4>{bi("Nicht-exklusive Auslastungsquoten", "Non-exclusive utilization ratios")}</h4>')
    body.append(charts.render_utilization_ratio_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Kapazitaets-/Nebenlaeufigkeitsprofil", "Capacity/concurrency profile")}</h3>')
    body.append(_render_capacity_profile_table(data.capacity_profiles, f"capacity-{safe_suffix}"))
    body.append(charts.render_concurrency_scaling_chart(data.capacity_profiles, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Zuverlaessigkeit &amp; Fehler", "Reliability &amp; errors")}</h3>')
    body.append(charts.render_reliability_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Iteration &amp; Rework", "Iteration &amp; rework")}</h3>')
    body.append(charts.render_iteration_rework_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Routing-/Kosten-Szenario (illustrativ)", "Routing/cost scenario (illustrative)")}</h3>')
    body.append(charts.render_routing_scenario_chart(data.aggregates, id_prefix=safe_suffix))
    body.append(f'<h3>{bi("Task-Detail (Drilldown)", "Task detail (drilldown)")}</h3>')
    body.append(_render_samples_table(data.samples, f"samples-{safe_suffix}"))
    body.append(f'<h3>{bi("Fehler", "Errors")}</h3>')
    body.append(_render_errors_table(data.samples, f"errors-{safe_suffix}"))

    title = data.title or data.campaign_id
    return (
        f'<details class="campaign"{open_attr}>'
        f"<summary><span>{esc(title)} "
        f'<span class="chip">{esc(data.generated_at)}</span></span>'
        f"<span>{_campaign_summary_chips(data)}</span></summary>"
        f'<div class="campaign-body">' + "".join(body) + "</div></details>"
    )


def _feasibility_badge(
    projection: Optional[FeasibilityProjection], *, allow_measured: bool
) -> str:
    """Render one feasibility projection as a fits-value plus a prominent
    measured-vs-projection confidence badge.

    ``allow_measured`` mirrors :func:`model_inventory.validate_feasibility`:
    hardware that is actually owned (the current 12 GB notebook GPU) may
    show a green "measured" badge; hardware that is not yet owned (the
    hypothetical RTX 5090 32 GB) is schema-forbidden from ever claiming
    "measured" confidence at load time (:func:`model_inventory.validate_model_spec`).
    As defense in depth against a record that somehow still carries
    ``confidence="measured"`` for that column (e.g. constructed directly,
    bypassing validation), this function *never* renders the "measured"
    badge when ``allow_measured`` is ``False`` -- such a claim is
    defensively downgraded to the projection/estimate bucket instead of
    ever being displayed as a measured result.
    """

    if projection is None:
        return f'<span class="hint">{NA}</span>'
    fits_text = (
        bi("ja", "yes")
        if projection.fits is True
        else bi("nein", "no") if projection.fits is False else bi("unbekannt", "unknown")
    )
    confidence = projection.confidence
    if confidence == "measured" and not allow_measured:
        confidence = "projected"
    if confidence == "measured":
        badge_class = "tier-measured"
        badge_text = bi("GEMESSEN", "MEASURED")
    elif confidence in ("estimated", "projected"):
        badge_class = "tier-projection"
        badge_text = bi("PROJEKTION/SCHAETZUNG", "PROJECTION/ESTIMATE")
    else:
        badge_class = "tier-unknown"
        badge_text = bi("UNBEKANNT", "UNKNOWN")
    assumptions = f'<div class="hint">{esc(projection.assumptions)}</div>' if projection.assumptions else ""
    notes = f'<div class="hint">{esc(projection.notes)}</div>' if projection.notes else ""
    return (
        f"{fits_text} <span class=\"tier {badge_class}\">{badge_text}</span>{assumptions}{notes}"
    )


def _render_model_inventory_table(specs: Sequence[ModelSpec]) -> str:
    """Render the report-wide (not per-campaign) model inventory/feasibility
    table -- what the campaign configuration says COULD be evaluated, with
    a prominent, unambiguous measured-vs-projection distinction for every
    VRAM-fit claim (see :func:`_feasibility_badge`). Never claims a
    measured result for hardware that was not actually tested.
    """

    if not specs:
        return (
            f'<p class="hint">{bi("Kein Modell-Inventar fuer diesen Bericht geladen.", "No model inventory loaded for this report.")}</p>'
        )
    ordered = sorted(specs, key=lambda s: (s.provider, s.backend, s.model_id))
    rows = []
    for spec in ordered:
        params = fmt(spec.params_billion, 1) if spec.params_billion is not None else NA
        active_params = (
            fmt(spec.active_params_billion, 1) if spec.active_params_billion is not None else NA
        )
        rows.append(
            "<tr>"
            f"<td>{esc(spec.provider)}</td>"
            f"<td>{esc(spec.backend)}</td>"
            f"<td>{esc(spec.display_name)}</td>"
            f"<td>{esc(spec.architecture)}</td>"
            f'<td data-sort-value="{fmt(spec.params_billion, 1)}">{params}</td>'
            f'<td data-sort-value="{fmt(spec.active_params_billion, 1)}">{active_params}</td>'
            f"<td>{esc(spec.quantization) if spec.quantization else NA}</td>"
            f'<td data-sort-value="{fmt(spec.size_gb_on_disk, 1)}">{fmt(spec.size_gb_on_disk, 1) if spec.size_gb_on_disk is not None else NA}</td>'
            f"<td>{_feasibility_badge(spec.fits_12gb_vram, allow_measured=True)}</td>"
            f"<td>{_feasibility_badge(spec.fits_rtx5090_32gb_projection, allow_measured=False)}</td>"
            f"<td>{esc(spec.notes) if spec.notes else NA}</td>"
            "</tr>"
        )
    return (
        f'<div class="table-scroll"><table class="data-table sortable" id="model-inventory">'
        "<thead><tr>"
        f'<th>{bi("Anbieter", "Provider")}</th>'
        f'<th>{bi("Backend", "Backend")}</th>'
        f'<th>{bi("Modell", "Model")}</th>'
        f'<th>{bi("Architektur", "Architecture")}</th>'
        f'<th>{bi("Parameter (Mrd.)", "Params (B)")}</th>'
        f'<th>{bi("Aktive Parameter (Mrd.)", "Active params (B)")}</th>'
        f'<th>{bi("Quantisierung", "Quantization")}</th>'
        f'<th>{bi("Groesse (GB)", "Size (GB)")}</th>'
        f'<th>{bi("12 GB VRAM Passung", "12 GB VRAM fit")}</th>'
        f'<th>{bi("RTX 5090 32 GB (hypothetisch)", "RTX 5090 32 GB (hypothetical)")}</th>'
        f'<th>{bi("Hinweise", "Notes")}</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _render_executive_summary(campaigns: Sequence[CampaignReportData]) -> str:
    if not campaigns:
        return f'<p>{bi("Noch keine Kampagne vorhanden.", "No campaign yet.")}</p>'
    latest = campaigns[0]
    # "Best model" is only ever chosen among candidates that passed the hard
    # acceptance gate -- a fast, high-keyword-score, or otherwise superficially
    # appealing but "not-usable" model-run must never be picked here, no
    # matter what its raw overall_score looks like.
    usable = [a for a in latest.aggregates if not a.hard_gate_failed]
    excluded = [a for a in latest.aggregates if a.hard_gate_failed]
    best = None
    for agg in usable:
        if agg.overall_score is None:
            continue
        if best is None or agg.overall_score > best.overall_score:
            best = agg
    best_heuristic_caveat_de = ""
    best_heuristic_caveat_en = ""
    if best is not None and best.reviewer_evidence_is_heuristic_only:
        # This is the direct safeguard against labeling a model "suitable"
        # on the strength of a keyword/heuristic reviewer score alone (for
        # example a legacy-imported model with a single high
        # legacy-heuristic-keyword-score sample and no deterministic
        # evidence): the tier itself is already capped below "recommended"
        # (see rubric.suitability_tier), and this caveat makes the reason
        # explicit right where the "best model" is named.
        best_heuristic_caveat_de = (
            " Achtung: Dieser Score beruht ausschliesslich auf einem "
            "heuristischen Keyword-Reviewer-Score ohne deterministischen "
            "Test oder echtes Orchestrator-/Human-Review -- dies ist KEINE "
            "validierte Eignungsaussage."
        )
        best_heuristic_caveat_en = (
            " Caution: this score rests solely on a heuristic "
            "keyword-matching reviewer score with no deterministic test or "
            "genuine orchestrator/human review -- this is NOT a validated "
            "suitability claim."
        )
    best_evidence_caveat_de = ""
    best_evidence_caveat_en = ""
    if best is not None and best.evidence_stage == "gate_only":
        # Fix for pilot-review finding #1: a connect-gate/mini-gate pass is
        # real evidence the model *works*, not evidence it is *suitable for
        # delegated daily-runner use* -- suitability_tier is already capped
        # at "gate-passed-provisional" (see rubric.evidence_stage()), and
        # this caveat makes the reason explicit right where "best model" is
        # named, so a high score can never read as "recommended"/"strong
        # candidate" on gate-only evidence alone.
        best_evidence_caveat_de = (
            " Achtung: Dies beruht nur auf einem bestandenen Connect-/"
            "Mini-Gate (eine Aufgabenkategorie, kein Reviewer-/"
            "Kollaborations-Nachweis) -- dies ist KEINE validierte "
            "Tier-1-/Daily-Runner-Eignungsaussage."
        )
        best_evidence_caveat_en = (
            " Caution: this rests only on a passed connect/mini gate (one "
            "task category, no reviewer/collaboration evidence yet) -- this "
            "is NOT a validated tier-1/daily-runner suitability claim."
        )
    elif best is not None and best.evidence_stage == "partial_suite":
        best_evidence_caveat_de = (
            " Hinweis: Die Evidenzbasis ist eine Teil-Suite (unvollstaendige "
            "Kategorieabdeckung, Reviewer- oder Kollaborationsnachweis) -- "
            "unterhalb von 'tier-1-recommended' gedeckelt."
        )
        best_evidence_caveat_en = (
            " Note: evidence base is a partial suite (incomplete category "
            "coverage, reviewer, or collaboration evidence) -- capped below "
            "'tier-1-recommended'."
        )
    best_line = (
        bi(
            f"Bestes NUTZBARES Modell im aktuellen Lauf (nach dem harten "
            f"Akzeptanz-Gate): {best.backend}/{best.model} "
            f"(Score {fmt(best.overall_score)}, {best.suitability_tier}). "
            "Diese Auswahl beruecksichtigt ausschliesslich Kandidaten, die "
            f"das Akzeptanz-Gate bestanden haben.{best_heuristic_caveat_de}{best_evidence_caveat_de}",
            f"Best USABLE model in the current run (after the hard "
            f"acceptance gate): {best.backend}/{best.model} "
            f"(score {fmt(best.overall_score)}, {best.suitability_tier}). "
            "This pick only ever considers candidates that passed the "
            f"acceptance gate.{best_heuristic_caveat_en}{best_evidence_caveat_en}",
        )
        if best is not None
        else bi(
            "Kein Modell im aktuellen Lauf hat das harte Akzeptanz-Gate "
            "bestanden und einen vollstaendigen Score erhalten.",
            "No model in the current run passed the hard acceptance gate "
            "with a complete score.",
        )
    )
    excluded_line = ""
    if excluded:
        # Include track in each name so that a model excluded in both the
        # pure_model and tool_agent tracks (two separate model-run rows)
        # does not appear as a confusing, unexplained duplicate in this list.
        names = ", ".join(esc(f"{a.backend}/{a.model} [{a.track}]") for a in excluded)
        excluded_line = (
            "<li>"
            + bi(
                f"{len(excluded)} Modell-Lauf/Laeufe wurden als NICHT NUTZBAR "
                f"eingestuft und vom Ranking ausgeschlossen: {names}.",
                f"{len(excluded)} model-run(s) were classified NOT USABLE and "
                f"excluded from ranking: {names}.",
            )
            + "</li>"
        )
    return (
        '<div class="exec-summary"><ul>'
        f"<li>{bi(f'{len(campaigns)} Kampagne(n) im Bericht.', f'{len(campaigns)} campaign(s) in this report.')}</li>"
        f"<li>{bi(f'Aktuellste Kampagne: {latest.campaign_id} ({latest.generated_at}).', f'Latest campaign: {latest.campaign_id} ({latest.generated_at}).')}</li>"
        f"<li><strong>{bi('Harte Regel', 'Hard rule')}:</strong> "
        f"{bi('Geschwindigkeit gleicht niemals mangelnde Nutzbarkeit aus. Ein Modell mit fehlgeschlagenen deterministischen Tests, unvollstaendiger/Platzhalter-Ausgabe, unsicherem Verhalten oder zu niedrigem Reviewer-Score gilt als NICHT NUTZBAR und wird unabhaengig von Tokens/Sekunde vom Ranking ausgeschlossen; Performance ist nur ein Tie-Breaker unter bereits akzeptierten, nutzbaren Ergebnissen. Kein Modell wird allein aufgrund eines Keyword- oder Geschwindigkeits-Scores als geeignet bezeichnet.', 'Speed never compensates for unusable quality. A model with failed deterministic tests, placeholder/incomplete output, unsafe behavior, or a below-threshold reviewer score is classified NOT USABLE and excluded from ranking regardless of tokens/sec; performance is only ever a tie-breaker among already-accepted, usable results. No model is ever labeled suitable based on a keyword or speed score alone.')}</li>"
        f"<li>{best_line}</li>"
        f"{excluded_line}"
        f"<li>{bi('Dies ist ein Mess-/Berichts-Framework; es werden keine Modellergebnisse behauptet, solange keine Kampagne gemessen wurde.', 'This is a measurement/reporting harness; no model results are claimed until a campaign has actually been measured.')}</li>"
        "</ul></div>"
    )


_METHODOLOGY_HTML = f"""
<div class="methodology">
  <p><strong>{bi("(0) Hartes Akzeptanz-Gate zuerst", "(0) Hard acceptance gate first")}:</strong> {bi(
      "Bevor irgendein Performance- oder Gesamt-Ranking berechnet wird, durchlaeuft "
      "jeder Versuch ein hartes Akzeptanz-Gate: fehlgeschlagene deterministische "
      "Tests, Platzhalter-/unvollstaendige Ausgabe, unsicheres Verhalten oder ein "
      "Reviewer-Score unter dem Schwellwert fuehren zu 'not_usable' -- unabhaengig "
      "davon, wie schnell oder guenstig der Versuch war. Modell-Laeufe mit zu "
      "niedriger Akzeptanzquote oder jeglichem unsicheren Verhalten gelten als "
      "GESAMT NICHT NUTZBAR und werden komplett vom Ranking ausgeschlossen. "
      "Geschwindigkeit/Tokens-pro-Sekunde ist erst danach ueberhaupt relevant -- "
      "und dann nur als Tie-Breaker unter bereits akzeptierten Ergebnissen.",
      "Before any performance or overall ranking is computed, every attempt "
      "passes through a hard acceptance gate: failed deterministic tests, "
      "placeholder/incomplete output, unsafe behavior, or a reviewer score below "
      "threshold all force 'not_usable' -- regardless of how fast or cheap the "
      "attempt was. Model-runs with too low an acceptance rate or any unsafe "
      "behavior are classified entirely NOT USABLE and excluded from ranking. "
      "Speed/tokens-per-second only becomes relevant after this gate -- and even "
      "then only as a tie-breaker among already-accepted results.",
  )}</p>
  <p>{bi(
      "Reihenfolge der Bewertung danach: (1) deterministische Korrektheits-Gates, "
      "(2) Reviewer-/Orchestrator-Bewertung, (3) Kollaborations-/Iterationseffizienz, "
      "(4) Geschwindigkeit/Ressourcen/Kosten zuletzt. Ein schnelles, aber falsches "
      "Modell kann allein durch Tokens/s nicht gewinnen: faellt das deterministische "
      "Gate durch, wird der Gesamt-Score auf den deterministischen Score gedeckelt.",
      "Scoring order after the hard gate: (1) deterministic correctness gates, "
      "(2) reviewer/orchestrator review, (3) collaboration/iteration efficiency, "
      "(4) speed/resource/cost last. A fast-but-wrong model cannot win on "
      "tokens/sec alone: a failing deterministic gate caps the overall score at "
      "the deterministic score.",
  )}</p>
  <p>{bi(
      "Eine zentrale methodische Frage: Koennen mehrere schnelle Korrekturschleifen "
      "eine einzelne langsame, qualitativ hochwertige Antwort schlagen? Dazu wird "
      "'time-to-accepted-result' (kumulierte Zeit bis zur akzeptierten Antwort) und "
      "der kumulierte Tokenverbrauch verglichen, nicht nur die Geschwindigkeit des "
      "einzelnen Versuchs. Wiederholte schnelle, aber nie akzeptierte Fehlschlaege "
      "muessen dabei schlechter bewertet werden als ein einzelner, langsamerer, "
      "aber akzeptierter Durchlauf -- genau dafuer existiert das harte "
      "Akzeptanz-Gate auf Modell-Lauf-Ebene.",
      "One central methodological question: can several fast correction loops beat "
      "one slow, high-quality pass? This is evaluated via time-to-accepted-result "
      "(cumulative time to an accepted answer) and cumulative token spend, not just "
      "single-attempt speed. Repeated fast but never-accepted failures must score "
      "worse than one slower, accepted pass -- which is exactly what the model-run "
      "-level hard acceptance gate enforces.",
  )}</p>
  <dl>
    <dt>{bi("Pure-Model-Track", "Pure-model track")}</dt>
    <dd>{bi("Ein einzelner Prompt/Antwort-Austausch ohne Werkzeugzugriff.", "A single prompt/response exchange with no tool access.")}</dd>
    <dt>{bi("Tool-Agent-Track", "Tool-agent track")}</dt>
    <dd>{bi("Eine delegierte Helper-Aufgabe, ausgefuehrt ueber ein agentisches Werkzeug mit echten Datei-/Tool-Aktionen.", "A delegated helper task executed through an agentic tool with real file/tool actions.")}</dd>
    <dt>{bi("GitHub-Copilot-Hinweis", "GitHub Copilot caveat")}</dt>
    <dd>{bi(
        "GitHub-Copilot-Task-Agent-Referenzen koennen rohe Tokens/Sekunde und "
        "Prozessressourcen nicht zuverlaessig offenlegen; solche Felder erscheinen "
        "als N/A statt geschaetzt. Pure-Model- und Tool-Agent-Vergleiche bleiben getrennt.",
        "GitHub Copilot task-agent references cannot reliably expose raw tokens/sec "
        "or process resources; those fields appear as N/A rather than estimated. "
        "Pure-model and tool-agent comparisons are kept separate.",
    )}</dd>
    <dt>{bi("Historische Daten", "Historical data")}</dt>
    <dd>{bi(
        "Importierte Legacy-Zeilen (migration_llm_bench) sind als "
        "'historical_import' markiert; Felder wie RAM in MB oder TTFT, die die "
        "Legacy-Pipeline nie erfasst hat, bleiben N/A statt geschaetzt.",
        "Imported legacy rows (migration_llm_bench) are marked "
        "'historical_import'; fields such as RAM in MB or TTFT that the legacy "
        "pipeline never captured remain N/A rather than estimated.",
    )}</dd>
    <dt>{bi("Zwei Vergleichsbasislinien", "Two comparison baselines")}</dt>
    <dd>{bi(
        "Baseline A (Modellauswahl): das 'beste nutzbare Modell' im Leaderboard wird "
        "ausschliesslich unter bereits akzeptierten Kandidaten verglichen -- niemals "
        "gegen ausgeschlossene ('not-usable') Laeufe. Baseline B (Kapazitaetsskalierung): "
        "jede Nebenlaeufigkeitszeile (2, 4 gleichzeitige Anfragen) wird explizit gegen "
        "die Einzel-Anfrage-Baseline (Nebenlaeufigkeit = 1) desselben Modells normiert "
        "(Effizienz-%, siehe Kapazitaetsprofil-Abschnitt) -- niemals gegen ein anderes "
        "Modell oder gegen eine hypothetische Hardware. Diese zwei Baselines werden nie "
        "vermischt: Modellqualitaet und Nebenlaeufigkeits-Skalierung beantworten "
        "unterschiedliche Fragen.",
        "Baseline A (model selection): the 'best usable model' in the leaderboard is "
        "compared only among already-accepted candidates -- never against excluded "
        "('not-usable') runs. Baseline B (capacity scaling): every concurrency row (2, "
        "4 simultaneous requests) is explicitly normalized against the single-request "
        "baseline (concurrency = 1) of the SAME model (efficiency %, see the capacity "
        "profile section) -- never against a different model or hypothetical hardware. "
        "These two baselines are never mixed: model quality and concurrency scaling "
        "answer different questions.",
    )}</dd>
    <dt>{bi("Gate unabhaengig vom Kosten-/Anbieter-Tier", "Gate independent of cost/provider tier")}</dt>
    <dd>{bi(
        "Das harte Akzeptanz-Gate wird identisch angewendet, unabhaengig vom "
        "Kosten-/Anbieter-Tier: ein kostenloses lokales Modell, ein bezahltes "
        "Siemens-API-Modell und ein GitHub-Copilot-Agent-Verweis durchlaufen dieselben "
        "Zero-Tolerance-Kriterien. Ein 'billiges' oder schnelles Ergebnis kann sich nicht "
        "in das Akzeptanz-Gate 'einkaufen'; Kosten/Geschwindigkeit werden erst NACH "
        "diesem Gate als Tie-Breaker betrachtet.",
        "The hard acceptance gate is applied identically regardless of cost/provider "
        "tier: a free local model, a paid Siemens API model, and a GitHub Copilot agent "
        "reference all pass through the same zero-tolerance criteria. A 'cheap' or fast "
        "result can never buy its way past the acceptance gate; cost/speed are only "
        "considered as a tie-breaker AFTER this gate.",
    )}</dd>
    <dt>{bi("Gemessen vs. Annahme/Projektion", "Measured vs. assumption/projection")}</dt>
    <dd>{bi(
        "Jede Kennzahl in diesem Bericht ist entweder eine tatsaechliche Messung "
        "(Kampagnen-Tabellen, GPU-Auslastung mit Herkunft 'measured') oder ausdruecklich "
        "als Annahme/Projektion gekennzeichnet (z. B. die Modell-Inventar-Machbarkeitstabelle "
        "oben: eine hypothetische RTX 5090 32 GB kann laut Schema niemals als 'GEMESSEN' "
        "auftreten, nur als 'PROJEKTION/SCHAETZUNG'). Keine Projektion wird je als "
        "gemessenes Ergebnis dargestellt.",
        "Every figure in this report is either an actual measurement (campaign tables, "
        "GPU utilization with 'measured' provenance) or explicitly labeled as an "
        "assumption/projection (for example the model inventory feasibility table above: "
        "a hypothetical RTX 5090 32 GB can never be schema-validated as 'MEASURED', only "
        "as 'PROJECTION/ESTIMATE'). No projection is ever presented as a measured result.",
    )}</dd>
    <dt>{bi("Kanonische Datenquelle", "Canonical data source")}</dt>
    <dd>{bi(
        "Jede Zahl in diesem Bericht wird direkt aus der kanonischen SQLite-/CSV-Quelle "
        "generiert; es werden keine Zahlen manuell in dieses Template kopiert. Das "
        "verhindert Kopier-/Einfuegefehler und Drift zwischen Rohdaten und Bericht.",
        "Every number in this report is generated directly from the canonical "
        "SQLite/CSV source; no figures are manually copied into this template. This "
        "prevents copy/paste errors and drift between raw data and report.",
    )}</dd>
  </dl>
</div>
"""


def render_report(
    campaigns: Iterable[CampaignReportData],
    *,
    generated_at: Optional[str] = None,
    expand_latest: int = 2,
    model_inventory: Sequence[ModelSpec] = (),
) -> str:
    """Render the full self-contained HTML report for a list of campaigns.

    Campaigns are sorted by ``generated_at`` descending (most recent first);
    the ``expand_latest`` most recent ones are expanded by default, older
    ones are collapsed (but present and selectable) via native
    ``<details>``.

    ``model_inventory`` is optional and report-wide (not per-campaign): it
    renders a "what could be evaluated" feasibility table (model size,
    architecture, quantization, 12 GB VRAM fit, hypothetical RTX 5090 32 GB
    projection) clearly separate from the per-campaign measured-result
    tables below it. Defaults to an empty tuple so existing callers/tests
    keep rendering the "no inventory loaded" fallback unchanged.
    """

    from .schema import utc_now_iso

    ordered = sorted(campaigns, key=lambda c: c.generated_at, reverse=True)
    generated_at = generated_at or utc_now_iso()

    campaign_html = []
    for index, campaign in enumerate(ordered):
        campaign_html.append(
            _render_campaign(campaign, open_by_default=index < expand_latest, index=index)
        )

    doc = f"""<!DOCTYPE html>
<html lang="de" data-lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Agent-Helper Evaluation Report</title>
<style>{_CSS}</style>
</head>
<body>
<header class="app-header">
  <div class="container">
    <h1>{bi("Agent-Helper Evaluationsbericht", "Agent-Helper Evaluation Report")}</h1>
    <div class="subtitle">{bi("Generiert am", "Generated at")} {esc(generated_at)} &middot; {bi("Schema", "Schema")} {esc(REPORT_TEMPLATE_VERSION)}</div>
    <div class="controls">
      <button id="langToggle" class="lang-toggle" type="button" aria-label="Switch language" aria-keyshortcuts="L">EN</button>
      <button id="printBtn" class="print-btn" type="button" aria-keyshortcuts="P">{bi("Drucken/PDF", "Print/PDF")}</button>
      <button id="fullscreenBtn" class="fullscreen-btn" type="button" aria-keyshortcuts="F">{bi("Vollbild", "Fullscreen")}</button>
      <span class="shortcut-hint">{bi("Tastenkuerzel", "Keyboard shortcuts")}: <kbd>L</kbd> {bi("Sprache", "language")} &middot; <kbd>F</kbd> {bi("Vollbild", "fullscreen")} &middot; <kbd>P</kbd> {bi("Drucken", "print")}</span>
    </div>
  </div>
</header>
<div class="container">
  <section class="panel">
    <h2>{bi("Zusammenfassung", "Executive summary")}</h2>
    {charts.render_kpi_cards(ordered[0].aggregates, ordered[0].samples) if ordered else charts.render_kpi_cards((), ())}
    {_render_executive_summary(ordered)}
  </section>
  <section class="panel">
    <h2>{bi("Modell-Inventar &amp; Machbarkeit", "Model inventory &amp; feasibility")}</h2>
    <p class="hint">{bi(
        "Diese Tabelle beschreibt, was laut Kampagnen-Inventarkonfiguration bewertet werden KOENNTE "
        "(Modellgroesse, Architektur, Quantisierung, VRAM-Passung) -- sie zeigt keine gemessenen "
        "Ergebnisse dieser Kampagne. Gemessene Ergebnisse stehen ausschliesslich in den "
        "Kampagnen-Tabellen unten.",
        "This table describes what COULD be evaluated per the campaign inventory configuration "
        "(model size, architecture, quantization, VRAM fit) -- it shows no measured results from "
        "this campaign. Measured results live exclusively in the campaign tables below.",
    )}</p>
    {_render_model_inventory_table(model_inventory)}
    {charts.render_feasibility_fit_chart(model_inventory, id_prefix="inventory")}
  </section>
  <section class="panel">
    <h2>{bi("Verlauf ueber Kampagnen", "Historical trend across campaigns")}</h2>
    <p class="hint">{bi(
        "Nutzbar-vs-nicht-nutzbar-Verteilung und bester nutzbarer Score je Kampagne, chronologisch "
        "sortiert -- dient der Einordnung des aktuellen Laufs, nicht als weiteres Ranking.",
        "Usable-vs-not-usable distribution and best usable score per campaign, sorted "
        "chronologically -- for context on the current run, not another ranking.",
    )}</p>
    {charts.render_historical_trend_chart(ordered, id_prefix="history")}
  </section>
  <section class="panel">
    <h2>{bi("Kampagnen", "Campaigns")}</h2>
    {''.join(campaign_html) if campaign_html else f'<p>{bi("Keine Kampagnen vorhanden.", "No campaigns available.")}</p>'}
  </section>
  <section class="panel methodology">
    <h2>{bi("Methodik und Vorbehalte", "Methodology and caveats")}</h2>
    {_METHODOLOGY_HTML}
  </section>
</div>
<footer class="app-footer container">
  {bi("Nur Mess-/Berichtsrahmen -- keine Modellkampagne wurde durch die Erstellung dieses Berichts ausgefuehrt.", "Harness only -- generating this report does not run any model campaign.")}
</footer>
<script>{_JS}</script>
</body>
</html>
"""
    return doc
