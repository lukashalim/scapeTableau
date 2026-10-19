"""Dashboard zones → printable SVG/HTML wireframes."""

from __future__ import annotations

from html import escape

from engine.schema import DashboardInfo, DashboardZone

_SCALE = 100_000


def _boxes(zones: list[DashboardZone], acc: list[DashboardZone]) -> None:
    for zone in zones:
        acc.append(zone)
        if zone.children:
            _boxes(zone.children, acc)


def dashboard_svg(dashboard: DashboardInfo, *, width: int = 960, height: int = 540) -> str:
    w = dashboard.width or width
    h = dashboard.height or height
    aspect_h = max(120, int(width * (h / max(w, 1))))
    boxes: list[DashboardZone] = []
    _boxes(dashboard.zones, boxes)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {aspect_h}" '
        f'width="{width}" height="{aspect_h}" role="img" aria-label="{escape(dashboard.name)}">',
        f'<rect x="0" y="0" width="{width}" height="{aspect_h}" fill="#f8fafc" stroke="#94a3b8"/>',
        f'<text x="12" y="22" font-size="14" font-family="sans-serif" fill="#0f172a">'
        f"{escape(dashboard.name)}</text>",
    ]
    for zone in boxes:
        zx = zone.x or 0
        zy = zone.y or 0
        zw = zone.w if zone.w not in (None, 0) else _SCALE
        zh = zone.h if zone.h not in (None, 0) else _SCALE
        x = 8 + (zx / _SCALE) * (width - 16)
        y = 32 + (zy / _SCALE) * (aspect_h - 40)
        bw = max(24.0, (zw / _SCALE) * (width - 16))
        bh = max(18.0, (zh / _SCALE) * (aspect_h - 40))
        label = escape(zone.name or zone.type or "zone")
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="#fff" stroke="#64748b" rx="4"/>'
        )
        parts.append(
            f'<text x="{x + 6:.1f}" y="{y + 14:.1f}" font-size="11" '
            f'font-family="sans-serif" fill="#334155">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def dashboard_html(dashboard: DashboardInfo) -> str:
    svg = dashboard.svg or dashboard_svg(dashboard)
    title = escape(dashboard.name)
    size = ""
    if dashboard.width or dashboard.height:
        size = f"<p>Size: {dashboard.width or '?'} × {dashboard.height or '?'}</p>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>{title}</title>"
        "<style>body{font-family:sans-serif;margin:24px}svg{max-width:100%;height:auto}"
        "@media print{body{margin:0}}</style></head><body>"
        f"<h1>{title}</h1>{size}{svg}</body></html>"
    )


def attach_layouts(dashboards: list[DashboardInfo]) -> list[DashboardInfo]:
    out: list[DashboardInfo] = []
    for dash in dashboards:
        svg = dashboard_svg(dash)
        updated = dash.model_copy(update={"svg": svg})
        updated = updated.model_copy(update={"html": dashboard_html(updated)})
        out.append(updated)
    return out
