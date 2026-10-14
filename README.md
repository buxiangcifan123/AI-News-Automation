# AI Weekly News Automation

> An n8n-triggered pipeline that turns raw trending tech news into a daily/weekly AI report — summary, images, a Whisper-generated podcast, an Obsidian note and an email — fully automated.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/buxiangcifan123/AI-News-Automation/pulls)
[![GitHub stars](https://img.shields.io/github/stars/buxiangcifan123/AI-News-Automation?style=social)](https://github.com/buxiangcifan123/AI-News-Automation)

## Screenshots

> Add a GIF of the run + a sample report here — it's the fastest way for visitors to "get it".
<!--
![pipeline](docs/demo.gif)
![weekly report](docs/report.png)
-->

## What it does

Every Monday at 10:00 (via n8n), the pipeline:

1. **Collect** — fetches the last 7 days of curated AI news from TrendRadar / AI HOT.
2. **Summarize & classify** — asks DeepSeek for a detailed report grouped into 7 topics (LLMs & training, products & apps, papers & OSS, AI safety & alignment, AI hardware & compute, industry people, policy & ethics).
3. **Illustrate** — generates image prompts and calls the Agnes cloud image API, inserting images into the report.
4. **Podcast** — downloads podcast RSS audio, transcribes locally with Whisper, summarizes with DeepSeek.
5. **Store** — writes the report as Markdown into a configured Obsidian vault.
6. **Email** — sends the report via SMTP (QQ Mail).
7. **Index** — embeds news / transcripts / notes / external data into a FAISS + SQLite vector store for later semantic search.

## Pipeline modules

| File | Purpose |
|------|---------|
| `weekly_report.py` | Main entry: load data → DeepSeek analysis → images → podcast → Obsidian → email |
| `aihot_fetch.py` | Fetch last-7-days AI HOT news, incrementally index into `news` |
| `ingest.py` | Index news / podcasts / Obsidian notes / external data into the vector store |
| `ingest_claude_memory.py` | Embed local CLAUDE.md + project memory files into `claude_memory` |
| `ingest_memory.py` | Manually write one "change summary" into `claude_memory` |
| `query.py` | Semantic search / RAG Q&A / related-topic lookup |
| `query_memory.py` | Semantic search over the `claude_memory` collection |
| `image_generator.py` | DeepSeek image prompt → Agnes API → insert into report |
| `podcast_pipeline.py` | Podcast RSS → download → Whisper transcript → DeepSeek summary |
| `do_podcast.py` | Process a single podcast episode (example script) |
| `blog_pipeline.py` | Blog RSS → article extraction → DeepSeek summary → Obsidian |
| `collect_external.py` | Collect community hot posts (V2EX / Reddit / Twitter / GitHub / YouTube) |
| `chroma_client.py` / `vector_store.py` | Vector store wrapper (FAISS + SQLite, ChromaDB-compatible API) |

## Quick start

```bash
# 1. install Python deps
pip install -r requirements.txt

# 2. local embedding model (Ollama)
ollama serve
ollama pull nomic-embed-text

# 3. configure (never commit real keys)
cp config.example.json config.json   # then fill in your own keys

# 4. run once
python weekly_report.py
```

Podcast transcription additionally needs local `ffmpeg` and a usable GPU (Whisper runs with CUDA/FP16). Image generation needs an Agnes API key.

## Configuration

`config.json` holds your keys (DeepSeek, Agnes, SMTP mail), the Obsidian vault path, the TrendRadar directory, and the podcast/image output folders. `config.json` is git-ignored — **never commit real keys**.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `FAISS_INDEX_DIR` | Vector index / DB directory (must be a pure-ASCII path — FAISS C++ does not support non-ASCII paths) | `~/.ai-faiss-index` |
| `CHROMA_DB_PATH` | DB directory used by the standalone `ingest_memory.py` | `<script dir>/chroma_db` |
| `TWITTER_CLI` | Override the `twitter` CLI path | auto-detect `twitter` on PATH |
| `YTDLP_CLI` | Override the `yt-dlp` CLI path | auto-detect `yt-dlp` on PATH |

## Usage

```bash
python weekly_report.py            # run once (skips if already run today)
python ingest.py --init            # full backfill of the vector store
python ingest.py                   # incremental update
python aihot_fetch.py              # fetch + index AI HOT news only
python query.py --search "question"  # semantic search
python query.py --ask "question"     # RAG Q&A
python collect_external.py         # collect community hot posts
```

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 buxiangcifan123

---

# AI 每周新闻自动化

> 抓取热点 → 大模型摘要 → 分类配图 → 生成播客 → 写入 Obsidian 并邮件推送

每周自动生成一份 AI 行业新闻周报。由 n8n 于**每周一 10:00** 触发，抓取 TrendRadar / AI HOT 的热点新闻，调用 DeepSeek 生成分类分析报告，自动配图、生成播客摘要，最终写入 Obsidian 笔记库并发送邮件。

## 功能概览

1. **抓取热点** — 从 TrendRadar / AI HOT API 抓取过去 7 天的精选新闻。
2. **大模型摘要与分类** — 调用 DeepSeek 生成详细的每周分析报告，按 7 个主题分类（大模型与训练、产品与应用、论文与开源、AI 安全与对齐、AI 硬件与算力、AI 行业人物、政策与伦理）。
3. **配图** — 为排名靠前的新闻生成 prompt 并调用 Agnes 云端生图 API 生成配图，插入报告。
4. **播客摘要** — 从播客 RSS 下载音频，用 Whisper 本地转录，再交给 DeepSeek 生成摘要，追加到报告。
5. **写入 Obsidian** — 报告保存为 Markdown 写入配置好的 Obsidian vault 子目录。
6. **发送邮件** — 通过 SMTP（QQ 邮箱）把报告发到指定收件箱。
7. **向量入库** — 把新闻、播客转录、Obsidian 笔记、外源数据嵌入向量库（FAISS + SQLite）供后续语义检索。

管线模块表见上方英文部分（Pipeline modules）。

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
