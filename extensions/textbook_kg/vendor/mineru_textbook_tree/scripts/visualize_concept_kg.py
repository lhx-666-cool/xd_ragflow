from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a small concept KG preview as HTML and optional SVG.")
    parser.add_argument("--input", required=True, type=Path, help="Path to concept_kg.json")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML path")
    parser.add_argument("--svg-output", type=Path, default=None, help="Optional output SVG path")
    parser.add_argument("--max-nodes", type=int, default=30, help="Maximum number of nodes to render")
    return parser.parse_args()


def load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_subgraph(payload: dict[str, Any], max_nodes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities = payload.get("entities") or []
    relations = payload.get("relations") or []
    if len(entities) <= max_nodes:
        return entities, relations

    degree = Counter()
    for relation in relations:
        degree[relation.get("head_entity_id")] += 1
        degree[relation.get("tail_entity_id")] += 1

    sorted_entities = sorted(
        entities,
        key=lambda item: (-degree.get(item.get("entity_id"), 0), item.get("canonical_name", "").lower()),
    )
    selected_entities = sorted_entities[:max_nodes]
    selected_ids = {entity["entity_id"] for entity in selected_entities}
    selected_relations = [
        relation
        for relation in relations
        if relation.get("head_entity_id") in selected_ids and relation.get("tail_entity_id") in selected_ids
    ]
    return selected_entities, selected_relations


def layout_nodes(entities: list[dict[str, Any]], width: int = 1400, height: int = 1000) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    if not entities:
        return positions
    center_x = width / 2
    center_y = height / 2
    radius = min(width, height) * 0.36
    for index, entity in enumerate(entities):
        angle = (2 * math.pi * index) / len(entities)
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        positions[entity["entity_id"]] = (x, y)
    return positions


def build_svg(entities: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    width = 1400
    height = 1000
    positions = layout_nodes(entities, width=width, height=height)
    pieces: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<style>',
        '.bg{fill:#f8f3ea}.edge{stroke:#7b6a58;stroke-width:1.4;opacity:.75}.edge-label{font:12px sans-serif;fill:#6b4f3a}.node{fill:#fffaf2;stroke:#b57f50;stroke-width:2}.node-label{font:13px sans-serif;fill:#3d2d20;font-weight:600}.meta{font:12px sans-serif;fill:#725640}',
        '</style>',
        '<rect class="bg" x="0" y="0" width="1400" height="1000" rx="28" ry="28"/>',
        '<text class="meta" x="36" y="48">Concept KG Preview</text>',
    ]

    for relation in relations:
        head_pos = positions.get(relation["head_entity_id"])
        tail_pos = positions.get(relation["tail_entity_id"])
        if not head_pos or not tail_pos:
            continue
        mid_x = (head_pos[0] + tail_pos[0]) / 2
        mid_y = (head_pos[1] + tail_pos[1]) / 2
        pieces.append(
            f'<line class="edge" x1="{head_pos[0]:.1f}" y1="{head_pos[1]:.1f}" x2="{tail_pos[0]:.1f}" y2="{tail_pos[1]:.1f}"/>'
        )
        pieces.append(f'<text class="edge-label" x="{mid_x:.1f}" y="{mid_y - 6:.1f}" text-anchor="middle">{escape_xml(relation["relation"])}</text>')

    for entity in entities:
        x, y = positions[entity["entity_id"]]
        label = escape_xml(entity["canonical_name"])
        entity_type = escape_xml(entity.get("type", "Concept"))
        pieces.append(f'<circle class="node" cx="{x:.1f}" cy="{y:.1f}" r="36"/>')
        pieces.append(f'<text class="node-label" x="{x:.1f}" y="{y - 3:.1f}" text-anchor="middle">{label}</text>')
        pieces.append(f'<text class="meta" x="{x:.1f}" y="{y + 16:.1f}" text-anchor="middle">{entity_type}</text>')

    pieces.append("</svg>")
    return "\n".join(pieces)


def escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_html(svg: str, payload: dict[str, Any], entities: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    metadata = payload.get("metadata") or {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Concept KG Preview</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      background: linear-gradient(135deg, #efe2cd, #f8f4ec 45%, #e3cfb4);
      color: #2c2016;
    }}
    .wrap {{
      max-width: 1480px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }}
    .panel {{
      background: rgba(255, 250, 242, 0.88);
      border: 1px solid rgba(160, 120, 78, 0.18);
      border-radius: 22px;
      box-shadow: 0 18px 60px rgba(106, 77, 48, 0.12);
      overflow: hidden;
    }}
    .header {{
      padding: 22px 24px 10px;
    }}
    .stats {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      padding: 0 24px 20px;
      color: #6f533a;
      font-size: 14px;
    }}
    .stat {{
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(205, 175, 132, 0.18);
    }}
    .svg-box {{
      overflow: auto;
      border-top: 1px solid rgba(160, 120, 78, 0.16);
      background: rgba(255,255,255,0.52);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <div class="header">
        <h1 style="margin:0 0 10px;font-size:36px;">Concept Knowledge Graph</h1>
        <div style="font-size:15px;line-height:1.7;color:#604a36;">{escape_xml(str(metadata.get("source_document", "")))}</div>
      </div>
      <div class="stats">
        <div class="stat">Rendered nodes: {len(entities)}</div>
        <div class="stat">Rendered edges: {len(relations)}</div>
        <div class="stat">Total entities: {metadata.get("entity_count", len(payload.get("entities") or []))}</div>
        <div class="stat">Total relations: {metadata.get("relation_count", len(payload.get("relations") or []))}</div>
      </div>
      <div class="svg-box">{svg}</div>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    payload = load_payload(args.input)
    entities, relations = select_subgraph(payload, max_nodes=args.max_nodes)
    svg = build_svg(entities, relations)
    args.output.write_text(build_html(svg, payload, entities, relations), encoding="utf-8")
    if args.svg_output:
        args.svg_output.write_text(svg, encoding="utf-8")
    print(f"Preview written to {args.output}")


if __name__ == "__main__":
    main()
