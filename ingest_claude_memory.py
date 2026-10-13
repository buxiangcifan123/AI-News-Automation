"""
Claude 项目记忆入库模块 - 将 CLAUDE.md + 项目记忆文件嵌入 ChromaDB
用法: python ingest_claude_memory.py           # 增量更新
      python ingest_claude_memory.py --init    # 全量重建
"""
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import chromadb
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chroma_client import get_client

# ── 配置 ─────────────────────────────────────────────────────
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"

SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

CHROMA_PATH = Path(config["chromadb"]["path"])
EMBED_MODEL = config["chromadb"]["embedding_model"]
EMBED_URL = "http://localhost:11434/api/embed"
COLLECTION_NAME = "claude_memory"

BATCH_SIZE = 50


def embed_batch(texts: list[str]) -> list[list[float]]:
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = Request(EMBED_URL, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))["embeddings"]


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (元数据, 正文)。"""
    meta = {}
    body = content.strip()
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip()
            body = parts[2].strip()
    return meta, body


def chunk_by_headers(text: str, min_chars: int = 30) -> list[tuple[str, str]]:
    """按 ## 标题分段，返回 [(标题, 内容), ...]。"""
    sections = re.split(r"\n(?=## )", text)
    chunks = []
    for sec in sections:
        sec = sec.strip()
        if len(sec) < min_chars:
            continue
        lines = sec.split("\n", 1)
        title = lines[0].lstrip("#").strip() if lines else ""
        chunk_text = sec
        chunks.append((title, chunk_text))
    return chunks


def class_file(file_path: Path) -> str:
    """判断文件类别，映射到六大分类体系"""
    if file_path.name == "CLAUDE.md":
        return "global_config"
    content = file_path.read_text(encoding="utf-8", errors="replace")
    meta, _ = parse_frontmatter(content)
    mem_type = meta.get("type", meta.get("node_type", "unknown"))
    # 六大分类体系
    type_map = {
        "dev-log":      "开发日志",
        "bug":          "Bug记录",
        "decision":     "决策记录",
        "conversation": "对话记录",
        "project":      "项目文档",
        "reference":    "技术资料",
        "user":         "用户信息",
        # 兼容旧值
        "feedback":     "决策记录",
    }
    return type_map.get(mem_type, "项目文档")


def find_memory_files() -> list[Path]:
    """扫描 CLAUDE.md + 项目记忆文件，跳过插件市场。"""
    files = []

    # CLAUDE.md
    claude_md = CLAUDE_DIR / "CLAUDE.md"
    if claude_md.exists():
        files.append(claude_md)

    # 项目记忆文件
    if PROJECTS_DIR.exists():
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            memory_dir = proj_dir / "memory"
            if not memory_dir.exists():
                continue
            for md_file in memory_dir.glob("*.md"):
                # 跳过 MEMORY.md 索引文件（内容只是列表，实际内容在各文件中）
                if md_file.name == "MEMORY.md":
                    # 读索引作为补充上下文
                    if md_file.stat().st_size < 2000:
                        files.append(md_file)
                    continue
                files.append(md_file)

    return files


def main():
    is_init = "--init" in sys.argv
    if is_init:
        print("🔧 全量重建模式\n")

    files = find_memory_files()
    print(f"📂 找到 {len(files)} 个记忆文件\n")

    client = get_client()

    if is_init:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "description": "Claude 项目记忆：开发日志、Bug记录、决策记录、项目文档、技术资料"},
    )

    all_texts, all_ids, all_metas = [], [], []
    total_skipped = 0

    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  ⚠️ 读取失败 {file_path.name}: {e}")
            continue

        if not content.strip():
            continue

        meta, body = parse_frontmatter(content)
        file_type = class_file(file_path)

        # 确定项目名
        rel_path = str(file_path.relative_to(CLAUDE_DIR))
        project = "全局"
        if str(file_path).startswith(str(PROJECTS_DIR)):
            # 从路径提取项目名
            parts = Path(str(file_path.relative_to(PROJECTS_DIR))).parts
            if parts:
                project = parts[0]  # e.g. "C--Users-------"

        # 按 ## 分段
        chunks = chunk_by_headers(body)
        if not chunks:
            # 没标题就整个文件做一个 chunk
            chunks = [(meta.get("name", file_path.stem), body)]

        for title, chunk_text in chunks:
            h = content_hash(chunk_text)
            existing = collection.get(ids=[h])
            if existing["ids"]:
                total_skipped += 1
                continue

            # 限制单段长度（嵌入模型有 token 上限）
            if len(chunk_text) > 8000:
                chunk_text = chunk_text[:8000]

            all_texts.append(chunk_text)
            all_ids.append(h)
            all_metas.append({
                "file": str(rel_path),
                "project": project,
                "type": file_type,
                "section": title,
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "ingested_at": datetime.now().isoformat(),
            })

    if not all_texts:
        print(f"📭 无新内容，{total_skipped} 段已存在")
        return

    # 分批嵌入
    added = 0
    for i in range(0, len(all_texts), BATCH_SIZE):
        batch_texts = all_texts[i:i + BATCH_SIZE]
        batch_ids = all_ids[i:i + BATCH_SIZE]
        batch_metas = all_metas[i:i + BATCH_SIZE]

        try:
            embeddings = embed_batch(batch_texts)
            collection.add(
                ids=batch_ids,
                embeddings=embeddings,
                documents=batch_texts,
                metadatas=batch_metas,
            )
            added += len(batch_texts)
            print(f"  📝 记忆入库: {added}/{len(all_texts)}")
        except Exception as e:
            print(f"  ⚠️ 嵌入失败: {e}")
            continue

    print(f"\n✅ 记忆入库完成: {added} 段新增，{total_skipped} 段跳过（已存在）")


if __name__ == "__main__":
    main()
