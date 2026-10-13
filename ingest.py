"""
数据入库模块 - 将新闻、播客、Obsidian 笔记嵌入并存入 ChromaDB
用法: python ingest.py --init   # 最近半年全量回填
      python ingest.py           # 增量更新（每周自动调用）
"""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

# ── Windows UTF-8 编码修复 ─────────────────────────────────
if sys.platform == "win32":
    import io
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import chromadb
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chroma_client import get_client

SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

CHROMA_PATH = Path(config["chromadb"]["path"])
EMBED_MODEL = config["chromadb"]["embedding_model"]
OLLAMA_BATCH_URL = "http://localhost:11434/api/embed"  # 批量接口
TRENDRADAR_DIR = Path(config.get("trendradar_dir", str(SCRIPT_DIR / "TrendRadar")))
VAULT_PATH = Path(config["obsidian_vault_path"])
PODCAST_DIR = Path(config["podcast_output_dir"])

BATCH_SIZE = 50  # 每批嵌入条数

# 只索引入 AI/技术相关平台
AI_PLATFORMS = {
    "华尔街见闻", "AI热点", "掘金", "少数派",
    "Product Hunt", "GitHub Trending", "IT之家",
    "知乎", "bilibili 热搜", "今日头条", "澎湃新闻",
}


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量调用 Ollama 嵌入，返回多条 768 维向量。"""
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = Request(OLLAMA_BATCH_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["embeddings"]


def content_hash(text: str) -> str:
    """内容 MD5 去重。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _flush_batch(collection, ids, embeddings, documents, metadatas):
    """将缓存的批次写入 ChromaDB。"""
    if ids:
        collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
        return len(ids)
    return 0


# ─── 新闻入库 ───────────────────────────────────────────────

def add_weekly_news():
    """从 AI HOT RSS 抓取新闻并增量入库（替代 TrendRadar）。"""
    import aihot_fetch
    aihot_fetch.ingest_to_chromadb()


# ─── 播客入库 ───────────────────────────────────────────────

def add_weekly_podcasts():
    """将播客转录文字嵌入并入库。"""
    if not PODCAST_DIR.exists():
        print("⚠ 播客目录为空，跳过")
        return

    client = get_client()
    collection = client.get_or_create_collection(
        name="podcasts", metadata={"hnsw:space": "cosine"},
    )

    texts, hashes, metas = [], [], []
    skipped = 0

    for txt_file in PODCAST_DIR.rglob("*.txt"):
        content = txt_file.read_text(encoding="utf-8").strip()
        if not content:
            continue
        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) >= 50]
        for i, para in enumerate(paragraphs):
            h = content_hash(para)
            existing = collection.get(ids=[h])
            if existing["ids"]:
                skipped += 1
                continue
            texts.append(para)
            hashes.append(h)
            metas.append({
                "source": txt_file.name, "chunk_index": i,
                "date": datetime.now().strftime("%Y-%m-%d"),
            })

    if not texts:
        print(f"📭 播客: {skipped} 个段落全部已存在，无新增")
        return

    added = 0
    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts = texts[i:i + BATCH_SIZE]
        batch_hashes = hashes[i:i + BATCH_SIZE]
        batch_metas = metas[i:i + BATCH_SIZE]
        embeddings = embed_batch(batch_texts)
        collection.add(ids=batch_hashes, embeddings=embeddings, documents=batch_texts, metadatas=batch_metas)
        added += len(batch_texts)
        print(f"  🎙️ 播客: {added}/{len(texts)} 已嵌入")

    print(f"✅ 播客入库: {added} 个段落新增，{skipped} 个跳过")


# ─── Obsidian 入库 ──────────────────────────────────────────

def refresh_obsidian_vault():
    """增量更新 Obsidian vault 索引。"""
    if not VAULT_PATH.exists():
        print(f"⚠ Obsidian vault 不存在: {VAULT_PATH}，跳过")
        return

    client = get_client()
    collection = client.get_or_create_collection(
        name="obsidian", metadata={"hnsw:space": "cosine"},
    )

    texts, hashes, metas = [], [], []
    skipped = 0

    for md_file in VAULT_PATH.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if not content.strip():
            continue
        sections = content.split("\n## ")
        for i, section in enumerate(sections):
            section = section.strip()
            if len(section) < 30:
                continue
            h = content_hash(section)
            existing = collection.get(ids=[h])
            if existing["ids"]:
                skipped += 1
                continue
            texts.append(section)
            hashes.append(h)
            metas.append({
                "file": str(md_file.relative_to(VAULT_PATH)),
                "section_index": i,
                "date": datetime.now().strftime("%Y-%m-%d"),
            })

    if not texts:
        print(f"📭 Obsidian: {skipped} 段均已存在，无新增")
        return

    added = 0
    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts = texts[i:i + BATCH_SIZE]
        batch_hashes = hashes[i:i + BATCH_SIZE]
        batch_metas = metas[i:i + BATCH_SIZE]
        embeddings = embed_batch(batch_texts)
        collection.add(ids=batch_hashes, embeddings=embeddings, documents=batch_texts, metadatas=batch_metas)
        added += len(batch_texts)
        print(f"  📝 Obsidian: {added}/{len(texts)} 已嵌入")

    print(f"✅ Obsidian 入库: {added} 段新增，{skipped} 段跳过（已存在）")


# ─── 外部数据入库（项目三 + Agent-Reach）─────────────────────

# 项目三 output 目录路径
CONTENT_FARM_OUTPUT = Path(config.get("content_farm_output_dir",
    str(SCRIPT_DIR.parent / "ai-content-farm" / "output")))


def add_external_raw():
    """读取项目三 + agent-reach 的 raw JSON，增量入库到 content_farm collection。"""
    if not CONTENT_FARM_OUTPUT.exists():
        print(f"⚠ 项目三 output 目录不存在: {CONTENT_FARM_OUTPUT}，跳过外源数据")
        return

    # 匹配 *_raw.json 和 *_external.json
    json_files = (
        sorted(CONTENT_FARM_OUTPUT.glob("*_raw.json")) +
        sorted(CONTENT_FARM_OUTPUT.glob("*_external.json"))
    )
    if not json_files:
        print("📭 无外源数据文件，跳过")
        return

    client = get_client()
    collection = client.get_or_create_collection(
        name="content_farm", metadata={"hnsw:space": "cosine"},
    )

    # 读取已存在 URL 用于去重
    existing_urls = set()
    try:
        all_data = collection.get(include=["metadatas"])
        for meta in all_data["metadatas"]:
            url = meta.get("url", "")
            if url:
                existing_urls.add(url)
    except Exception:
        pass

    all_texts, all_hashes, all_metas = [], [], []
    seen_hashes = set()
    total_skipped = 0
    url_skipped = 0

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取失败 {json_file.name}: {e}")
            continue

        for item in items:
            title = item.get("title", "") or ""
            content = item.get("content", "") or ""
            url = item.get("url", "") or ""
            combined = f"{title}\n\n{content}"
            if len(combined.strip()) < 20:
                continue

            # URL 去重
            if url and url in existing_urls:
                url_skipped += 1
                total_skipped += 1
                continue

            h = content_hash(combined)
            if h in seen_hashes:
                total_skipped += 1
                continue
            seen_hashes.add(h)
            existing_urls.add(url)

            all_texts.append(combined)
            all_hashes.append(h)
            all_metas.append({
                "title": title,
                "source": item.get("source", ""),
                "source_type": item.get("source_type", ""),
                "url": url,
                "fetched_at": item.get("fetched_at", ""),
            })

    if not all_texts:
        print(f"📭 外源数据: {total_skipped} 条全部已存在，无新增")
        return

    # 1) 先全部嵌入
    all_embeddings = []
    for i in range(0, len(all_texts), BATCH_SIZE):
        batch_texts = all_texts[i:i + BATCH_SIZE]
        try:
            embs = embed_batch(batch_texts)
            all_embeddings.extend(embs)
        except Exception as e:
            print(f"  ⚠️ 嵌入失败: {e}")
            continue

    if len(all_embeddings) != len(all_texts):
        print(f"  ⚠️ 嵌入数量不匹配，跳过语义去重")
        added = 0
        for i in range(0, len(all_texts), BATCH_SIZE):
            batch_texts = all_texts[i:i + BATCH_SIZE]
            batch_hashes = all_hashes[i:i + BATCH_SIZE]
            batch_metas = all_metas[i:i + BATCH_SIZE]
            try:
                collection.add(
                    ids=batch_hashes,
                    embeddings=all_embeddings[i:i + BATCH_SIZE] if i < len(all_embeddings) else embed_batch(batch_texts),
                    documents=batch_texts,
                    metadatas=batch_metas,
                )
                added += len(batch_texts)
            except Exception as e:
                print(f"  ⚠️ 入库失败: {e}")
                continue
        print(f"✅ 外源数据入库（降级）: {added} 条新增，{total_skipped} 条跳过")
        return

    # 2) 语义去重：检查每一条是否与库中已有内容高度相似
    semantic_dup = 0
    final_texts, final_hashes, final_metas, final_embs = [], [], [], []
    for idx in range(len(all_texts)):
        try:
            res = collection.query(query_embeddings=[all_embeddings[idx]], n_results=1)
            if res["distances"] and res["distances"][0]:
                similarity = 1 - res["distances"][0][0]
                if similarity >= 0.95:
                    semantic_dup += 1
                    continue
        except Exception:
            pass
        final_texts.append(all_texts[idx])
        final_hashes.append(all_hashes[idx])
        final_metas.append(all_metas[idx])
        final_embs.append(all_embeddings[idx])

    # 3) 入库非重复内容
    added = 0
    for i in range(0, len(final_texts), BATCH_SIZE):
        batch_ids = final_hashes[i:i + BATCH_SIZE]
        batch_texts = final_texts[i:i + BATCH_SIZE]
        batch_metas = final_metas[i:i + BATCH_SIZE]
        batch_embs = final_embs[i:i + BATCH_SIZE]
        try:
            collection.add(ids=batch_ids, embeddings=batch_embs,
                documents=batch_texts, metadatas=batch_metas)
            added += len(batch_texts)
        except Exception as e:
            print(f"  ⚠️ 入库失败: {e}")
            continue

    print(f"✅ 外源数据入库: {added} 条新增，{total_skipped} 条 URL 去重跳过，{semantic_dup} 条语义去重跳过")


# ─── CLI ────────────────────────────────────────────────────

def main():
    import sys

    if "--init" in sys.argv:
        print("🔧 全量回填模式\n")
        add_weekly_news()
        add_weekly_podcasts()
        refresh_obsidian_vault()
        add_external_raw()
        print("\n🎉 全量回填完成")
    else:
        print("📥 增量更新模式\n")
        add_weekly_news()
        add_weekly_podcasts()
        refresh_obsidian_vault()
        add_external_raw()
        print("\n🎉 增量更新完成")


if __name__ == "__main__":
    main()
