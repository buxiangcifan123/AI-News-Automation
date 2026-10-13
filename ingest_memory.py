"""复用脚本：将项目修改总结存入 ChromaDB claude_memory
用法: python ingest_memory.py "做了什么" "为什么这么做" "项目名" "标题"
"""
import hashlib, json, os, sys, io
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

# Windows UTF-8 编码修复
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import chromadb

CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", str(Path(__file__).parent / "chroma_db")))
EMBED_MODEL = "nomic-embed-text"
OLLAMA_BATCH_URL = "http://localhost:11434/api/embed"

def embed_batch(texts):
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = Request(OLLAMA_BATCH_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))["embeddings"]

def content_hash(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def main():
    if len(sys.argv) < 5:
        print("用法: python ingest_memory.py '做了什么' '为什么' '项目名' '标题'")
        sys.exit(1)

    what = sys.argv[1]
    why = sys.argv[2]
    project = sys.argv[3]
    title = sys.argv[4]

    doc = (
        f"## {title}\n\n"
        f"### 做了什么\n{what}\n\n"
        f"### 为什么\n{why}\n\n"
        f"项目: {project}\n"
        f"日期: {datetime.now().strftime('%Y-%m-%d')}"
    )

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(
        name="claude_memory", metadata={"hnsw:space": "cosine"}
    )

    h = content_hash(doc)
    existing = collection.get(ids=[h])
    if existing["ids"]:
        print(f"skip: already exists - {title}")
        return

    embeddings = embed_batch([doc])
    collection.add(
        ids=[h],
        embeddings=embeddings,
        documents=[doc],
        metadatas=[{
            "name": title.replace(" ", "-").lower(),
            "description": title,
            "ingested_at": datetime.now().isoformat(),
            "type": "修改记录",
            "project": project,
            "section": "修改记录",
        }],
    )
    print(f"OK ingested: {title}")

if __name__ == "__main__":
    main()
