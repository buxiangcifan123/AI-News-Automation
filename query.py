"""
查询模块 - 语义搜索、RAG 问答、智能关联
用法: python query.py --search "你的问题"
      python query.py --ask "你的问题"
      python query.py --find-related --this-week
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

import chromadb

SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

CHROMA_PATH = Path(config["chromadb"]["path"])
EMBED_MODEL = config["chromadb"]["embedding_model"]
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
DEEPSEEK_API_KEY = config["deepseek_api_key"]
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


def embed(text: str) -> list[float]:
    """调用 Ollama 嵌入模型。"""
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode("utf-8")
    req = Request(OLLAMA_EMBED_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["embedding"]


def get_client():
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


# ─── A. 语义搜索 ────────────────────────────────────────────

def search_similar(query: str, collection_name: str = "all", top_k: int = 10) -> list[dict]:
    """自然语言语义搜索，返回 top_k 条最相关结果。"""
    query_vec = embed(query)
    client = get_client()
    results = []

    collections = (
        ["news", "podcasts", "obsidian", "external"]
        if collection_name == "all"
        else [collection_name]
    )

    for name in collections:
        try:
            col = client.get_collection(name)
            res = col.query(query_embeddings=[query_vec], n_results=top_k)
            for i, doc_id in enumerate(res["ids"][0]):
                results.append({
                    "id": doc_id,
                    "text": res["documents"][0][i] if res["documents"] else "",
                    "score": 1 - res["distances"][0][i],  # cosine → similarity
                    "source": name,
                    "metadata": res["metadatas"][0][i] if res["metadatas"] else {},
                })
        except Exception as e:
            print(f"⚠ 查询 {name} 失败: {e}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


# ─── B. RAG 问答 ────────────────────────────────────────────

def ask_with_context(question: str, top_k: int = 5) -> str:
    """搜索最相关上下文 → 拼入 prompt → DeepSeek 回答。"""
    # 搜索
    hits = search_similar(question, collection_name="all", top_k=top_k)

    if not hits:
        return "未找到相关内容。"

    # 拼上下文
    context_parts = []
    for h in hits:
        src = h["metadata"].get("file") or h["metadata"].get("title") or h["source"]
        context_parts.append(f"【来源: {src}】\n{h['text'][:1000]}")

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""你是一个 AI 新闻知识助手。请根据以下历史内容回答用户问题。

历史内容:
{context}

用户问题: {question}

请基于历史内容回答。如果历史内容不足以回答，请诚实说明。"""

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 2048,
    }

    req = Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    with urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


# ─── C. 智能关联 ────────────────────────────────────────────

def find_related(target_text: str, collection_name: str = "news", threshold: float = 0.75, top_k: int = 10) -> list[dict]:
    """找出与 target_text 语义相似的历史条目（相似度 > threshold）。"""
    hits = search_similar(target_text, collection_name=collection_name, top_k=top_k)
    return [h for h in hits if h["score"] >= threshold]


# ─── CLI ────────────────────────────────────────────────────

def main():
    if "--search" in sys.argv:
        idx = sys.argv.index("--search")
        query = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not query:
            print("用法: python query.py --search \"你的问题\"")
            return
        print(f"🔍 搜索: {query}\n")
        results = search_similar(query)
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['source']}] (相似度: {r['score']:.3f})")
            print(f"   {r['text'][:150]}...")
            meta = r.get("metadata", {})
            label = meta.get("title") or meta.get("file") or ""
            if label:
                print(f"   📎 {label}")
            print()

    elif "--ask" in sys.argv:
        idx = sys.argv.index("--ask")
        question = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not question:
            print("用法: python query.py --ask \"你的问题\"")
            return
        print(f"🤖 RAG 问答: {question}\n")
        answer = ask_with_context(question)
        print(answer)

    elif "--find-related" in sys.argv:
        if "--this-week" in sys.argv:
            # 读取本周报告
            today = datetime.now().strftime("%Y-%m-%d")
            vault_path = Path(config["obsidian_vault_path"]) / config["obsidian_subfolder"]
            report_file = vault_path / f"{today}.md"
            if not report_file.exists():
                print(f"⚠ 本周报告不存在: {report_file}")
                return
            target = report_file.read_text(encoding="utf-8")[:3000]
        else:
            target = input("请输入要关联的文本: ")

        print("🔗 智能关联结果:\n")
        results = find_related(target)
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['source']}] (相似度: {r['score']:.3f})")
            print(f"   {r['text'][:150]}...")
            print()

    else:
        print("用法:")
        print("  python query.py --search \"问题\"   语义搜索")
        print("  python query.py --ask \"问题\"      RAG 问答")
        print("  python query.py --find-related --this-week  智能关联本周报告")


if __name__ == "__main__":
    main()
