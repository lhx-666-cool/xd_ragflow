# 服务器批量跑书说明

这份说明对应 Linux 服务器场景，目标是：

- 把十几本教材一次性写进书单配置
- 用一条命令顺序跑完全部教材
- 中途中断后可以继续跑，不必从头开始

## 1. 推荐目录结构

建议把代码、PDF、输出分开：

```text
/opt/mineru_textbook_tree          # 项目代码
/data/books                        # 教材 PDF
/data/output                       # 如需单独存输出，可改配置指向这里
```

## 2. 复制一份服务器书单

项目里已经提供模板：

- `config/books.server.example.yaml`

建议在服务器上复制成真正要用的配置：

```bash
cp config/books.server.example.yaml config/books.server.yaml
```

然后把十几本教材都填进去。

示例：

```yaml
defaults:
  output_root: ./output
  chunk_size: 8
  lang: ch
  backend: pipeline
  force_ocr: false

books:
  - id: modern_os4
    pdf: /data/books/modern_os4.pdf
    book_title: 现代操作系统第4版
    toc_pages: 10-16
    page_offset: 16

  - id: kurose7
    pdf: /data/books/kurose7.pdf
    book_title: 计算机网络自顶向下方法第7版
    toc_pages: 15-18
    page_offset: 18
```

字段说明：

- `id`: 书的短 ID，后面单独补跑时会用到
- `pdf`: 服务器上的 PDF 路径
- `book_title`: 输出展示名称
- `toc_pages`: 目录页在 PDF 中的页码范围
- `page_offset`: 目录页码到 PDF 实页的偏移
- `chunk_size`: 每次给 MinerU 的页数，服务器上建议先用 `8`

## 3. 一条命令跑全部

先进入项目目录并激活环境：

```bash
cd /opt/mineru_textbook_tree
source .venv/bin/activate
```

然后直接跑：

```bash
python scripts/run_book_jobs.py --manifest config/books.server.yaml --all
```

## 4. 常用变体

只跑一本：

```bash
python scripts/run_book_jobs.py --manifest config/books.server.yaml --book-id modern_os4
```

跳过已经跑完的书：

```bash
python scripts/run_book_jobs.py --manifest config/books.server.yaml --all --skip-existing
```

强制全部重新 OCR：

```bash
python scripts/run_book_jobs.py --manifest config/books.server.yaml --all --force-ocr
```

先检查命令，不真正执行：

```bash
python scripts/run_book_jobs.py --manifest config/books.server.yaml --all --dry-run
```

## 5. 推荐后台运行方式

如果十几本书要跑很久，建议不要挂在前台。

### 用 tmux

```bash
tmux new -s textbooks
cd /opt/mineru_textbook_tree
source .venv/bin/activate
python scripts/run_book_jobs.py --manifest config/books.server.yaml --all --skip-existing
```

退出但保持运行：

```bash
Ctrl+b 然后按 d
```

重新连接：

```bash
tmux attach -t textbooks
```

### 用 nohup

```bash
mkdir -p logs
nohup bash -lc 'cd /opt/mineru_textbook_tree && source .venv/bin/activate && python scripts/run_book_jobs.py --manifest config/books.server.yaml --all --skip-existing' > logs/run_all_books.log 2>&1 &
```

看日志：

```bash
tail -f logs/run_all_books.log
```

## 6. 服务器上为什么建议 chunk_size=8

MinerU 在某些扫描版教材上会吃掉比较多内存，尤其是版面排序模型加载时。

所以服务器上更稳的建议是：

- 默认 `chunk_size: 8`
- 内存很充足时再慢慢升到 `10` 或 `12`
- 如果某本书特别大或图片很多，就单独把那本书写成 `chunk_size: 4`

## 7. 断点续跑策略

这个批量入口已经支持“跳过已有结果”。

也就是说：

- 第一次跑：`--all`
- 断了以后继续：`--all --skip-existing`

只要某本书已经产出了 `content_tree.json`，后续就会跳过，不会重头再跑。

## 8. 推荐的实际流程

建议按这个顺序：

1. 先把十几本书都放到 `/data/books`
2. 写好 `config/books.server.yaml`
3. 先执行一次 `--dry-run` 检查路径
4. 再执行 `--all --skip-existing`
5. 用 `tmux` 或 `nohup` 挂后台
