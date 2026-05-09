from __future__ import annotations

from ..report_v2 import render_html_report_v2
from ..report_v3 import render_html_report_v3
from ..report_view_v2 import build_report_view_v2


def attach_report_views(payload: dict) -> dict:
    payload.setdefault("reportViews", {})["v2"] = build_report_view_v2(payload)
    return payload


def render_report_html(payload: dict) -> str:
    return render_html_report_v2(payload)


def render_report_html_v3(payload: dict) -> str:
    return render_html_report_v3(payload)
