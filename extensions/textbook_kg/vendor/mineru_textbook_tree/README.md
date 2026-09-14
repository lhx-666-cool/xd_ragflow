# MinerU OCR 教材树状图谱工具

这个项目的默认主流程是：

1. 用项目内置的 MinerU 对 PDF 做 OCR 识别
2. 从 OCR 结果里提取目录与正文
3. 构建带正文内容的章节树
4. 生成树状图谱 HTML、SVG 和正文浏览版 HTML

如果你输入的是 PDF，那么默认就是走 MinerU OCR。
只有你显式传 `--skip-ocr` 时，才会复用已有的 `content_list.json`。

## 目录结构

```text
mineru_textbook_tree/
├─ .venv-mineru/              # 项目内置 MinerU 运行环境
├─ .mineru_cache/             # 项目内置模型缓存
├─ run_mineru_ocr_tree.ps1    # 推荐直接使用的一键入口
├─ README.md
├─ requirements.txt
├─ scripts/
│  ├─ mineru_toc_content_tree.py
│  ├─ mineru_chapter_tree.py
│  ├─ render_content_tree_graph.py
│  └─ render_content_tree_visual.py
└─ tests/
   └─ fixtures/
      └─ sample_content_list.json
```

## 你该怎么用

最推荐的方式不是手敲很多参数，而是直接用项目根目录下这条一键脚本：

- `run_mineru_ocr_tree.ps1`

它会自动做三件事：

1. 调用项目内置 MinerU 对 PDF 做 `OCR`
2. 生成 `content_tree.json`
3. 再生成树状图谱 `HTML / SVG` 和正文浏览版 `HTML`

一键脚本默认生成的成品文件名是：

- `content_tree_graph.html`
- `content_tree_graph.svg`
- `content_tree_visual.html`

## 最常用命令

先进入项目目录：

```powershell
cd "$env:USERPROFILE\Desktop\mineru_textbook_tree"
```

然后直接运行：

```powershell
.\run_mineru_ocr_tree.ps1 `
  -Pdf "C:\无纸化\编程语言\412-数据结构\数据结构C语言版.pdf" `
  -OutputDir ".\output\数据结构C语言版_tree" `
  -BookTitle "数据结构C语言版" `
  -TocPages "7-10" `
  -PageOffset 11 `
  -ChunkSize 20 `
  -ForceOcr
```

这条命令会明确使用：

- 项目内置 `.\.venv-mineru\python.exe`
- 项目内置 `.\.venv-mineru\Scripts\mineru.exe`
- 项目内置 `.\.mineru_cache\`
- MinerU `OCR` 模式

## 输出结果

输出目录里最重要的几个文件：

- `content_tree.json`
- `content_tree.txt`
- `content_tree.md`
- `content_tree_graph.html`
- `content_tree_graph.svg`
- `content_tree_visual.html`

例如：

```text
.\output\数据结构C语言版_tree\
```

里面会有：

- `content_tree.json`
- `content_tree_graph.html`
- `content_tree_graph.svg`
- `content_tree_visual.html`

## 什么时候才会跳过 MinerU

默认不会跳过。

只有下面这种情况才会复用已有 OCR 结果：

```powershell
.\.venv-mineru\python.exe .\scripts\mineru_toc_content_tree.py `
  --pdf "C:\你的教材.pdf" `
  --output-dir ".\output\你的教材_tree" `
  --book-title "你的教材" `
  --skip-ocr `
  --content-list ".\output\你的教材_tree\merged_content_list.json" `
  --toc-pages 7-10 `
  --page-offset 11
```

这个模式只建议用于：

- 你已经跑过一次 MinerU OCR
- 现在只是修目录页范围
- 现在只是修页码偏移
- 现在只是重建树图和内容切分

如果你要的是“从 PDF 开始重新 OCR 识别”，就不要加 `--skip-ocr`。

## 手动脚本方式

如果你想不用一键脚本，也可以手动分两步：

### 1. 先用 MinerU OCR 生成章节树

```powershell
.\.venv-mineru\python.exe .\scripts\mineru_toc_content_tree.py `
  --pdf "C:\你的教材.pdf" `
  --output-dir ".\output\你的教材_tree" `
  --book-title "你的教材" `
  --toc-pages 7-10 `
  --page-offset 11 `
  --chunk-size 20 `
  --method ocr `
  --lang ch `
  --force-ocr
```

### 2. 再渲染成树状图谱

```powershell
.\.venv-mineru\python.exe .\scripts\render_content_tree_graph.py `
  --input ".\output\你的教材_tree\content_tree.json" `
  --output ".\output\你的教材_tree\你的教材_树状图谱.html" `
  --svg-output ".\output\你的教材_tree\你的教材_树状图谱.svg"
```

### 3. 再生成正文浏览版

```powershell
.\.venv-mineru\python.exe .\scripts\render_content_tree_visual.py `
  --input ".\output\你的教材_tree\content_tree.json" `
  --output ".\output\你的教材_tree\你的教材_章节图谱可视化.html"
```

## 参数解释

### `-Pdf` / `--pdf`

要处理的教材 PDF 路径。

### `-OutputDir` / `--output-dir`

输出目录。

### `-BookTitle` / `--book-title`

根节点标题，也会用于输出成品文件名。

### `-TocPages` / `--toc-pages`

目录页在 PDF 中的页码范围。
例如：

```text
7-10
```

### `-PageOffset` / `--page-offset`

目录页码和 PDF 实际页码的偏移量。
例如目录页 1 对应 PDF 第 12 页，则偏移量是：

```text
11
```

### `-ChunkSize` / `--chunk-size`

MinerU 分块 OCR 的页数。

经验上：

- 普通书：`20`
- 比较重的扫描书：`10` 到 `20`

### `-ForceOcr` / `--force-ocr`

强制重新调用 MinerU OCR，即使输出目录里已经有旧的 `merged_content_list.json`。

如果你要确认“这次就是重新从 PDF OCR”，建议加上。

## 当前项目里的 MinerU 是怎么接入的

现在项目代码已经改成：

- 优先从项目根目录寻找 `.\.venv-mineru\Scripts\mineru.exe`
- 优先使用项目根目录下的 `.\.mineru_cache`
- `mineru_toc_content_tree.py` 默认 `--method ocr`
- 只有显式 `--skip-ocr` 才允许复用 `content_list.json`

也就是说，这个项目现在的默认行为已经是“PDF -> MinerU OCR -> 章节树 -> 图谱成品”。

## 常见问题

### 1. 我怎么确认这次真的调用了 MinerU？

现在主脚本会输出类似信息：

- `Using MinerU executable: ...`
- `Using MinerU method: ocr`
- `Running MinerU on PDF pages ...`

只要你是走 PDF 主流程，就会看到这些日志。

### 2. 为什么还保留 `--skip-ocr`？

因为有时候一本书 OCR 跑完很久，后面只是修目录切分，不值得重新 OCR。
但它现在已经不是默认路径，而是专门的“复用旧 OCR 结果”模式。

### 3. 我应该用哪个脚本？

日常直接用：

- `run_mineru_ocr_tree.ps1`

如果你要调试细节，再直接用：

- `scripts/mineru_toc_content_tree.py`

## 推荐命令模板

```powershell
cd "$env:USERPROFILE\Desktop\mineru_textbook_tree"

.\run_mineru_ocr_tree.ps1 `
  -Pdf "C:\你的教材.pdf" `
  -OutputDir ".\output\你的教材_tree" `
  -BookTitle "你的教材" `
  -TocPages "目录页范围" `
  -PageOffset 目录页码偏移 `
  -ChunkSize 20 `
  -ForceOcr
```

如果你愿意，下一步你只要给我一本 PDF 路径和目录页范围，我就可以继续帮你把这个桌面项目再调成更“傻瓜式”的双击运行版本。
