"""
Claude 项目记忆查询 - 语义搜索你的项目知识库
用法: python query_memory.py "如何配置 Electron 开机自启"
      python query_memory.py "Tidy 架构" --top 5
"""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import chromadb

SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

CHROMA_PATH = Path(config["chromadb"]["path"])
EMBED_MODEL = config["chromadb"]["embedding_model"]
EMBED_URL = "http://localhost:11434/api/embed"
COLLECTION_NAME = "claude_memory"


def embed_query(text: str) -> list[float]:
    payload = json.dumps({"model": EMBED_MODEL, "input": [text]}).encode("utf-8")
    req = Request(EMBED_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))["embeddings"][0]


def main():
    if len(sys.argv) < 2:
        print("用法: python query_memory.py <查询文本> [--top N]")
        print("示例: python query_memory.py \"Tidy 开机自启怎么实现\" --top 5")
        return

    query = sys.argv[1]
    top_n = 5
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        if idx + 1 < len(sys.argv):
            top_n = int(sys.argv[idx + 1])

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        print("❌ 记忆库不存在，请先运行 ingest_claude_memory.py --init")
        return

    print(f"🔍 搜索: {query}\n")

    embedding = embed_query(query)
    results = collection.query(
        query_embeddings=[embedding],
        n_results=top_n,
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"][0]:
        print("无匹配结果")
        return

    for i, (doc, meta, dist) in enumerate(zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ), 1):
        similarity = max(0, 1 - dist)  # cosine distance → similarity
        print(f"{'─' * 60}")
        print(f"#{i}  [{meta.get('type', '')}] {meta.get('file', '')}")
        print(f"    📂 {meta.get('project', '')}  |  § {meta.get('section', '')}")
        print(f"    🎯 相似度: {similarity:.1%}")
        print(f"{'─' * 60}")
        # 截断长文本
        preview = doc[:500] + ("..." if len(doc) > 500 else "")
        print(preview)
        print()


if __name__ == "__main__":
    main()
