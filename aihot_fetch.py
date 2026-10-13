"""
AI HOT 每日增量入库 — 从 API 抓取过去 7 天精选，去重后写入 ChromaDB news 集合
"""
import hashlib
import io
import json
import sys
import time
from datetime import datetime, timedelta

# Windows UTF-8 修复
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from urllib.request import Request, urlopen

import chromadb
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chroma_client import get_client

SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

EMBED_MODEL = config["chromadb"]["embedding_model"]
OLLAMA_BATCH_URL = "http://localhost:11434/api/embed"
API_BASE = "https://aihot.virxact.com/api/public/items"
BATCH_SIZE = 50
FETCH_DAYS = 7
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def embed_batch(texts: list[str]) -> list[list[float]]:
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = Request(OLLAMA_BATCH_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))["embeddings"]


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def fetch_all() -> list[dict]:
    """分页抓取过去 7 天精选，返回标准化条目列表"""
    since = (datetime.now() - timedelta(days=FETCH_DAYS)).strftime("%Y-%m-%d")
    items = []
    cursor = None
    page = 0

    while True:
        url = f"{API_BASE}?mode=selected&since={since}"
        if cursor:
            url += f"&cursor={cursor}"

        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        page += 1
        batch = data.get("items", [])
        items.extend(batch)
        print(f"  page {page}: {len(batch)} items")

        if not data.get("hasNext") or not data.get("nextCursor"):
            break
        cursor = data["nextCursor"]
        time.sleep(0.3)  # 礼貌间隔

    print(f"  API 共 {len(items)} 条（{since} ~ 今）")

    # 转标准化格式
    result = []
    cat_map = {
        "ai-models": "ai-models",
        "ai-products": "ai-products",
        "industry": "industry",
        "paper": "paper",
        "tip": "tip",
    }
    for item in items:
        date_str = ""
        if item.get("publishedAt"):
            try:
                dt = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        cat = item.get("category", "")
        result.append({
            "title": item.get("title", ""),
            "url": item.get("url", "") or item.get("permalink", ""),
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
            "category": cat_map.get(cat, cat),
            "date": date_str,
            "published_at": item.get("publishedAt", ""),
            "fetched_at": datetime.now().isoformat(),
        })

    return result


def ingest_to_chromadb():
    """抓取 API 并增量入库 news 集合"""
    client = get_client()
    collection = client.get_or_create_collection(
        name="news", metadata={"hnsw:space": "cosine"},
    )

    # 读取已有 URL 去重
    existing_urls = set()
    try:
        all_data = collection.get(include=["metadatas"])
        for meta in all_data["metadatas"]:
            url = meta.get("url", "")
            if url:
                existing_urls.add(url)
    except Exception:
        pass
    print(f"  库中已有 {len(existing_urls)} 条记录")

    items = fetch_all()
    print(f"  待处理: {len(items)} 条")

    ids, docs, metas = [], [], []
    url_skipped = 0
    for item in items:
        url = item["url"]
        if url and url in existing_urls:
            url_skipped += 1
            continue

        text = item["summary"] or item.get("title", "")
        if len(text) < 20:
            continue

        uid = content_hash(item["url"] or item["title"])
        ids.append(uid)
        docs.append(text[:2000])
        metas.append({
            "title": item["title"],
            "url": item["url"],
            "category": item["category"],
            "source": item["source"],
            "summary": item["summary"][:500] if item["summary"] else "",
            "date": item["date"],
            "published_at": item["published_at"],
            "fetched_at": item["fetched_at"],
        })
        existing_urls.add(url)

    if not ids:
        print(f"  📭 AIHOT: {url_skipped} 条均已存在，无新增")
        return

    # 嵌入
    all_embeddings = []
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i:i + BATCH_SIZE]
        all_embeddings.extend(embed_batch(batch))

    # 语义去重
    semantic_dup = 0
    final_ids, final_docs, final_metas, final_embs = [], [], [], []
    for idx in range(len(docs)):
        try:
            res = collection.query(query_embeddings=[all_embeddings[idx]], n_results=1)
            if res["distances"] and res["distances"][0]:
                if (1 - res["distances"][0][0]) >= 0.95:
                    semantic_dup += 1
                    continue
        except Exception:
            pass
        final_ids.append(ids[idx])
        final_docs.append(docs[idx])
        final_metas.append(metas[idx])
        final_embs.append(all_embeddings[idx])

    # 入库
    added = 0
    for i in range(0, len(final_docs), BATCH_SIZE):
        collection.add(
            ids=final_ids[i:i + BATCH_SIZE],
            embeddings=final_embs[i:i + BATCH_SIZE],
            documents=final_docs[i:i + BATCH_SIZE],
            metadatas=final_metas[i:i + BATCH_SIZE],
        )
        added += len(final_docs[i:i + BATCH_SIZE])

    print(f"  ✅ AIHOT: {added} 条新增，{url_skipped} 条 URL 去重跳过，{semantic_dup} 条语义去重跳过")


if __name__ == "__main__":
    ingest_to_chromadb()
