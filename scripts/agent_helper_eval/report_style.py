"""Shared text/style helpers used by both ``report.py`` (tables/page shell)
and ``charts.py`` (inline SVG visualizations).

Split out of ``report.py`` purely to avoid a circular import: ``charts.py``
needs :func:`esc`/:func:`fmt`/:func:`bi` and the tier color/class maps, and
``report.py`` needs to call into ``charts.py`` to render chart fragments.
Both modules import from here instead of from each other. ``report.py``
re-exports ``esc``/``fmt``/``bi`` at module level (``from .report_style
import esc, fmt, bi``) so existing call sites and tests that do
``from agent_helper_eval.report import esc`` keep working unchanged.
"""

from __future__ import annotations

import html

from .schema import NA

__all__ = [
    "esc",
    "fmt",
    "bi",
    "TIER_COLORS",
    "tier_css_class",
    "scaling_css_class",
    "status_css_class",
    "acceptance_css_class",
]


def esc(value: object) -> str:
    """HTML-escape any value for safe interpolation into markup or attributes."""

    if value is None:
        return NA
    return html.escape(str(value), quote=True)


def fmt(value: object, digits: int = 2) -> str:
    """Format a value for display, using the literal N/A sentinel for ``None``."""

    if value is None:
        return NA
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def bi(de: str, en: str) -> str:
    """Render a bilingual (DE default-visible, EN toggle) inline text fragment."""

    return f'<span class="i18n-de">{esc(de)}</span><span class="i18n-en">{esc(en)}</span>'


#: Suitability-tier -> hex color, shared by every chart/badge that colors a
#: model-run by tier. ``not-usable`` is a deliberately dark, distinct red
#: (never the same shade as ``tier-3-not-recommended``) so a hard-gate
#: exclusion never visually blends in with a merely low-ranked-but-usable
#: result.
TIER_COLORS = {
    "not-usable": "#742a2a",
    "gate-passed-provisional": "#2b6cb0",
    "tier-1-recommended": "#2f855a",
    "tier-2-conditional": "#c05621",
    "tier-3-not-recommended": "#c53030",
    "insufficient-data": "#718096",
}


def tier_css_class(tier: str) -> str:
    return {
        "not-usable": "tier-notusable",
        "gate-passed-provisional": "tier-provisional",
        "tier-1-recommended": "tier-1",
        "tier-2-conditional": "tier-2",
        "tier-3-not-recommended": "tier-3",
        "insufficient-data": "tier-na",
    }.get(tier, "tier-na")


def scaling_css_class(classification: str) -> str:
    # NOTE: fixed while moving this function out of report.py -- the
    # previous inline version mapped into suitability-tier *names* (e.g.
    # "tier-1-recommended") instead of the actual CSS classes defined in
    # ``_CSS`` (``tier-1``/``tier-2``/``tier-3``/``tier-na``), so the
    # capacity-profile table's scaling badge silently rendered unstyled
    # (no matching CSS rule). Corrected to reuse the real tier classes.
    return {
        "scales-well": "tier-1",
        "diminishing-returns": "tier-2",
        "thrashing-or-no-benefit": "tier-3",
        "insufficient-data": "tier-na",
    }.get(classification, "tier-na")


def status_css_class(status: str) -> str:
    return f"status-{esc(status)}"


def acceptance_css_class(acceptance_status: str) -> str:
    return {
        "accepted": "tier-1",
        "not_usable": "tier-notusable",
        "not_evaluated": "tier-na",
    }.get(acceptance_status, "tier-na")
