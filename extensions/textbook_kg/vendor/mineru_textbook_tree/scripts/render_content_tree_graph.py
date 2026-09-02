from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an interactive tree-graph HTML from content_tree.json.")
    parser.add_argument("--input", required=True, type=Path, help="Path to content_tree.json.")
    parser.add_argument("--output", type=Path, help="Output HTML path. Defaults to content_tree_graph.html next to the input.")
    parser.add_argument(
        "--svg-output",
        type=Path,
        help="Optional static SVG output path. Defaults to a sibling SVG next to the HTML output.",
    )
    return parser.parse_args()


def assign_ids(nodes: list[dict[str, Any]], prefix: str = "n", parent_id: str = "root") -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        node_id = f"{prefix}-{index}"
        indexed.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "marker": node.get("marker", ""),
                "title": node.get("title", ""),
                "label": node.get("label", ""),
                "level": int(node.get("level", 1) or 1),
                "toc_page_start": node.get("toc_page_start"),
                "toc_page_end": node.get("toc_page_end"),
                "pdf_page_start": node.get("pdf_page_start"),
                "pdf_page_end": node.get("pdf_page_end"),
                "content": node.get("content", "") or "",
                "children": assign_ids(node.get("children", []), node_id, node_id),
            }
        )
    return indexed


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in nodes:
        items.append(node)
        items.extend(flatten(node.get("children", [])))
    return items


def compute_stats(root: dict[str, Any]) -> dict[str, int]:
    flat = flatten(root["children"])
    return {
        "node_count": len(flat),
        "chapter_count": len(root["children"]),
        "max_depth": max((int(node.get("level", 1) or 1) for node in flat), default=0),
        "content_chars": sum(len((node.get("content") or "").strip()) for node in flat),
    }


def build_root(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "root",
        "parent_id": None,
        "marker": "",
        "title": data["book_title"],
        "label": data["book_title"],
        "level": 0,
        "toc_page_start": None,
        "toc_page_end": None,
        "pdf_page_start": None,
        "pdf_page_end": None,
        "content": "",
        "children": assign_ids(data["chapters"]),
    }


def clone_tree_for_export(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "label": node.get("label", ""),
        "level": int(node.get("level", 0) or 0),
        "pdf_page_start": node.get("pdf_page_start"),
        "pdf_page_end": node.get("pdf_page_end"),
        "children": [clone_tree_for_export(child) for child in node.get("children", [])],
    }


def node_card_spec_py(node: dict[str, Any]) -> dict[str, Any]:
    level = int(node.get("level", 0) or 0)
    if level == 0:
        return {"width": 250, "height": 92, "wrap": 12, "kind": "book"}
    if level == 1:
        return {"width": 230, "height": 72, "wrap": 15, "kind": "chapter"}
    if level == 2:
        return {"width": 216, "height": 64, "wrap": 16, "kind": "section"}
    return {"width": 210, "height": 58, "wrap": 17, "kind": "subsection"}


def wrap_label_py(text: str, max_units: float) -> list[str]:
    lines: list[str] = []
    current = ""
    current_units = 0.0

    def char_units(char: str) -> float:
        return 0.58 if ord(char) <= 0xFF else 1.0

    for char in text:
        units = char_units(char)
        if current and current_units + units > max_units:
            lines.append(current)
            current = char
            current_units = units
        else:
            current += char
            current_units += units

    if current:
        lines.append(current)
    return lines[:3]


def page_line_text(node: dict[str, Any]) -> str:
    start = node.get("pdf_page_start")
    end = node.get("pdf_page_end")
    if start is None:
        return "PDF 页码未记录"
    if end is None or end == start:
        return f"PDF {start}"
    return f"PDF {start}-{end}"


def layout_tree_py(root: dict[str, Any]) -> dict[str, Any]:
    depth_gap = 290
    leaf_gap = 72
    margin_left = 88
    margin_top = 220
    footer_padding = 84
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, dict[str, Any]]] = []
    leaf_index = 0
    max_depth = 0

    def visit(node: dict[str, Any], depth: int) -> dict[str, Any]:
        nonlocal leaf_index, max_depth
        laid_out = {
            "id": node["id"],
            "label": node.get("label", ""),
            "level": int(node.get("level", 0) or 0),
            "pdf_page_start": node.get("pdf_page_start"),
            "pdf_page_end": node.get("pdf_page_end"),
            "children": [],
        }
        max_depth = max(max_depth, depth)
        children = [visit(child, depth + 1) for child in node.get("children", [])]
        laid_out["children"] = children
        if not children:
            laid_out["y"] = leaf_index * leaf_gap
            leaf_index += 1
        else:
            for child in children:
                links.append({"source": laid_out, "target": child})
            laid_out["y"] = (children[0]["y"] + children[-1]["y"]) / 2
        laid_out["x"] = depth * depth_gap
        nodes.append(laid_out)
        return laid_out

    visit(root, 0)

    width = margin_left + max_depth * depth_gap + 380
    height = margin_top + max(leaf_index * leaf_gap, 480) + footer_padding

    for node in nodes:
        node["plotX"] = node["x"] + margin_left
        node["plotY"] = node["y"] + margin_top

    return {"nodes": nodes, "links": links, "width": width, "height": height}


def render_static_svg(data: dict[str, Any]) -> str:
    root = build_root(data)
    stats = compute_stats(root)
    export_root = clone_tree_for_export(root)
    layout = layout_tree_py(export_root)
    width = layout["width"]
    height = layout["height"]
    title = html.escape(data["book_title"])
    subtitle = html.escape("静态树状图谱总览：直接展示章节节点与连线，适合截图、打印和快速查阅。")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f"  <title id=\"title\">{title} 树状图谱</title>",
        f"  <desc id=\"desc\">{subtitle}</desc>",
        "  <defs>",
        "    <linearGradient id=\"bgFill\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">",
        "      <stop offset=\"0%\" stop-color=\"#f4e7d3\" />",
        "      <stop offset=\"100%\" stop-color=\"#dcc4a2\" />",
        "    </linearGradient>",
        "    <linearGradient id=\"heroFill\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">",
        "      <stop offset=\"0%\" stop-color=\"#fffaf2\" stop-opacity=\"0.96\" />",
        "      <stop offset=\"100%\" stop-color=\"#fff0dd\" stop-opacity=\"0.92\" />",
        "    </linearGradient>",
        "    <linearGradient id=\"bookFillStatic\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">",
        "      <stop offset=\"0%\" stop-color=\"#f6e8d5\" />",
        "      <stop offset=\"100%\" stop-color=\"#d6ba98\" />",
        "    </linearGradient>",
        "    <linearGradient id=\"chapterFillStatic\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">",
        "      <stop offset=\"0%\" stop-color=\"#eff6f2\" />",
        "      <stop offset=\"100%\" stop-color=\"#dce9df\" />",
        "    </linearGradient>",
        "    <pattern id=\"grid\" width=\"24\" height=\"24\" patternUnits=\"userSpaceOnUse\">",
        "      <path d=\"M 24 0 L 0 0 0 24\" fill=\"none\" stroke=\"#5a3f2a\" stroke-opacity=\"0.06\" stroke-width=\"1\" />",
        "    </pattern>",
        "    <filter id=\"cardShadow\" x=\"-20%\" y=\"-20%\" width=\"140%\" height=\"160%\">",
        "      <feDropShadow dx=\"0\" dy=\"8\" stdDeviation=\"8\" flood-color=\"#48301f\" flood-opacity=\"0.12\" />",
        "    </filter>",
        "  </defs>",
        f"  <rect width=\"{width}\" height=\"{height}\" fill=\"url(#bgFill)\" />",
        f"  <rect width=\"{width}\" height=\"{height}\" fill=\"url(#grid)\" opacity=\"0.8\" />",
        f"  <rect x=\"18\" y=\"18\" width=\"{width - 36}\" height=\"154\" rx=\"28\" fill=\"#fffbf4\" fill-opacity=\"0.88\" stroke=\"#ffffff\" stroke-opacity=\"0.62\" />",
        "  <text x=\"48\" y=\"62\" fill=\"#23453a\" font-size=\"12\" font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\" letter-spacing=\"1.2\">TREE ATLAS</text>",
        f"  <text x=\"48\" y=\"112\" fill=\"#2a241d\" font-size=\"44\" font-family=\"Source Han Serif SC, Noto Serif CJK SC, STSong, serif\" font-weight=\"700\">{title}</text>",
        f"  <text x=\"48\" y=\"148\" fill=\"#746355\" font-size=\"16\" font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">{subtitle}</text>",
    ]

    stat_items = [
        ("节点总数", str(stats["node_count"])),
        ("一级章节", str(stats["chapter_count"])),
        ("最大层级", str(stats["max_depth"])),
        ("正文字符", str(stats["content_chars"])),
    ]
    stat_w = 142
    stat_h = 54
    stat_gap = 14
    stat_start_x = width - (stat_w * 2 + stat_gap + 48)
    stat_start_y = 38
    for index, (label, value) in enumerate(stat_items):
        col = index % 2
        row = index // 2
        x = stat_start_x + col * (stat_w + stat_gap)
        y = stat_start_y + row * (stat_h + 14)
        parts.extend(
            [
                f"  <rect x=\"{x}\" y=\"{y}\" width=\"{stat_w}\" height=\"{stat_h}\" rx=\"18\" fill=\"#ffffff\" fill-opacity=\"0.74\" stroke=\"#ffffff\" stroke-opacity=\"0.82\" />",
                f"  <text x=\"{x + 14}\" y=\"{y + 18}\" fill=\"#746355\" font-size=\"12\" font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">{html.escape(label)}</text>",
                f"  <text x=\"{x + 14}\" y=\"{y + 42}\" fill=\"#8c3d20\" font-size=\"24\" font-weight=\"700\" font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">{html.escape(value)}</text>",
            ]
        )

    parts.append("  <g id=\"graph\">")
    for link in layout["links"]:
        source = link["source"]
        target = link["target"]
        source_spec = node_card_spec_py(source)
        target_spec = node_card_spec_py(target)
        x1 = source["plotX"] + source_spec["width"]
        y1 = source["plotY"] + source_spec["height"] / 2
        x2 = target["plotX"]
        y2 = target["plotY"] + target_spec["height"] / 2
        mid_x = x1 + (x2 - x1) * 0.52
        stroke = "#23453a" if source["level"] == 0 else "#8c5a41"
        stroke_width = "2.4" if source["level"] == 0 else "2"
        parts.append(
            f"    <path d=\"M {x1:.1f} {y1:.1f} C {mid_x:.1f} {y1:.1f}, {mid_x:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}\" "
            f"fill=\"none\" stroke=\"{stroke}\" stroke-opacity=\"0.38\" stroke-width=\"{stroke_width}\" stroke-linecap=\"round\" />"
        )

    for node in layout["nodes"]:
        spec = node_card_spec_py(node)
        kind = spec["kind"]
        fill = {
            "book": "url(#bookFillStatic)",
            "chapter": "url(#chapterFillStatic)",
            "section": "#ffffff",
            "subsection": "#fdf9f3",
        }[kind]
        fill_opacity = {
            "book": "1",
            "chapter": "1",
            "section": "0.88",
            "subsection": "0.94",
        }[kind]
        stroke = {
            "book": "#23453a",
            "chapter": "#23453a",
            "section": "#7f3d20",
            "subsection": "#7f3d20",
        }[kind]
        stroke_opacity = {
            "book": "0.28",
            "chapter": "0.18",
            "section": "0.16",
            "subsection": "0.14",
        }[kind]
        x = node["plotX"]
        y = node["plotY"]
        label_lines = wrap_label_py(node["label"], spec["wrap"])
        parts.extend(
            [
                f"    <g transform=\"translate({x:.1f}, {y:.1f})\">",
                f"      <rect x=\"0\" y=\"0\" width=\"{spec['width']}\" height=\"{spec['height']}\" rx=\"18\" fill=\"{fill}\" fill-opacity=\"{fill_opacity}\" stroke=\"{stroke}\" stroke-opacity=\"{stroke_opacity}\" stroke-width=\"1.5\" filter=\"url(#cardShadow)\" />",
            ]
        )
        for index, line in enumerate(label_lines):
            parts.append(
                f"      <text x=\"16\" y=\"{24 + index * 18}\" fill=\"#2a241d\" font-size=\"14\" font-weight=\"700\" "
                f"font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">{html.escape(line)}</text>"
            )
        parts.append(
            f"      <text x=\"16\" y=\"{spec['height'] - 12}\" fill=\"#746355\" font-size=\"11.5\" "
            f"font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">{html.escape(page_line_text(node))}</text>"
        )
        parts.append("    </g>")

    parts.extend(
        [
            "  </g>",
            f"  <rect x=\"{width - 358}\" y=\"{height - 62}\" width=\"316\" height=\"34\" rx=\"14\" fill=\"#ffffff\" fill-opacity=\"0.76\" stroke=\"#ffffff\" stroke-opacity=\"0.82\" />",
            f"  <text x=\"{width - 340}\" y=\"{height - 40}\" fill=\"#746355\" font-size=\"12\" font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\">静态成品版：打开即可看到完整树图，适合截图与打印</text>",
            "</svg>",
        ]
    )
    return "\n".join(parts)


def render_css() -> str:
    return """
    :root {
      --sand: #f1e4cf;
      --sand-deep: #dbc2a0;
      --paper: rgba(255, 251, 244, 0.9);
      --ink: #2a241d;
      --muted: #746355;
      --line: rgba(90, 63, 42, 0.2);
      --copper: #b45a34;
      --copper-strong: #8c3d20;
      --moss: #23453a;
      --shadow: 0 18px 56px rgba(77, 51, 33, 0.14);
      --radius: 24px;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      min-height: 100%;
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.55), transparent 24%),
        radial-gradient(circle at 80% 20%, rgba(180,90,52,0.16), transparent 30%),
        linear-gradient(135deg, var(--sand), var(--sand-deep));
      color: var(--ink);
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }

    body {
      padding: 16px;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      min-height: calc(100vh - 32px);
    }

    .panel {
      background: var(--paper);
      border: 1px solid rgba(255,255,255,0.55);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      overflow: hidden;
    }

    .graph-panel {
      position: relative;
      display: flex;
      flex-direction: column;
      min-width: 0;
      background:
        linear-gradient(180deg, rgba(255, 251, 244, 0.98), rgba(251, 243, 231, 0.9)),
        linear-gradient(120deg, rgba(35,69,58,0.08), transparent 38%);
    }

    .hero {
      padding: 22px 24px 18px;
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(160deg, rgba(255, 250, 242, 0.96), rgba(255, 240, 221, 0.9)),
        linear-gradient(120deg, rgba(180,90,52,0.12), transparent 55%);
    }

    .eyebrow {
      display: inline-flex;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(35, 69, 58, 0.08);
      color: var(--moss);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .hero-top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
    }

    .title {
      margin: 14px 0 10px;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
      font-size: clamp(28px, 3vw, 42px);
      line-height: 1.12;
    }

    .subtitle {
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.7;
      max-width: 760px;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(100px, 1fr));
      gap: 10px;
      min-width: 230px;
    }

    .stat {
      padding: 12px 14px;
      border-radius: 18px;
      background: rgba(255,255,255,0.62);
      border: 1px solid rgba(255,255,255,0.76);
    }

    .stat-label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }

    .stat-value {
      color: var(--copper-strong);
      font-size: 22px;
      font-weight: 700;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.48);
    }

    .search {
      flex: 1 1 260px;
      min-width: 220px;
      padding: 12px 14px;
      border-radius: 15px;
      border: 1px solid rgba(140, 61, 32, 0.18);
      background: rgba(255,255,255,0.82);
      font-size: 14px;
      color: var(--ink);
      outline: none;
    }

    .search:focus {
      border-color: rgba(180, 90, 52, 0.5);
      box-shadow: 0 0 0 4px rgba(180, 90, 52, 0.12);
    }

    .btn-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    button {
      appearance: none;
      border: none;
      border-radius: 14px;
      padding: 10px 14px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      transition: transform 160ms ease, box-shadow 160ms ease;
    }

    button:hover {
      transform: translateY(-1px);
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--copper), #d68457);
      color: white;
      box-shadow: 0 10px 18px rgba(180, 90, 52, 0.22);
    }

    .btn-secondary {
      background: rgba(35, 69, 58, 0.1);
      color: var(--moss);
    }

    .canvas-shell {
      position: relative;
      flex: 1;
      min-height: 640px;
      overflow: hidden;
      background:
        linear-gradient(rgba(90,63,42,0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(90,63,42,0.05) 1px, transparent 1px),
        radial-gradient(circle at 15% 20%, rgba(255,255,255,0.45), transparent 26%);
      background-size: 24px 24px, 24px 24px, auto;
    }

    .canvas-note {
      position: absolute;
      right: 18px;
      bottom: 18px;
      z-index: 2;
      padding: 10px 12px;
      border-radius: 14px;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255,255,255,0.72);
      border: 1px solid rgba(255,255,255,0.78);
      pointer-events: none;
    }

    svg {
      display: block;
      width: 100%;
      height: 100%;
      user-select: none;
      cursor: grab;
    }

    svg.dragging {
      cursor: grabbing;
    }

    .link {
      fill: none;
      stroke: rgba(127, 61, 32, 0.34);
      stroke-width: 2;
      stroke-linecap: round;
    }

    .link.root-link {
      stroke: rgba(35, 69, 58, 0.42);
      stroke-width: 2.4;
    }

    .node-card {
      fill: rgba(255,255,255,0.84);
      stroke: rgba(127, 61, 32, 0.16);
      stroke-width: 1.5;
      filter: drop-shadow(0 10px 16px rgba(72, 48, 31, 0.08));
    }

    .node-book .node-card {
      fill: url(#bookFill);
      stroke: rgba(35, 69, 58, 0.26);
      stroke-width: 2;
    }

    .node-chapter .node-card {
      fill: url(#chapterFill);
      stroke: rgba(35, 69, 58, 0.18);
    }

    .node-section .node-card {
      fill: rgba(255,255,255,0.88);
    }

    .node-subsection .node-card {
      fill: rgba(253,249,243,0.92);
    }

    .node.selected .node-card {
      stroke: rgba(180, 90, 52, 0.72);
      stroke-width: 2.6;
    }

    .node.dim {
      opacity: 0.22;
    }

    .node.match .node-card {
      stroke: rgba(35, 69, 58, 0.7);
      stroke-width: 2.2;
    }

    .node-label {
      fill: var(--ink);
      font-size: 14px;
      font-weight: 700;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }

    .node-pages {
      fill: var(--muted);
      font-size: 11.5px;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }

    .toggle-pill {
      fill: rgba(35, 69, 58, 0.12);
      stroke: rgba(35, 69, 58, 0.18);
      stroke-width: 1;
    }

    .toggle-text {
      fill: var(--moss);
      font-size: 12px;
      font-weight: 700;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      pointer-events: none;
    }

    .detail-panel {
      display: flex;
      flex-direction: column;
      background:
        linear-gradient(180deg, rgba(255,253,248,0.97), rgba(251,244,233,0.9)),
        linear-gradient(145deg, rgba(35,69,58,0.06), transparent 45%);
    }

    .detail-head {
      padding: 24px 24px 18px;
      border-bottom: 1px solid var(--line);
    }

    .detail-kicker {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .detail-title {
      margin: 12px 0 0;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
      font-size: clamp(24px, 2vw, 34px);
      line-height: 1.2;
    }

    .detail-breadcrumbs {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }

    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 13px;
      background: rgba(35, 69, 58, 0.08);
      color: var(--moss);
    }

    .chip.copper {
      background: rgba(180, 90, 52, 0.12);
      color: var(--copper-strong);
    }

    .detail-scroll {
      flex: 1;
      overflow: auto;
      padding: 18px 22px 24px;
    }

    .content-box {
      border-radius: 22px;
      padding: 20px 18px;
      background: rgba(255,255,255,0.62);
      border: 1px solid rgba(255,255,255,0.74);
      box-shadow: 0 10px 26px rgba(72, 48, 31, 0.07);
    }

    .content-caption {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .content-text {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.9;
      font-size: 15px;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
    }

    .empty {
      color: var(--muted);
      font-style: italic;
    }

    @media (max-width: 1080px) {
      .layout {
        grid-template-columns: 1fr;
      }

      .detail-panel {
        min-height: 45vh;
      }

      .canvas-shell {
        min-height: 70vh;
      }
    }
    """


def render_markup(title: str, stats: dict[str, int], payload: str) -> str:
    return f"""
  <div class="layout">
    <section class="panel graph-panel">
      <div class="hero">
        <div class="hero-top">
          <div>
            <div class="eyebrow">Tree Atlas</div>
            <h1 class="title">{title}</h1>
            <p class="subtitle">真正的树状图谱视图。主节点代表整本教材，向右分叉出章、节、小节；点击节点可在右侧查看正文，拖拽或滚轮可以缩放与平移整张图。</p>
          </div>
          <div class="stats">
            <div class="stat"><div class="stat-label">节点总数</div><div class="stat-value">{stats["node_count"]}</div></div>
            <div class="stat"><div class="stat-label">一级章节</div><div class="stat-value">{stats["chapter_count"]}</div></div>
            <div class="stat"><div class="stat-label">最大层级</div><div class="stat-value">{stats["max_depth"]}</div></div>
            <div class="stat"><div class="stat-label">正文字符</div><div class="stat-value">{stats["content_chars"]}</div></div>
          </div>
        </div>
      </div>

      <div class="toolbar">
        <input id="searchInput" class="search" type="search" placeholder="搜索编号或标题，例如 7.5、二叉树、排序">
        <div class="btn-row">
          <button id="showChaptersButton" class="btn-secondary" type="button">仅看章</button>
          <button id="showSectionsButton" class="btn-secondary" type="button">展开到二级</button>
          <button id="expandAllButton" class="btn-primary" type="button">展开全部</button>
          <button id="fitButton" class="btn-secondary" type="button">适应画布</button>
        </div>
      </div>

      <div class="canvas-shell" id="canvasShell">
        <svg id="treeSvg" aria-label="章节树状图谱">
          <defs>
            <linearGradient id="bookFill" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#f6e8d5"></stop>
              <stop offset="100%" stop-color="#d6ba98"></stop>
            </linearGradient>
            <linearGradient id="chapterFill" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#eff6f2"></stop>
              <stop offset="100%" stop-color="#dce9df"></stop>
            </linearGradient>
          </defs>
          <g id="viewport"></g>
        </svg>
        <div class="canvas-note">滚轮缩放 · 拖拽平移 · 点击节点查看正文 · 点击角标折叠/展开</div>
      </div>
    </section>

    <aside class="panel detail-panel">
      <div class="detail-head">
        <div class="detail-kicker">Node Detail</div>
        <h2 id="detailTitle" class="detail-title"></h2>
        <div id="detailBreadcrumbs" class="detail-breadcrumbs"></div>
        <div class="chips">
          <div id="detailLevel" class="chip"></div>
          <div id="detailPdf" class="chip copper"></div>
          <div id="detailToc" class="chip"></div>
        </div>
      </div>
      <div class="detail-scroll">
        <div class="content-box">
          <p class="content-caption">对应正文</p>
          <pre id="detailContent" class="content-text"></pre>
        </div>
      </div>
    </aside>
  </div>

  <script id="tree-data" type="application/json">{payload}</script>
  <script>
{render_script()}
  </script>
"""


def render_script() -> str:
    return """
    const source = JSON.parse(document.getElementById("tree-data").textContent);
    const root = source.root;

    const svg = document.getElementById("treeSvg");
    const viewport = document.getElementById("viewport");
    const canvasShell = document.getElementById("canvasShell");
    const searchInput = document.getElementById("searchInput");
    const detailTitle = document.getElementById("detailTitle");
    const detailBreadcrumbs = document.getElementById("detailBreadcrumbs");
    const detailLevel = document.getElementById("detailLevel");
    const detailPdf = document.getElementById("detailPdf");
    const detailToc = document.getElementById("detailToc");
    const detailContent = document.getElementById("detailContent");

    const showChaptersButton = document.getElementById("showChaptersButton");
    const showSectionsButton = document.getElementById("showSectionsButton");
    const expandAllButton = document.getElementById("expandAllButton");
    const fitButton = document.getElementById("fitButton");

    const state = {
      selectedId: "root",
      query: "",
      expanded: new Set(),
      transform: { x: 32, y: 40, k: 1 },
      hasFitted: false,
      pendingFocusId: null,
      bounds: { width: 1200, height: 900 },
      nodeMap: new Map(),
      matchSet: new Set(),
    };

    function walk(node, parent = null) {
      node.parent = parent;
      node.searchText = [node.marker || "", node.title || "", node.label || ""].join(" ").toLowerCase();
      state.nodeMap.set(node.id, node);
      (node.children || []).forEach((child) => walk(child, node));
    }

    walk(root);

    function initializeExpansion(mode) {
      state.expanded.clear();
      const openUntil = mode === "chapters" ? 0 : mode === "sections" ? 1 : Number.POSITIVE_INFINITY;
      state.nodeMap.forEach((node) => {
        if ((node.children || []).length && node.level <= openUntil) {
          state.expanded.add(node.id);
        }
      });
      drawCurrent(true);
    }

    function formatRange(start, end, prefix) {
      if (start == null) return prefix + "：未记录";
      if (end == null || end === start) return prefix + "：" + start;
      return prefix + "：" + start + "-" + end;
    }

    function getNodePath(node) {
      const chain = [];
      let current = node;
      while (current) {
        chain.push(current.label);
        current = current.parent;
      }
      return chain.reverse();
    }

    function updateDetail(node) {
      detailTitle.textContent = node.label;
      detailBreadcrumbs.textContent = getNodePath(node).join(" / ");
      detailLevel.textContent = "层级：" + node.level;
      detailPdf.textContent = formatRange(node.pdf_page_start, node.pdf_page_end, "PDF页码");
      detailToc.textContent = formatRange(node.toc_page_start, node.toc_page_end, "目录页码");
      const text = (node.content || "").trim();
      detailContent.textContent = text || "(无正文内容)";
      detailContent.classList.toggle("empty", !text);
    }

    function createSvgEl(name, attrs = {}) {
      const el = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
      return el;
    }

    function isNodeExpanded(node) {
      return state.expanded.has(node.id);
    }

    function buildVisibleTree(node, query) {
      const lowered = query.trim().toLowerCase();
      const selfMatch = !lowered || node.searchText.includes(lowered);
      const children = (node.children || [])
        .map((child) => buildVisibleTree(child, lowered))
        .filter(Boolean);
      const allowChildren = lowered ? true : isNodeExpanded(node);
      const visibleChildren = allowChildren ? children : [];
      const hasVisibleDescendant = children.length > 0;

      if (lowered && !selfMatch && !hasVisibleDescendant) {
        return null;
      }

      return {
        ...node,
        match: selfMatch && !!lowered,
        children: visibleChildren,
      };
    }

    function collectMatches(node, set) {
      if (node.match) {
        set.add(node.id);
      }
      (node.children || []).forEach((child) => collectMatches(child, set));
    }

    function layoutTree(visibleRoot) {
      const nodes = [];
      const links = [];
      const depthGap = 290;
      const leafGap = 72;
      const marginLeft = 80;
      const marginTop = 80;
      let leafIndex = 0;
      let maxDepth = 0;

      function visit(node, depth) {
        node.depth = depth;
        maxDepth = Math.max(maxDepth, depth);
        if (!node.children || node.children.length === 0) {
          node.y = leafIndex * leafGap;
          leafIndex += 1;
        } else {
          node.children.forEach((child) => {
            visit(child, depth + 1);
            links.push({ source: node, target: child });
          });
          node.y = (node.children[0].y + node.children[node.children.length - 1].y) / 2;
        }
        node.x = depth * depthGap;
        nodes.push(node);
      }

      visit(visibleRoot, 0);

      const width = marginLeft + maxDepth * depthGap + 340;
      const height = marginTop + Math.max(leafIndex * leafGap, 480);

      nodes.forEach((node) => {
        node.plotX = node.x + marginLeft;
        node.plotY = node.y + marginTop;
      });

      return { nodes, links, width, height };
    }

    function wrapLabel(text, maxUnits) {
      const lines = [];
      let current = "";
      let currentUnits = 0;

      function charUnits(char) {
        return /[\\u0000-\\u00ff]/.test(char) ? 0.58 : 1;
      }

      for (const char of text) {
        const units = charUnits(char);
        if (current && currentUnits + units > maxUnits) {
          lines.push(current);
          current = char;
          currentUnits = units;
        } else {
          current += char;
          currentUnits += units;
        }
      }

      if (current) {
        lines.push(current);
      }
      return lines.slice(0, 3);
    }

    function nodeCardSpec(node) {
      if (node.level === 0) return { width: 250, height: 92, className: "node-book", wrap: 12 };
      if (node.level === 1) return { width: 230, height: 72, className: "node-chapter", wrap: 15 };
      if (node.level === 2) return { width: 216, height: 64, className: "node-section", wrap: 16 };
      return { width: 210, height: 58, className: "node-subsection", wrap: 17 };
    }

    function updateTransform() {
      viewport.setAttribute("transform", `translate(${state.transform.x}, ${state.transform.y}) scale(${state.transform.k})`);
    }

    function fitToView() {
      const shell = canvasShell.getBoundingClientRect();
      const padding = 48;
      const scaleX = (shell.width - padding * 2) / state.bounds.width;
      const scaleY = (shell.height - padding * 2) / state.bounds.height;
      const k = Math.max(0.2, Math.min(1.15, scaleX, scaleY));
      state.transform.k = k;
      const scaledWidth = state.bounds.width * k;
      const scaledHeight = state.bounds.height * k;
      state.transform.x = scaledWidth <= shell.width - padding * 2
        ? (shell.width - scaledWidth) / 2
        : padding;
      state.transform.y = scaledHeight <= shell.height - padding * 2
        ? Math.max(16, (shell.height - scaledHeight) / 2)
        : 16;
      updateTransform();
      state.hasFitted = true;
    }

    function focusNodeById(nodeId) {
      const node = state.nodeMap.get(nodeId);
      if (!node || !node._layout) return;

      const shell = canvasShell.getBoundingClientRect();
      const spec = nodeCardSpec(node);
      const centerX = node._layout.plotX + spec.width / 2;
      const centerY = node._layout.plotY + spec.height / 2;
      state.transform.x = shell.width * 0.42 - centerX * state.transform.k;
      state.transform.y = shell.height * 0.5 - centerY * state.transform.k;
      updateTransform();
    }

    function firstVisibleNode(node) {
      if (!node) return null;
      if (node.id !== "root") return node;
      for (const child of node.children || []) {
        const found = firstVisibleNode(child);
        if (found) return found;
      }
      return node;
    }

    function drawGraph(layout) {
      viewport.innerHTML = "";

      layout.links.forEach((link) => {
        const sourceSpec = nodeCardSpec(link.source);
        const targetSpec = nodeCardSpec(link.target);
        const x1 = link.source.plotX + sourceSpec.width;
        const y1 = link.source.plotY + sourceSpec.height / 2;
        const x2 = link.target.plotX;
        const y2 = link.target.plotY + targetSpec.height / 2;
        const midX = x1 + (x2 - x1) * 0.52;
        const path = createSvgEl("path", {
          d: `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`,
          class: "link" + (link.source.level === 0 ? " root-link" : ""),
        });
        viewport.appendChild(path);
      });

      layout.nodes.forEach((node) => {
        const spec = nodeCardSpec(node);
        const origin = state.nodeMap.get(node.id);
        if (origin) {
          origin._layout = node;
        }
        const group = createSvgEl("g", {
          class: [
            "node",
            spec.className,
            state.selectedId === node.id ? "selected" : "",
            state.query && !state.matchSet.has(node.id) && node.id !== "root" ? "dim" : "",
            state.matchSet.has(node.id) ? "match" : "",
          ].filter(Boolean).join(" "),
          transform: `translate(${node.plotX}, ${node.plotY})`,
        });

        const rect = createSvgEl("rect", {
          class: "node-card",
          rx: 18,
          ry: 18,
          width: spec.width,
          height: spec.height,
        });
        group.appendChild(rect);

        const text = createSvgEl("text", { class: "node-label", x: 16, y: 22 });
        wrapLabel(node.label, spec.wrap).forEach((line, index) => {
          const tspan = createSvgEl("tspan", { x: 16, y: 24 + index * 18 });
          tspan.textContent = line;
          text.appendChild(tspan);
        });
        group.appendChild(text);

        const pageLine = createSvgEl("text", { class: "node-pages", x: 16, y: spec.height - 12 });
        pageLine.textContent = node.pdf_page_start == null
          ? "PDF页码未记录"
          : node.pdf_page_end && node.pdf_page_end !== node.pdf_page_start
            ? `PDF ${node.pdf_page_start}-${node.pdf_page_end}`
            : `PDF ${node.pdf_page_start}`;
        group.appendChild(pageLine);

        rect.addEventListener("click", (event) => {
          event.stopPropagation();
          state.selectedId = node.id;
          updateDetail(state.nodeMap.get(node.id));
          drawCurrent();
        });

        group.addEventListener("dblclick", (event) => {
          event.stopPropagation();
          state.selectedId = node.id;
          updateDetail(state.nodeMap.get(node.id));
          drawCurrent();
          requestAnimationFrame(() => focusNodeById(node.id));
        });

        if ((node.children || []).length) {
          const toggleGroup = createSvgEl("g", { transform: `translate(${spec.width - 28}, 8)` });
          const pill = createSvgEl("rect", { class: "toggle-pill", rx: 9, ry: 9, width: 20, height: 20 });
          const toggleText = createSvgEl("text", {
            class: "toggle-text",
            x: 10,
            y: 14,
            "text-anchor": "middle",
          });
          toggleText.textContent = state.expanded.has(node.id) || state.query ? "−" : "+";
          toggleGroup.appendChild(pill);
          toggleGroup.appendChild(toggleText);
          toggleGroup.addEventListener("click", (event) => {
            event.stopPropagation();
            if (state.expanded.has(node.id)) {
              state.expanded.delete(node.id);
            } else {
              state.expanded.add(node.id);
            }
            drawCurrent();
          });
          group.appendChild(toggleGroup);
        }

        viewport.appendChild(group);
      });
    }

    function drawCurrent(resetFit = false) {
      const visibleRoot = buildVisibleTree(root, state.query) || root;
      const layout = layoutTree(visibleRoot);
      state.bounds = { width: layout.width, height: layout.height };
      state.matchSet.clear();
      collectMatches(visibleRoot, state.matchSet);

      if (resetFit) {
        state.hasFitted = false;
      }

      if (state.query && !state.matchSet.has(state.selectedId) && state.selectedId !== "root") {
        const replacement = firstVisibleNode(visibleRoot);
        state.selectedId = replacement ? replacement.id : "root";
      }

      updateDetail(state.nodeMap.get(state.selectedId) || root);
      drawGraph(layout);
      updateTransform();

      if (!state.hasFitted) {
        fitToView();
      } else if (state.pendingFocusId) {
        const id = state.pendingFocusId;
        state.pendingFocusId = null;
        requestAnimationFrame(() => focusNodeById(id));
      }
    }

    function zoomAt(clientX, clientY, deltaScale) {
      const rect = canvasShell.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const nextK = Math.max(0.18, Math.min(2.6, state.transform.k * deltaScale));
      const worldX = (px - state.transform.x) / state.transform.k;
      const worldY = (py - state.transform.y) / state.transform.k;
      state.transform.k = nextK;
      state.transform.x = px - worldX * nextK;
      state.transform.y = py - worldY * nextK;
      updateTransform();
    }

    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.08 : 0.92);
    }, { passive: false });

    let dragState = null;

    svg.addEventListener("pointerdown", (event) => {
      if (event.target.closest && event.target.closest(".node")) {
        return;
      }
      dragState = {
        x: event.clientX,
        y: event.clientY,
        originX: state.transform.x,
        originY: state.transform.y,
      };
      svg.classList.add("dragging");
      svg.setPointerCapture(event.pointerId);
    });

    svg.addEventListener("pointermove", (event) => {
      if (!dragState) return;
      state.transform.x = dragState.originX + (event.clientX - dragState.x);
      state.transform.y = dragState.originY + (event.clientY - dragState.y);
      updateTransform();
    });

    function endDrag(event) {
      if (!dragState) return;
      dragState = null;
      svg.classList.remove("dragging");
      try {
        svg.releasePointerCapture(event.pointerId);
      } catch (error) {
        // Ignore release errors.
      }
    }

    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);

    searchInput.addEventListener("input", (event) => {
      state.query = (event.target.value || "").trim().toLowerCase();
      if (state.query) {
        const matches = [];
        state.nodeMap.forEach((node) => {
          if (node.searchText.includes(state.query)) {
            matches.push(node);
            let current = node.parent;
            while (current) {
              state.expanded.add(current.id);
              current = current.parent;
            }
          }
        });
        state.pendingFocusId = matches[0] ? matches[0].id : null;
      } else {
        state.pendingFocusId = null;
      }
      drawCurrent();
    });

    showChaptersButton.addEventListener("click", () => initializeExpansion("chapters"));
    showSectionsButton.addEventListener("click", () => initializeExpansion("sections"));
    expandAllButton.addEventListener("click", () => initializeExpansion("all"));
    fitButton.addEventListener("click", () => fitToView());

    window.addEventListener("resize", () => fitToView());

    initializeExpansion("sections");
"""


def render_html(data: dict[str, Any]) -> str:
    root = build_root(data)
    stats = compute_stats(root)
    payload = json.dumps(
        {
            "book_title": data["book_title"],
            "toc_pages_pdf": data.get("toc_pages_pdf", []),
            "toc_to_pdf_offset": data.get("toc_to_pdf_offset"),
            "root": root,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    title = html.escape(data["book_title"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · 树状图谱</title>
  <style>
{render_css()}
  </style>
</head>
<body>
{render_markup(title, stats, payload)}
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve() if args.output else input_path.with_name("content_tree_graph.html")
    svg_output_path = args.svg_output.resolve() if args.svg_output else output_path.with_suffix(".svg")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.write_text(render_html(data), encoding="utf-8")
    svg_output_path.write_text(render_static_svg(data), encoding="utf-8")
    print(output_path)
    print(svg_output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
