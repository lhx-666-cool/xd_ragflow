# mineru_textbook_tree 使用教程

这份教程是给你自己直接使用 `C:\Users\ki\Desktop\mineru_textbook_tree` 这套项目准备的。

目标很简单：

1. 用项目内置的 MinerU 对教材 PDF 做 OCR
2. 生成带正文内容的章节树
3. 生成树状图谱 `HTML / SVG`

## 1. 先确认项目目录

项目目录应该在这里：

```text
C:\Users\ki\Desktop\mineru_textbook_tree
```

这个目录里已经包含：

- `.\.venv-mineru`
- `.\.mineru_cache`
- `.\scripts`
- `.\run_mineru_ocr_tree.ps1`

也就是说，这套项目默认就是“自带 MinerU 环境”的，不需要你再单独配置一次。

## 2. 打开 PowerShell

进入项目目录：

```powershell
cd C:\Users\ki\Desktop\mineru_textbook_tree
```

如果 PowerShell 不让你执行 `.ps1` 脚本，就先在当前窗口临时放开：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

这只对当前 PowerShell 会话生效，关掉窗口就恢复。

## 3. 最推荐的运行方式

直接用项目根目录下这个一键脚本：

```text
run_mineru_ocr_tree.ps1
```

它会自动完成：

1. 调用项目内置 MinerU 对 PDF 做 OCR
2. 生成 `content_tree.json`
3. 生成树图 `content_tree_graph.html`
4. 生成静态大图 `content_tree_graph.svg`
5. 生成正文浏览版 `content_tree_visual.html`

## 4. 通用命令模板

```powershell
.\run_mineru_ocr_tree.ps1 `
  -Pdf "C:\你的教材.pdf" `
  -OutputDir ".\output\你的教材_tree" `
  -BookTitle "你的教材" `
  -TocPages "目录页范围" `
  -PageOffset 页码偏移 `
  -ChunkSize 20 `
  -ForceOcr
```

### 参数含义

- `-Pdf`
  教材 PDF 的完整路径
- `-OutputDir`
  输出目录
- `-BookTitle`
  书名，会写进树根节点
- `-TocPages`
  目录在 PDF 中的页码范围，例如 `"7-10"`
- `-PageOffset`
  目录页码和 PDF 实际页码的偏移量
- `-ChunkSize`
  MinerU 分块 OCR 的页数，推荐 `20`
- `-ForceOcr`
  强制重新 OCR，即使已经跑过一遍

## 5. 这本《运筹学》的实际命令

你这本书目前可以直接这样跑：

```powershell
.\run_mineru_ocr_tree.ps1 `
  -Pdf "C:\无纸化\经管院\运筹学清华大学第四版书本.pdf" `
  -OutputDir ".\output\运筹学清华大学第四版书本_tree" `
  -BookTitle "运筹学清华大学第四版书本" `
  -TocPages "9-16" `
  -PageOffset 14 `
  -ChunkSize 20 `
  -ForceOcr
```

说明：

- `TocPages` 是 `9-16`
- `PageOffset` 是 `14`

也就是说目录里写的正文页码，加上 `14` 才是 PDF 里的实际页码。

## 6. 如果你已经跑过 OCR，不想再重跑

如果输出目录里已经有：

```text
.\output\你的教材_tree\merged_content_list.json
```

那你可以跳过 OCR，只重建树和正文切分。

命令如下：

```powershell
.\.venv-mineru\python.exe .\scripts\mineru_toc_content_tree.py `
  --pdf "C:\你的教材.pdf" `
  --output-dir ".\output\你的教材_tree" `
  --book-title "你的教材" `
  --skip-ocr `
  --content-list ".\output\你的教材_tree\merged_content_list.json" `
  --toc-pages "目录页范围" `
  --page-offset 页码偏移
```

这个模式适合：

- 只修目录页范围
- 只修页码偏移
- 只修树结构切分
- 不想再耗时重跑整本 OCR

## 7. 只重渲染图谱时怎么做

### 重新生成树状图谱

```powershell
.\.venv-mineru\python.exe .\scripts\render_content_tree_graph.py `
  --input ".\output\你的教材_tree\content_tree.json" `
  --output ".\output\你的教材_tree\content_tree_graph.html" `
  --svg-output ".\output\你的教材_tree\content_tree_graph.svg"
```

### 重新生成正文浏览版

```powershell
.\.venv-mineru\python.exe .\scripts\render_content_tree_visual.py `
  --input ".\output\你的教材_tree\content_tree.json" `
  --output ".\output\你的教材_tree\content_tree_visual.html"
```

## 8. 输出文件怎么看

生成完成后，主要看这些文件：

- `content_tree.json`
  最核心的结构化结果
- `content_tree.txt`
  树状文本，带正文
- `content_tree.md`
  Markdown 版本
- `content_tree_graph.html`
  交互式树图
- `content_tree_graph.svg`
  静态大图，适合截图和打印
- `content_tree_visual.html`
  带正文面板的浏览版

例如《运筹学》这本书的输出目录是：

```text
C:\Users\ki\Desktop\mineru_textbook_tree\output\运筹学清华大学第四版书本_tree
```

其中最常打开的是：

- `content_tree_graph.html`
- `content_tree_graph.svg`
- `content_tree_visual.html`

## 9. 怎么知道自己这次真的调用了 MinerU

如果你走的是 PDF 主流程，终端里会出现类似这些日志：

- `Using MinerU executable: ...`
- `Using MinerU method: ocr`
- `Running MinerU on PDF pages ...`

只要看到这些，就说明这次确实是在调用 MinerU OCR。

## 10. 怎么确定 `TocPages`

`TocPages` 指的是目录在 PDF 里的页码范围，不是印刷页码。

最简单的方法：

1. 在 PDF 阅读器里打开目录页
2. 看阅读器当前显示的是 PDF 第几页
3. 记下目录开始页和结束页

例如：

- 目录从 PDF 第 9 页开始
- 到 PDF 第 16 页结束

那就是：

```text
-TocPages "9-16"
```

## 11. 怎么确定 `PageOffset`

公式就是：

```text
PDF 实际页码 = 目录印刷页码 + PageOffset
```

例如目录里写：

- `第1章 ... 3`

但你在 PDF 书签或正文里看到第 1 章实际从 PDF 第 `17` 页开始，
那么：

```text
17 - 3 = 14
```

所以：

```text
-PageOffset 14
```

## 12. 常见问题

### 问题 1：脚本报“找不到 mineru”

先确认这两个目录还在：

```text
C:\Users\ki\Desktop\mineru_textbook_tree\.venv-mineru
C:\Users\ki\Desktop\mineru_textbook_tree\.mineru_cache
```

如果在，就说明项目环境还完整。

### 问题 2：`.ps1` 脚本无法运行

先执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

然后再重跑。

### 问题 3：重新跑太慢

如果 OCR 已经跑过，不要再用 `-ForceOcr`。
直接重建树，或者只重渲染图谱，会快很多。

### 问题 4：树里缺章节

优先检查：

- `TocPages` 是否把目录最后一页漏掉了
- `PageOffset` 是否填对了
- `merged_content_list.json` 里是否已经识别出对应目录文字

### 问题 5：打开 HTML 还是旧图

不要只刷新旧标签页。
直接重新打开输出目录里的新文件路径。

## 13. 你最该记住的两条命令

### 从 PDF 重新 OCR

```powershell
.\run_mineru_ocr_tree.ps1 `
  -Pdf "C:\你的教材.pdf" `
  -OutputDir ".\output\你的教材_tree" `
  -BookTitle "你的教材" `
  -TocPages "目录页范围" `
  -PageOffset 页码偏移 `
  -ChunkSize 20 `
  -ForceOcr
```

### 已有 OCR 结果，只重建树

```powershell
.\.venv-mineru\python.exe .\scripts\mineru_toc_content_tree.py `
  --pdf "C:\你的教材.pdf" `
  --output-dir ".\output\你的教材_tree" `
  --book-title "你的教材" `
  --skip-ocr `
  --content-list ".\output\你的教材_tree\merged_content_list.json" `
  --toc-pages "目录页范围" `
  --page-offset 页码偏移
```

如果你后面想让我继续补，我建议下一步补一个更省事的“双击填写参数版”，比如运行脚本后弹出输入框，让你只填 PDF 路径、目录页和偏移量就能跑。
