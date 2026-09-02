# 教材知识图谱扩展

本扩展把教材 PDF 转换为章节树和概念知识图谱，并把结果导入 RAGFlow 原生
GraphRAG。RAGFlow 主服务负责用户鉴权、读取知识库模型配置和展示结果；独立
Sidecar 负责 MinerU、章节树、概念抽取与产物管理。

## 用户流程

1. 在知识库上传窗口开启“构建教材知识图谱”。
2. RAGFlow 上传 PDF 后，使用当前知识库的 `embd_id` 和所属租户的 `llm_id`
   提交 Sidecar 任务。
3. 文档列表展示排队、构建、失败或完成状态，并提供重试、取消、章节树和产物下载。
4. 任务成功后，`ragflow_adapter.json` 自动导入 RAGFlow 原生 GraphRAG，可供图谱
   页面、检索测试和 GraphRAG 问答使用。

模型供应商的 API Key 始终由 RAGFlow 已有的模型配置管理。浏览器和 Sidecar 都不会
保存用户的供应商密钥；Sidecar 仅收到由 RAGFlow 签发、限定文档与模型的临时令牌。

## 目录

- `vendor/mineru_textbook_tree/`：章节树构建、质量审计、旧树修复、批量任务与渲染脚本。
- `vendor/concept_kg_module/`：教材概念 KG pipeline、Sidecar API 与离线测试。
- `Dockerfile`：CPU Sidecar 镜像；不重复安装 MinerU 模型。
- `Dockerfile.mineru`：可选的开源 MinerU 3.4.4 GPU API 镜像。
- `docker-compose.textbook-kg.yml`：MinerU 与 Sidecar 的部署编排。

## 配置与启动

```bash
cd extensions/textbook_kg
cp .env.sidecar.example .env.sidecar
chmod 600 .env.sidecar
mkdir -p runtime/jobs runtime/embedding-cache runtime/mineru-cache

# 请把示例 Token 换成随机值；不要填写 SiliconFlow/OpenAI API Key。
docker compose -f docker-compose.textbook-kg.yml config
docker compose -f docker-compose.textbook-kg.yml build
docker compose -f docker-compose.textbook-kg.yml up -d
```

默认使用 Linux `network_mode: host`：MinerU 监听 `127.0.0.1:8886`，Sidecar 监听
`127.0.0.1:8890`，均不暴露公网端口。若 RAGFlow 主服务运行在容器网络内，请相应调整
网络与 `TEXTBOOK_KG_API_URL`、`TEXTBOOK_KG_RAGFLOW_INTERNAL_URL`。

RAGFlow 后端从以下任一位置读取 Sidecar 配置：

- 环境变量 `TEXTBOOK_KG_API_URL` 和 `TEXTBOOK_KG_API_TOKEN`；
- `TEXTBOOK_KG_API_ENV_FILE` 指定的文件；
- 默认文件 `extensions/textbook_kg/.env.sidecar`。

## 接口

浏览器只访问经过 RAGFlow 登录鉴权的代理接口：

- `POST /v1/textbook_kg/submit`：接收 `doc_ids`，支持一次提交多个 PDF。
- `GET /v1/textbook_kg/job/{document_id}`：查询状态并在成功后触发 GraphRAG 导入。
- `POST /v1/textbook_kg/job/{document_id}/retry`：从有效检查点重试。
- `POST /v1/textbook_kg/job/{document_id}/cancel`：取消任务。
- `POST /v1/textbook_kg/job/{document_id}/import`：重试导入 GraphRAG。
- `GET /v1/textbook_kg/job/{document_id}/tree`：返回章节树。
- `GET /v1/textbook_kg/job/{document_id}/bundle`：下载完整产物。

Sidecar 的 `/v1/textbook-kg/jobs` 还支持直接上传 `content_tree.json`，用于跳过 OCR 的
重建和测试。任务使用 SQLite 状态库和单并发持久化队列；容器重启不会自动重复消耗
LLM 额度，中断任务需手动重试。

## 验证

```bash
PYTHONPATH=. python -m unittest \
  test.test_textbook_kg_service \
  test.test_textbook_kg_tree \
  test.test_textbook_kg_graphrag \
  test.test_textbook_kg_app -v

PYTHONPATH=extensions/textbook_kg/vendor/concept_kg_module \
  python -m unittest discover \
  -s extensions/textbook_kg/vendor/concept_kg_module/tests -v

cd web
npm run lint
npm run build
```

生产任务的默认输出语言遵循教材原文：中文教材使用中文实体名、类型、关系和描述；
仅原文中的英文术语、缩写或专名保留英文。
