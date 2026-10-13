# AI 每周新闻自动化

> AI Weekly News Automation — 抓取热点 → 大模型摘要 → 分类配图 → 生成播客 → 写入 Obsidian 并邮件推送

每周自动生成一份 AI 行业新闻周报。由 n8n 于**每周一 10:00** 触发，抓取 TrendRadar / AI HOT 的热点新闻，调用 DeepSeek 生成分类分析报告，自动配图、生成播客摘要，最终写入 Obsidian 笔记库并发送邮件。

## 功能概览

每周一自动运行，流程如下：

1. **抓取热点** — 从 TrendRadar / AI HOT API 抓取过去 7 天的精选新闻。
2. **大模型摘要与分类** — 调用 DeepSeek 生成详细的每周分析报告，按 7 个主题分类（大模型与训练、产品与应用、论文与开源、AI 安全与对齐、AI 硬件与算力、AI 行业人物、政策与伦理）。
3. **配图** — 为排名靠前的新闻生成 prompt 并调用 Agnes 云端生图 API 生成配图，插入报告。
4. **播客摘要** — 从播客 RSS 下载音频，用 Whisper 本地转录，再交给 DeepSeek 生成摘要，追加到报告。
5. **写入 Obsidian** — 报告保存为 Markdown 写入配置好的 Obsidian vault 子目录。
6. **发送邮件** — 通过 SMTP（QQ 邮箱）把报告发到指定收件箱。
7. **向量入库** — 把新闻、播客转录、Obsidian 笔记、外源数据嵌入向量库（FAISS + SQLite）供后续语义检索。

## 管线模块

| 文件 | 作用 |
|------|------|
| `weekly_report.py` | 主入口：加载数据 → DeepSeek 分析 → 配图 → 播客 → 写 Obsidian → 发邮件 |
| `aihot_fetch.py` | 从 AI HOT API 抓取过去 7 天精选新闻并增量入库 `news` 集合 |
| `ingest.py` | 数据入库：新闻 / 播客 / Obsidian 笔记 / 外源数据 → 向量库 |
| `ingest_claude_memory.py` | 将本机 CLAUDE.md 与项目记忆文件嵌入 `claude_memory` 集合 |
| `ingest_memory.py` | 手动把一条「项目修改总结」写入 `claude_memory` |
| `query.py` | 语义搜索 / RAG 问答 / 智能关联 |
| `query_memory.py` | 对 `claude_memory` 集合做语义检索 |
| `image_generator.py` | DeepSeek 生成图片 prompt → Agnes API 生图 → 插入报告 |
| `podcast_pipeline.py` | 播客 RSS → 下载 → Whisper 转录 → DeepSeek 总结 |
| `do_podcast.py` | 手动处理单期播客（示例脚本） |
| `blog_pipeline.py` | 博客 RSS → 网页正文提取 → DeepSeek 总结 → Obsidian |
| `collect_external.py` | 采集外源社区热点（V2EX / Reddit / Twitter / GitHub / YouTube） |
| `chroma_client.py` / `vector_store.py` | 向量存储封装（FAISS + SQLite，兼容 ChromaDB 接口） |

## 安装

```bash
pip install -r requirements.txt
```

依赖 Ollama 提供本地嵌入模型（`nomic-embed-text`），请先启动 `ollama serve` 并拉取该模型：

```bash
ollama pull nomic-embed-text
```

播客转录依赖本机 ffmpeg 与可用 GPU（Whisper 使用 CUDA/FP16）。

## 配置

复制配置模板并填入你自己的密钥：

```bash
cp config.example.json config.json
```

在 `config.json` 中填写：DeepSeek API Key、Agnes API Key、邮箱（SMTP 主机/端口/账号/授权码/收件人）、Obsidian vault 路径、TrendRadar 目录、播客与图片输出目录等。

> `config.json` 已加入 `.gitignore`，不会被提交；请勿提交任何真实密钥。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FAISS_INDEX_DIR` | 向量索引/数据库目录（须为纯 ASCII 路径，FAISS C++ 不支持中文路径） | `~/.ai-faiss-index` |
| `CHROMA_DB_PATH` | 独立脚本 `ingest_memory.py` 使用的库目录 | 脚本同级 `chroma_db` |
| `TWITTER_CLI` | twitter CLI 路径覆盖 | 自动从 PATH 查找 `twitter` |
| `YTDLP_CLI` | yt-dlp 路径覆盖 | 自动从 PATH 查找 `yt-dlp` |

## 用法

```bash
python weekly_report.py            # 运行一次周报（同一天不重复执行）
python ingest.py --init            # 全量回填向量库
python ingest.py                   # 增量更新向量库
python aihot_fetch.py              # 只抓取并入库 AI HOT 新闻
python query.py --search "问题"     # 语义搜索
python query.py --ask "问题"        # RAG 问答
python collect_external.py         # 采集外源社区热点
```

## 许可

本项目基于 MIT 许可证开源，详见 [LICENSE](LICENSE)。

Copyright (c) 2026 buxiangcifan123
