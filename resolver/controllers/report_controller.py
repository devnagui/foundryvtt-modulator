from __future__ import annotations

from ..report_v3 import render_html_report_v3
from ..report_view_v3 import build_report_view_v3


def attach_report_views(payload: dict) -> dict:
    payload.setdefault("reportViews", {})["v3"] = build_report_view_v3(payload)
    return payload


def render_report_html(payload: dict) -> str:
    return render_html_report_v3(payload)
