#!/usr/bin/env python3
"""Generate the README status badge and progress map from the parity tables.

The feature tables in ``docs/PARITY.md`` are the single source of truth for the
counts.  Run this script after editing them::

    python tools/generate_progress.py

It writes ``docs/progress.json``, ``docs/progress.svg``,
``docs/badge-parity.svg`` and ``docs/badge-tests.svg``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "docs" / "PARITY.md"

STATUS = {"✅": "full", "🟡": "partial", "⛔": "none"}
AREA_LABELS = {
    "Project and layers": "Project and layers",
    "Capture import": "Capture import",
    "Mesh replacement and asset creation": "Mesh replacement",
    "Materials": "Materials",
    "Textures": "Textures",
    "Assets, utilities, integration": "Assets and utilities",
}

GREEN = "#3fb950"
AMBER = "#d29922"
GREY = "#6e7681"
DARK = "#0d1117"
TEXT = "#c9d1d9"
MUTED = "#8b949e"

FONT = "Verdana, DejaVu Sans, sans-serif"


def parse_areas() -> list[dict]:
    """Read the per-area status counts from the Markdown parity tables."""
    areas: dict[str, dict] = {}
    current: str | None = None

    for line in PARITY.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            continue
        if current not in AREA_LABELS or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[2] not in STATUS:
            continue
        area = areas.setdefault(
            current,
            {"name": AREA_LABELS[current], "full": 0, "partial": 0, "none": 0},
        )
        area[STATUS[cells[2]]] += 1

    if not areas:
        raise SystemExit(f"No feature rows found in {PARITY}")

    result = []
    for key in AREA_LABELS:
        if key not in areas:
            continue
        area = areas[key]
        total = area["full"] + area["partial"] + area["none"]
        area["total"] = total
        area["percent"] = round(area["full"] * 100 / total, 1)
        result.append(area)

    total = sum(area["total"] for area in result)
    if total == 0:
        raise SystemExit("The parity tables contain no features")
    return result


def parse_tests() -> dict:
    """Read the executed test count from the parity document."""
    text = PARITY.read_text(encoding="utf-8")
    match = re.search(r"\*\*Executed result:\*\*\s+(\d+)\s+tests pass", text)
    passed = int(match.group(1)) if match else 0
    return {"passed": passed, "total": passed}


def badge_svg(label: str, value: str, colour: str, label_width: int, value_width: int) -> str:
    width = label_width + value_width
    rx = 4
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="28" viewBox="0 0 {width} 28" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <rect width="{width}" height="28" rx="{rx}" fill="#30363d"/>
  <path d="M{label_width} 0 H{width - rx} Q{width} 0 {width} {rx} V{28 - rx} Q{width} 28 {width - rx} 28 H{label_width} Z" fill="{colour}"/>
  <text x="{label_width // 2}" y="18.5" text-anchor="middle" font-family="{FONT}" font-size="11" fill="#ffffff">{label}</text>
  <text x="{label_width + value_width // 2}" y="18.5" text-anchor="middle" font-family="{FONT}" font-size="11" fill="#ffffff">{value}</text>
</svg>
"""


def progress_svg(areas: list[dict], totals: dict) -> str:
    width = 1000
    height = 296
    margin = 12
    full_pct = totals["full"] * 100 / totals["total"]
    coverage = (totals["full"] + totals["partial"]) * 100 / totals["total"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Toolkit parity: '
        f'{full_pct:.1f}% ({totals["full"]} of {totals["total"]} features fully implemented)">',
        f"  <title>Toolkit parity: {full_pct:.1f}% ({totals['full']}/{totals['total']})</title>",
        f'  <rect width="{width}" height="{height}" fill="{DARK}"/>',
        f'  <text x="{margin}" y="21" font-family="{FONT}" font-size="16" fill="#ffffff">'
        f"Toolkit parity: {full_pct:.1f}% ({totals['full']}/{totals['total']})</text>",
        f'  <text x="{width - margin}" y="21" text-anchor="end" font-family="{FONT}" font-size="12" fill="{MUTED}">'
        f"{coverage:.0f}% implemented or partial</text>",
    ]

    # Legend.
    legend_y = 42
    parts.append(f'  <g font-family="{FONT}" font-size="12" fill="{TEXT}">')
    for i, (colour, text) in enumerate(
        (
            (GREEN, f"{totals['full']} implemented"),
            (AMBER, f"{totals['partial']} partial"),
            (GREY, f"{totals['none']} not implemented"),
        )
    ):
        x = margin + i * 166
        parts.append(f'    <rect x="{x}" y="{legend_y - 9}" width="9" height="9" fill="{colour}"/>')
        parts.append(f'    <text x="{x + 14}" y="{legend_y}">{text}</text>')
    parts.append("  </g>")

    # Six area panels, two per row.
    panel_w = 480
    panel_h = 68
    row_gap = 10
    cell = 30
    cell_gap = 5
    for index, area in enumerate(areas):
        col = index % 2
        row = index // 2
        x = margin + col * (panel_w + 16)
        y = 60 + row * (panel_h + row_gap)
        parts.append(
            f'  <g><title>{area["name"]}: {area["full"]} implemented, '
            f'{area["partial"]} partial, {area["none"]} not implemented</title>'
        )
        parts.append(
            f'    <text x="{x}" y="{y + 17}" font-family="{FONT}" font-size="13" '
            f'font-weight="600" fill="{TEXT}">{area["name"]}</text>'
        )
        parts.append(
            f'    <text x="{x + panel_w - 4}" y="{y + 17}" text-anchor="end" '
            f'font-family="{FONT}" font-size="12" fill="{MUTED}">'
            f'{area["full"]} / {area["total"]} &#183; {area["percent"]:.0f}%</text>'
        )
        colours = [GREEN] * area["full"] + [AMBER] * area["partial"] + [GREY] * area["none"]
        for cell_index, colour in enumerate(colours):
            cx = x + cell_index * (cell + cell_gap)
            parts.append(
                f'    <rect x="{cx}" y="{y + 27}" width="{cell}" height="{cell}" '
                f'fill="{colour}" stroke="{DARK}" stroke-width="1"/>'
            )
        parts.append("  </g>")

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    areas = parse_areas()
    tests = parse_tests()

    totals = {
        "full": sum(area["full"] for area in areas),
        "partial": sum(area["partial"] for area in areas),
        "none": sum(area["none"] for area in areas),
    }
    totals["total"] = sum(area["total"] for area in areas)
    totals["percent"] = round(totals["full"] * 100 / totals["total"], 1)

    data = {
        "snapshot": "2026-10-31",
        "areas": areas,
        "totals": totals,
        "tests": tests,
    }
    docs = ROOT / "docs"
    (docs / "progress.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (docs / "progress.svg").write_text(progress_svg(areas, totals), encoding="utf-8")
    (docs / "badge-parity.svg").write_text(
        badge_svg("toolkit parity", f"{totals['percent']:.0f}%", "#9e6a03", 150, 66),
        encoding="utf-8",
    )
    (docs / "badge-tests.svg").write_text(
        badge_svg("tests", f"{tests['passed']}/{tests['total']}", "#238636", 78, 76),
        encoding="utf-8",
    )

    print(
        f"{totals['full']} implemented, {totals['partial']} partial, "
        f"{totals['none']} not implemented out of {totals['total']} "
        f"({totals['percent']}%), tests {tests['passed']}/{tests['total']}"
    )


if __name__ == "__main__":
    main()
