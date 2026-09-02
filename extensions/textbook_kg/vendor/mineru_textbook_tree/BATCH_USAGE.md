# 批量跑书使用说明

现在推荐把“每本书的参数”写进书单配置，而不是每次手敲一长串命令。

## 1. 配置文件放哪

优先使用：

- `config/books.local.yaml`

这个文件已经被 `.gitignore` 忽略，适合放你自己机器上的绝对路径。

仓库里也提供了一个模板：

- `config/books.example.yaml`

## 2. 配置文件长什么样

```yaml
defaults:
  output_root: ./output
  chunk_size: 20
  lang: ch
  backend: pipeline

books:
  - id: modern_os4
    pdf: C:/无纸化/计科与软工院/操作系统/现代操作系统第4版.pdf
    book_title: 现代操作系统第4版
    toc_pages: 10-16
    page_offset: 16

  - id: kurose7
    pdf: C:/无纸化/计科与软工院/计算机通信与网络/Kurose-2018-计算机网络：自顶向下方法-7th Edition_.pdf
    book_title: 计算机网络自顶向下方法第7版
    toc_pages: 15-18
    page_offset: 18
```

字段说明：

- `id`: 书的短 ID，后面跑单本时用
- `pdf`: PDF 路径
- `book_title`: 输出里显示的书名
- `toc_pages`: 目录页在 PDF 里的范围
- `page_offset`: 目录页码到 PDF 实际页码的偏移
- `output_root`: 输出根目录，通常保持 `./output`
- `chunk_size`: MinerU 分块页数

如果不写 `output_dir`，脚本会自动生成：

- `output/<书名>_tree`

## 3. 怎么跑

先进入项目根目录：

```powershell
cd "$env:USERPROFILE\Desktop\mineru_textbook_tree"
```

### 跑单本

```powershell
.\run_books.ps1 -BookId modern_os4
```

### 跑全部

```powershell
.\run_books.ps1 -All
```

### 强制重新 OCR

```powershell
.\run_books.ps1 -BookId modern_os4 -ForceOcr
```

### 跳过已经产生成果的书

```powershell
.\run_books.ps1 -All -SkipExisting
```

### 先看命令，不真正执行

```powershell
.\run_books.ps1 -BookId modern_os4 -DryRun
```

## 4. Python 入口

如果你不想用 PowerShell，也可以直接用 Python：

```powershell
.\.venv-mineru\python.exe .\scripts\run_book_jobs.py --book-id modern_os4
```

批量跑：

```powershell
.\.venv-mineru\python.exe .\scripts\run_book_jobs.py --all
```

指定别的配置文件：

```powershell
.\.venv-mineru\python.exe .\scripts\run_book_jobs.py --manifest .\config\books.example.yaml --book-id sample_book
```

## 5. 自动纠错层现在做了什么

目录树不是直接“识别完就结束”，中间还有一层自动纠错：

1. 章号修复
   例如 OCR 把 `第10章` 识别成 `第0章`，或把 `第13章` 识别成 `第1章`，会根据前后章序自动补正。

2. 跨章误挂过滤
   如果当前在第 11 章，却识别出 `1.1.4`，会先尝试修成 `11.1.4`；修不通就丢弃。

3. 深层编号补全
   像 `3.2.1.4.5` 这种多层编号会按编号前缀挂树，不再只依赖 OCR 给的 level。

4. 标题吞数字修复
   像 `11.1.120世纪80年代` 这种，会自动修成 `11.1.1`，并把 `20` 还给标题。

5. 缺失兄弟节点补回
   如果 OCR 把 `13.1.10` 和 `13.1.11` 黏成一条，但后面直接出现 `13.1.12`，会尝试从标题尾部拆出隐藏的小节。

这层逻辑都在：

- `scripts/mineru_toc_content_tree.py`

回归测试在：

- `tests/test_toc_hierarchy.py`
