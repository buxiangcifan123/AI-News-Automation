"""
Agent-Reach 外源数据采集 — 8 平台全覆盖
用法: python collect_external.py              # 采集所有平台
      python collect_external.py --reddit     # 只采集 Reddit
      python collect_external.py --twitter    # 只采集 Twitter
"""
import json
import subprocess
import sys
import io
import os
import shutil

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR.parent / "ai-content-farm" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now().strftime("%Y%m%d")
NOW_ISO = datetime.now().isoformat()

# CLI 工具路径（优先环境变量，其次 PATH，最后裸命令名）
TWITTER = os.environ.get("TWITTER_CLI") or shutil.which("twitter") or "twitter"
YTDLP = os.environ.get("YTDLP_CLI") or shutil.which("yt-dlp") or "yt-dlp"


def save_items(items: list):
    """保存到 output/YYYYMMDD_external.json，按 url 去重追加。"""
    output_file = OUTPUT_DIR / f"{TODAY}_external.json"
    existing = []
    if output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    seen_urls = {item.get("url") for item in existing}
    new_items = [item for item in items if item.get("url") not in seen_urls]
    if new_items:
        existing.extend(new_items)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f"💾 新增 {len(new_items)} 条，总计 {len(existing)} 条 -> {output_file.name}")
    else:
        print(f"📭 无新增内容")


# ═══════════════════════════════════════════════════════════════
#  V2EX（公开 API，无需认证）
# ═══════════════════════════════════════════════════════════════

def fetch_v2ex() -> list:
    """V2EX 公开 API — python/tech/programmers/AI 节点热门帖。"""
    print("🔵 V2EX 采集...")
    import urllib.request
    import re

    items = []
    nodes = ["python", "tech", "ai"]

    for node in nodes:
        try:
            url = f"https://www.v2ex.com/api/topics/show.json?node_name={node}&page=1"
            req = urllib.request.Request(url, headers={"User-Agent": "agent-reach/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                topics = json.loads(resp.read().decode("utf-8"))
            for t in topics[:5]:
                title = t.get("title", "")
                content = t.get("content_rendered", "") or t.get("content", "")
                content = re.sub(r"<[^>]+>", "", content)
                items.append({
                    "title": f"[V2EX/{node}] {title}",
                    "content": content[:2000],
                    "url": t.get("url", f"https://www.v2ex.com/t/{t.get('id', '')}"),
                    "source": "v2ex",
                    "source_type": "agent-reach",
                    "fetched_at": NOW_ISO,
                })
            print(f"  ✅ {node}: {len(topics[:5])} 条")
        except Exception as e:
            print(f"  ⚠️ {node} 失败: {e}")

    print(f"  📊 V2EX 共 {len(items)} 条")
    return items


# ═══════════════════════════════════════════════════════════════
#  Reddit（RSS Feed，比 API 更稳定，需 VPN）
# ═══════════════════════════════════════════════════════════════

REDDIT_RSS_FEEDS = [
    ("MachineLearning", "https://www.reddit.com/r/MachineLearning/.rss"),
    ("singularity", "https://www.reddit.com/r/singularity/.rss"),
    ("artificial", "https://www.reddit.com/r/artificial/.rss"),
    ("LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/.rss"),
    ("ChatGPT", "https://www.reddit.com/r/ChatGPT/.rss"),
    ("OpenAI", "https://www.reddit.com/r/OpenAI/.rss"),
]


def fetch_reddit() -> list:
    """Reddit — RSS Feed 抓取 AI 子版热门帖。"""
    print("🟠 Reddit 采集（RSS）...")
    import feedparser
    import re

    items = []
    for sub_name, rss_url in REDDIT_RSS_FEEDS:
        try:
            feed = feedparser.parse(rss_url)
            count = 0
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                items.append({
                    "title": f"[Reddit r/{sub_name}] {title}",
                    "content": summary[:2000] if summary else title,
                    "url": entry.get("link", ""),
                    "source": f"reddit-{sub_name}",
                    "source_type": "agent-reach",
                    "fetched_at": NOW_ISO,
                })
                count += 1
            print(f"  ✅ r/{sub_name}: {count} 条")
        except Exception as e:
            print(f"  ⚠️ r/{sub_name} 失败: {e}")

    print(f"  📊 Reddit 共 {len(items)} 条")
    return items


# ═══════════════════════════════════════════════════════════════
#  Twitter/X（twitter-cli，需设 TWITTER_AUTH_TOKEN）
# ═══════════════════════════════════════════════════════════════

TWITTER_AI_ACCOUNTS = [
    "OpenAI",
    "AnthropicAI",
    "GoogleDeepMind",
    "kaboroevich",    # AI 新闻聚合
    "ylecun",          # Yann LeCun
    "kaboroevich",    # AI 新闻聚合
    "ai_zona",         # AI Zone
]


def fetch_twitter() -> list:
    """Twitter/X 搜索 — AI 相关账号时间线 + 关键词搜索。"""
    print("🐦 Twitter 采集...")
    import os

    # 检查认证
    if not os.environ.get("TWITTER_AUTH_TOKEN"):
        print("  ⚠️ 未设置 TWITTER_AUTH_TOKEN 环境变量，跳过 Twitter")
        print("  💡 设置方法: export TWITTER_AUTH_TOKEN='你的token'")
        print("  💡 获取 token: Cookie-Editor 插件导出 Twitter cookies")
        return []

    items = []

    # 方式1: 搜索 AI 关键词
    for query in ["AI news today", "LLM announcement", "machine learning breakthrough"]:
        try:
            result = subprocess.run(
                [TWITTER, "search", query, "-n", "5", "--json"],
                capture_output=True, timeout=45,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    tweets = json.loads(result.stdout)
                    for t in tweets[:5]:
                        items.append({
                            "title": f"[Twitter] {t.get('text', '')[:80]}",
                            "content": t.get("text", "")[:2000],
                            "url": f"https://twitter.com/i/status/{t.get('id', '')}",
                            "source": "twitter",
                            "source_type": "agent-reach",
                            "fetched_at": NOW_ISO,
                        })
                except json.JSONDecodeError:
                    pass
            print(f"  ✅ 搜索 '{query}': 完成")
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ 搜索 '{query}' 超时")
        except Exception as e:
            print(f"  ⚠️ 搜索 '{query}' 失败: {e}")

    # 方式2: 抓 AI 账号时间线
    for account in TWITTER_AI_ACCOUNTS[:4]:  # 限 4 个少打点
        try:
            result = subprocess.run(
                [TWITTER, "user-posts", f"@{account}", "-n", "3", "--json"],
                capture_output=True, timeout=45,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    tweets = json.loads(result.stdout)
                    for t in tweets[:3]:
                        items.append({
                            "title": f"[Twitter @{account}] {t.get('text', '')[:80]}",
                            "content": t.get("text", "")[:2000],
                            "url": f"https://twitter.com/{account}/status/{t.get('id', '')}",
                            "source": f"twitter-{account}",
                            "source_type": "agent-reach",
                            "fetched_at": NOW_ISO,
                        })
                except json.JSONDecodeError:
                    pass
            print(f"  ✅ @{account}: 完成")
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ @{account} 超时")
        except Exception as e:
            print(f"  ⚠️ @{account} 失败: {e}")

    print(f"  📊 Twitter 共 {len(items)} 条")
    return items


# ═══════════════════════════════════════════════════════════════
#  GitHub（公开 REST API，无需认证）
# ═══════════════════════════════════════════════════════════════

def fetch_github() -> list:
    """GitHub — 公开 REST API 搜 AI/LLM 热门仓库。"""
    print("🐙 GitHub 采集...")
    import urllib.request

    items = []
    queries = [
        "AI+agent+topic:machine-learning",
        "LLM+topic:deep-learning",
        "machine+learning+topic:machine-learning",
    ]

    for query in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=5"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AI-Content-Farm/1.0",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for repo in data.get("items", []):
                items.append({
                    "title": f"[GitHub] {repo['full_name']} ⭐{repo['stargazers_count']}",
                    "content": f"{repo.get('description', '') or ''}\\n语言: {repo.get('language', 'N/A')}",
                    "url": repo["html_url"],
                    "source": "github",
                    "source_type": "agent-reach",
                    "stars": repo.get("stargazers_count", 0),
                    "fetched_at": NOW_ISO,
                })
            print(f"  ✅ '{query}': {len(data.get('items', []))} 条")
        except Exception as e:
            print(f"  ⚠️ '{query}' 失败: {e}")

    print(f"  📊 GitHub 共 {len(items)} 条")
    return items


# ═══════════════════════════════════════════════════════════════
#  YouTube（yt-dlp 搜索）
# ═══════════════════════════════════════════════════════════════

def fetch_youtube() -> list:
    """YouTube — yt-dlp 搜索 AI 视频，需浏览器 cookies 防 bot。"""
    print("▶️  YouTube 采集...")
    items = []
    queries = ["AI news today", "LLM announcement"]

    for query in queries:
        try:
            result = subprocess.run(
                [YTDLP, "--dump-json", "--cookies-from-browser", "chrome",
                 f"ytsearch3:{query}"],
                capture_output=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            # 如果 chrome cookies 不行，回退到无 cookie 模式
            if "Sign in to confirm" in result.stderr or result.returncode != 0:
                print(f"  ⚠️ cookies 方式失败，尝试无 cookie...")
                result = subprocess.run(
                    [YTDLP, "--dump-json", f"ytsearch3:{query}"],
                    capture_output=True, timeout=60,
                    encoding="utf-8", errors="replace",
                )

            if "Sign in to confirm" in (result.stderr + result.stdout):
                print(f"  ⚠️ YouTube 要求登录验证，跳过。")
                print(f"  💡 解决: yt-dlp --cookies-from-browser chrome")
                continue

            count = 0
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    video = json.loads(line)
                    items.append({
                        "title": f"[YouTube] {video.get('title', '')}",
                        "content": video.get("description", "")[:2000],
                        "url": video.get("webpage_url", ""),
                        "source": "youtube",
                        "source_type": "agent-reach",
                        "views": video.get("view_count", 0),
                        "upload_date": video.get("upload_date", ""),
                        "fetched_at": NOW_ISO,
                    })
                    count += 1
                except json.JSONDecodeError:
                    continue
            print(f"  ✅ '{query}': {count} 条")
        except FileNotFoundError:
            print(f"  ⚠️ yt-dlp 未找到: {YTDLP}")
            break
        except Exception as e:
            print(f"  ⚠️ '{query}' 失败: {e}")

    print(f"  📊 YouTube 共 {len(items)} 条")
    return items


# ═══════════════════════════════════════════════════════════════
#  汇总入口
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 50)
    print("  🌐 Agent-Reach 外源数据采集（5 平台）")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    print()
    print("  平台: V2EX | Reddit | Twitter | GitHub | YouTube")
    print()

    all_items = []
    platform_flags = {"--reddit", "--twitter", "--github", "--youtube", "--v2ex"}
    run_all = not any(f in sys.argv for f in platform_flags)

    if run_all or "--v2ex" in sys.argv:
        all_items.extend(fetch_v2ex())
    if run_all or "--reddit" in sys.argv:
        all_items.extend(fetch_reddit())
    if run_all or "--twitter" in sys.argv:
        all_items.extend(fetch_twitter())
    if run_all or "--github" in sys.argv:
        all_items.extend(fetch_github())
    if run_all or "--youtube" in sys.argv:
        all_items.extend(fetch_youtube())

    print(f"\n📊 总计采集 {len(all_items)} 条")

    if all_items:
        save_items(all_items)
        print("\n✅ 完成！运行 ingest.py 即可入库。")
    else:
        print("\n⚠️ 未采集到任何内容。可能原因：")
        print("   1. VPN 未开启（Reddit/V2EX 需要）")
        print("   2. Twitter 未设置 TWITTER_AUTH_TOKEN")
        print("   3. YouTube 需要浏览器 cookies")
        print("   4. 工具未安装（pip install yt-dlp rdt-cli twitter-cli）")

    print(f"\n💡 下一步: python ingest.py  # 将外源数据嵌入 ChromaDB")


if __name__ == "__main__":
    main()
