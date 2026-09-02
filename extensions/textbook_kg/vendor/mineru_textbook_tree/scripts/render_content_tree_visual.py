from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an interactive HTML visualization from content_tree.json.")
    parser.add_argument("--input", required=True, type=Path, help="Path to content_tree.json.")
    parser.add_argument("--output", type=Path, help="Path to output HTML file. Defaults to content_tree_visual.html next to the input.")
    return parser.parse_args()


def assign_ids(nodes: list[dict[str, Any]], parent_id: str | None = None, prefix: str = "n") -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        node_id = f"{prefix}-{index}"
        children = assign_ids(node.get("children", []), node_id, node_id)
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
                "children": children,
            }
        )
    return indexed


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in nodes:
        items.append(node)
        items.extend(flatten(node.get("children", [])))
    return items


def compute_stats(data: dict[str, Any]) -> dict[str, int]:
    flat = flatten(data["chapters"])
    return {
        "node_count": len(flat),
        "chapter_count": len(data["chapters"]),
        "max_depth": max((int(node.get("level", 1) or 1) for node in flat), default=0),
        "content_chars": sum(len((node.get("content") or "").strip()) for node in flat),
    }


def render_html(data: dict[str, Any]) -> str:
    stats = compute_stats(data)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(data["book_title"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · 章节图谱</title>
  <style>
    :root {{
      --bg: #efe5d6;
      --bg-deep: #d7c1a5;
      --panel: rgba(255, 250, 242, 0.84);
      --panel-strong: rgba(255, 252, 247, 0.96);
      --ink: #2e261f;
      --muted: #76675d;
      --line: rgba(86, 62, 44, 0.15);
      --accent: #b25733;
      --accent-soft: rgba(178, 87, 51, 0.14);
      --accent-strong: #7f3b1f;
      --forest: #27453b;
      --forest-soft: rgba(39, 69, 59, 0.12);
      --shadow: 0 18px 48px rgba(60, 39, 23, 0.14);
      --radius: 24px;
    }}

    * {{
      box-sizing: border-box;
    }}

    html, body {{
      margin: 0;
      min-height: 100%;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(255, 255, 255, 0.55), transparent 32%),
        radial-gradient(circle at bottom right, rgba(39, 69, 59, 0.12), transparent 28%),
        linear-gradient(135deg, var(--bg), var(--bg-deep));
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}

    body {{
      padding: 22px;
    }}

    .app {{
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 18px;
      min-height: calc(100vh - 44px);
    }}

    .panel {{
      background: var(--panel);
      border: 1px solid rgba(255, 255, 255, 0.55);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      overflow: hidden;
    }}

    .sidebar {{
      display: flex;
      flex-direction: column;
    }}

    .hero {{
      padding: 24px 24px 18px;
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(160deg, rgba(255, 250, 242, 0.98), rgba(251, 239, 220, 0.86)),
        linear-gradient(120deg, var(--accent-soft), transparent 55%);
    }}

    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(39, 69, 59, 0.08);
      color: var(--forest);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .title {{
      margin: 14px 0 10px;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
      font-size: clamp(26px, 3vw, 38px);
      line-height: 1.16;
    }}

    .subtitle {{
      margin: 0;
      color: var(--muted);
      line-height: 1.65;
      font-size: 14px;
    }}

    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 18px;
    }}

    .stat-card {{
      padding: 12px 14px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.58);
      border: 1px solid rgba(255, 255, 255, 0.8);
    }}

    .stat-label {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }}

    .stat-value {{
      font-size: 22px;
      font-weight: 700;
      color: var(--accent-strong);
    }}

    .controls {{
      display: grid;
      gap: 12px;
      padding: 18px 20px 16px;
      border-bottom: 1px solid var(--line);
    }}

    .search {{
      width: 100%;
      padding: 13px 16px;
      border: 1px solid rgba(127, 59, 31, 0.16);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      font-size: 14px;
      outline: none;
      transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
    }}

    .search:focus {{
      border-color: rgba(178, 87, 51, 0.45);
      box-shadow: 0 0 0 4px rgba(178, 87, 51, 0.12);
      transform: translateY(-1px);
    }}

    .button-row {{
      display: flex;
      gap: 10px;
    }}

    button {{
      appearance: none;
      border: none;
      border-radius: 14px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 160ms ease, opacity 160ms ease, box-shadow 160ms ease;
    }}

    button:hover {{
      transform: translateY(-1px);
    }}

    .btn-primary {{
      background: linear-gradient(135deg, var(--accent), #cf845d);
      color: white;
      box-shadow: 0 10px 18px rgba(178, 87, 51, 0.22);
    }}

    .btn-secondary {{
      background: rgba(39, 69, 59, 0.1);
      color: var(--forest);
    }}

    .tree-scroll {{
      overflow: auto;
      padding: 12px 10px 18px 14px;
      flex: 1;
    }}

    .tree-node {{
      margin: 3px 0;
    }}

    .tree-row {{
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 4px 0;
      animation: fadeIn 220ms ease;
    }}

    .toggle {{
      width: 24px;
      height: 24px;
      border-radius: 10px;
      background: transparent;
      color: var(--muted);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      flex: 0 0 24px;
    }}

    .toggle:hover {{
      background: rgba(0, 0, 0, 0.05);
    }}

    .toggle.placeholder {{
      visibility: hidden;
      pointer-events: none;
    }}

    .node-button {{
      width: 100%;
      text-align: left;
      padding: 12px 14px;
      border-radius: 18px;
      background: transparent;
      color: inherit;
      display: grid;
      gap: 4px;
      border: 1px solid transparent;
    }}

    .node-button:hover {{
      background: rgba(255, 255, 255, 0.52);
      border-color: rgba(127, 59, 31, 0.08);
    }}

    .node-button.active {{
      background: linear-gradient(135deg, rgba(178, 87, 51, 0.14), rgba(39, 69, 59, 0.12));
      border-color: rgba(178, 87, 51, 0.18);
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.55);
    }}

    .node-label {{
      font-size: 14px;
      line-height: 1.45;
      font-weight: 600;
    }}

    .node-meta {{
      color: var(--muted);
      font-size: 12px;
    }}

    .children {{
      margin-left: 20px;
      padding-left: 10px;
      border-left: 1px dashed rgba(127, 59, 31, 0.16);
    }}

    .detail {{
      display: flex;
      flex-direction: column;
      min-width: 0;
      background:
        linear-gradient(180deg, rgba(255, 252, 247, 0.94), rgba(255, 248, 238, 0.9)),
        linear-gradient(145deg, rgba(39, 69, 59, 0.06), transparent 35%);
    }}

    .detail-header {{
      padding: 28px 28px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-strong);
    }}

    .breadcrumbs {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      margin-bottom: 12px;
    }}

    .detail-title {{
      margin: 0;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
      font-size: clamp(28px, 3vw, 42px);
      line-height: 1.14;
    }}

    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }}

    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(39, 69, 59, 0.08);
      color: var(--forest);
      font-size: 13px;
    }}

    .chip.alt {{
      background: rgba(178, 87, 51, 0.1);
      color: var(--accent-strong);
    }}

    .detail-scroll {{
      overflow: auto;
      padding: 22px 28px 28px;
      flex: 1;
    }}

    .content-card {{
      padding: 22px 24px;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.62);
      border: 1px solid rgba(255, 255, 255, 0.75);
      box-shadow: 0 12px 32px rgba(60, 39, 23, 0.08);
    }}

    .content-caption {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 13px;
      letter-spacing: 0.03em;
    }}

    .content-text {{
      margin: 0;
      font-size: 15px;
      line-height: 1.9;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", "STSong", serif;
    }}

    .empty {{
      color: var(--muted);
      font-style: italic;
    }}

    @keyframes fadeIn {{
      from {{
        opacity: 0;
        transform: translateY(3px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    @media (max-width: 980px) {{
      body {{
        padding: 12px;
      }}

      .app {{
        grid-template-columns: 1fr;
        min-height: auto;
      }}

      .sidebar {{
        min-height: 55vh;
      }}

      .detail {{
        min-height: 60vh;
      }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="panel sidebar">
      <div class="hero">
        <div class="eyebrow">Visual Knowledge Tree</div>
        <h1 class="title">{title}</h1>
        <p class="subtitle">基于 MinerU OCR 和目录结构构建的章节图谱。左侧浏览章节树，右侧查看对应小节的页码与正文内容。</p>
        <div class="stats">
          <div class="stat-card">
            <div class="stat-label">章节节点</div>
            <div class="stat-value">{stats["node_count"]}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">一级章节</div>
            <div class="stat-value">{stats["chapter_count"]}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">树深度</div>
            <div class="stat-value">{stats["max_depth"]}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">正文字符</div>
            <div class="stat-value">{stats["content_chars"]}</div>
          </div>
        </div>
      </div>

      <div class="controls">
        <input id="searchInput" class="search" type="search" placeholder="搜索章节名或编号，例如 7.5、排序、二叉树">
        <div class="button-row">
          <button id="expandAllButton" class="btn-primary" type="button">展开全部</button>
          <button id="collapseButton" class="btn-secondary" type="button">收起到章</button>
        </div>
      </div>

      <div id="treeRoot" class="tree-scroll"></div>
    </aside>

    <main class="panel detail">
      <div class="detail-header">
        <div id="breadcrumbs" class="breadcrumbs"></div>
        <h2 id="detailTitle" class="detail-title"></h2>
        <div class="chip-row">
          <div id="pdfChip" class="chip alt"></div>
          <div id="tocChip" class="chip"></div>
          <div id="levelChip" class="chip"></div>
        </div>
      </div>
      <div class="detail-scroll">
        <div class="content-card">
          <p class="content-caption">章节正文</p>
          <pre id="detailContent" class="content-text"></pre>
        </div>
      </div>
    </main>
  </div>

  <script id="tree-data" type="application/json">{payload}</script>
  <script>
    const data = JSON.parse(document.getElementById("tree-data").textContent);
    const treeRoot = document.getElementById("treeRoot");
    const searchInput = document.getElementById("searchInput");
    const breadcrumbsEl = document.getElementById("breadcrumbs");
    const detailTitleEl = document.getElementById("detailTitle");
    const detailContentEl = document.getElementById("detailContent");
    const pdfChipEl = document.getElementById("pdfChip");
    const tocChipEl = document.getElementById("tocChip");
    const levelChipEl = document.getElementById("levelChip");
    const expandAllButton = document.getElementById("expandAllButton");
    const collapseButton = document.getElementById("collapseButton");

    const state = {{
      query: "",
      selectedId: null,
      expanded: new Set(),
    }};

    const allNodes = [];
    const nodeMap = new Map();

    function walk(nodes, parent = null) {{
      nodes.forEach((node) => {{
        node.parent_id = parent ? parent.id : null;
        node.search_text = [node.marker, node.title, node.label].join(" ").toLowerCase();
        allNodes.push(node);
        nodeMap.set(node.id, node);
        walk(node.children || [], node);
      }});
    }}

    walk(data.chapters);

    data.chapters.forEach((node) => state.expanded.add(node.id));
    state.selectedId = data.chapters[0] ? data.chapters[0].id : null;

    function formatRange(start, end, prefix) {{
      if (start == null) return prefix + "：未记录";
      if (end == null || end === start) return prefix + "：" + start;
      return prefix + "：" + start + "-" + end;
    }}

    function buildBreadcrumbs(node) {{
      const chain = [];
      let current = node;
      while (current) {{
        chain.push(current.label);
        current = current.parent_id ? nodeMap.get(current.parent_id) : null;
      }}
      return chain.reverse().join(" / ");
    }}

    function updateDetail(node) {{
      breadcrumbsEl.textContent = buildBreadcrumbs(node);
      detailTitleEl.textContent = node.label;
      detailContentEl.textContent = node.content && node.content.trim() ? node.content : "(无正文内容)";
      detailContentEl.classList.toggle("empty", !(node.content && node.content.trim()));
      pdfChipEl.textContent = formatRange(node.pdf_page_start, node.pdf_page_end, "PDF页码");
      tocChipEl.textContent = formatRange(node.toc_page_start, node.toc_page_end, "目录页码");
      levelChipEl.textContent = "层级：" + node.level;
    }}

    function hasVisibleNode(nodes, query) {{
      return nodes.some((node) => {{
        const selfMatch = node.search_text.includes(query);
        return selfMatch || hasVisibleNode(node.children || [], query);
      }});
    }}

    function ensureVisibleSelection(visibleNodes) {{
      const visibleIds = new Set();
      (function collect(nodes) {{
        nodes.forEach((node) => {{
          visibleIds.add(node.id);
          collect(node.children || []);
        }});
      }})(visibleNodes);

      if (!visibleIds.has(state.selectedId)) {{
        state.selectedId = visibleNodes[0] ? visibleNodes[0].id : null;
      }}
    }}

    function filterNodes(nodes, query) {{
      if (!query) return nodes;
      return nodes
        .map((node) => {{
          const children = filterNodes(node.children || [], query);
          const selfMatch = node.search_text.includes(query);
          if (!selfMatch && !children.length) return null;
          return {{ ...node, children }};
        }})
        .filter(Boolean);
    }}

    function renderTree(nodes, container, depth = 0) {{
      nodes.forEach((node) => {{
        const wrapper = document.createElement("div");
        wrapper.className = "tree-node";

        const row = document.createElement("div");
        row.className = "tree-row";

        const hasChildren = (node.children || []).length > 0;
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "toggle" + (hasChildren ? "" : " placeholder");
        toggle.textContent = hasChildren ? (state.query ? "▾" : (state.expanded.has(node.id) ? "▾" : "▸")) : "•";
        if (hasChildren) {{
          toggle.addEventListener("click", () => {{
            if (state.expanded.has(node.id)) {{
              state.expanded.delete(node.id);
            }} else {{
              state.expanded.add(node.id);
            }}
            render();
          }});
        }}

        const button = document.createElement("button");
        button.type = "button";
        button.className = "node-button" + (state.selectedId === node.id ? " active" : "");
        button.style.marginLeft = (depth * 8) + "px";
        button.innerHTML = `
          <div class="node-label"></div>
          <div class="node-meta"></div>
        `;
        button.querySelector(".node-label").textContent = node.label;
        button.querySelector(".node-meta").textContent = formatRange(node.pdf_page_start, node.pdf_page_end, "PDF");
        button.addEventListener("click", () => {{
          state.selectedId = node.id;
          let current = node;
          while (current && current.parent_id) {{
            state.expanded.add(current.parent_id);
            current = nodeMap.get(current.parent_id);
          }}
          render();
        }});

        row.appendChild(toggle);
        row.appendChild(button);
        wrapper.appendChild(row);

        const showChildren = hasChildren && (state.query || state.expanded.has(node.id));
        if (showChildren) {{
          const childrenEl = document.createElement("div");
          childrenEl.className = "children";
          renderTree(node.children, childrenEl, depth + 1);
          wrapper.appendChild(childrenEl);
        }}

        container.appendChild(wrapper);
      }});
    }}

    function render() {{
      const query = state.query.trim().toLowerCase();
      const visibleTree = filterNodes(data.chapters, query);
      ensureVisibleSelection(visibleTree);
      treeRoot.innerHTML = "";
      renderTree(visibleTree, treeRoot);
      const selectedNode = state.selectedId ? nodeMap.get(state.selectedId) : null;
      if (selectedNode) {{
        updateDetail(selectedNode);
      }}
    }}

    searchInput.addEventListener("input", (event) => {{
      state.query = event.target.value || "";
      render();
    }});

    expandAllButton.addEventListener("click", () => {{
      allNodes.forEach((node) => {{
        if ((node.children || []).length) state.expanded.add(node.id);
      }});
      render();
    }});

    collapseButton.addEventListener("click", () => {{
      state.expanded.clear();
      data.chapters.forEach((node) => state.expanded.add(node.id));
      render();
    }});

    render();
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve() if args.output else input_path.with_name("content_tree_visual.html")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    raw["chapters"] = assign_ids(raw["chapters"])

    output_path.write_text(render_html(raw), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
