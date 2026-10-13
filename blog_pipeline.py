"""
博客管线 - RSS 抓取 → 网页提取 → DeepSeek 总结 → Obsidian + FAISS
支持 HuggingFace / OpenAI / Anthropic / DeepMind / The Batch
"""
import json
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

import feedparser


# ── HTML 文本提取 ──────────────────────────────────────────────


class TextExtractor(HTMLParser):
    """提取 HTML 正文，跳过 script/style/head 标签"""

    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "nav", "footer", "header"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "nav", "footer", "header"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            t = data.strip()
            if t:
                self.text.append(t)

    def get_text(self) -> str:
        return "\n".join(self.text)


def extract_text_from_html(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.get_text()


def clean_text(text: str, max_chars: int = 6000) -> str:
    """压缩空白，截断到 max_chars"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


# ── RSS 解析 ───────────────────────────────────────────────────


def parse_blog_rss(rss_url: str) -> list[dict]:
    """解析 RSS，返回本周文章列表"""
    print(f"🔍 解析: {rss_url}")
    feed = feedparser.parse(rss_url)
    articles = []

    for entry in feed.entries:
        title = entry.get("title", "Untitled")
        link = entry.get("link", "")
        published = entry.get("published", entry.get("updated", ""))
        summary = entry.get("summary", entry.get("description", ""))

        if not is_this_week(published):
            continue

        articles.append({
            "title": title,
            "url": link,
            "published": published,
            "summary": summary,
            "source": feed.feed.get("title", rss_url),
        })

    print(f"   本周 {len(articles)} 篇")
    return articles


def is_this_week(published_str: str) -> bool:
    """本周一发以来发布的"""
    if not published_str:
        return True
    try:
        pub_date = parsedate_to_datetime(published_str)
        if pub_date.tzinfo:
            pub_date = pub_date.replace(tzinfo=None)
        days_since_monday = datetime.now().weekday()
        this_monday = (datetime.now() - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_monday = this_monday - timedelta(days=7)
        return pub_date >= last_monday
    except Exception:
        return True


# ── 网页抓取 ───────────────────────────────────────────────────


def fetch_article(url: str, timeout: int = 30) -> str:
    """抓取文章全文"""
    print(f"   📄 抓取: {url[:100]}...")
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    text = extract_text_from_html(html)
    return clean_text(text)


# ── DeepSeek 总结 ─────────────────────────────────────────────


def summarize_article(title: str, content: str, api_key: str) -> str:
    """用 DeepSeek 总结单篇文章"""
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 AI 技术博客分析师。请用中文简洁总结文章（150 字以内），"
                    "包含：1) 核心话题 2) 关键观点/发布 3) 对 AI 社区的意义。"
                ),
            },
            {
                "role": "user",
                "content": f"标题：{title}\n\n正文：\n{content}",
            },
        ],
        "temperature": 0.5,
        "max_tokens": 600,
    }

    req = Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


# ── 主入口 ─────────────────────────────────────────────────────


def process_blogs(config: dict) -> str:
    """处理所有博客源，返回 Markdown 报告"""
    rss_urls = config.get("blog_rss_urls", [])
    if not rss_urls:
        print("⚠ 未配置博客 RSS")
        return ""

    api_key = config["deepseek_api_key"]
    output_dir = Path(config.get("blog_output_dir", "blogs"))
    output_dir.mkdir(parents=True, exist_ok=True)

    all_articles = []
    for url in rss_urls:
        try:
            articles = parse_blog_rss(url)
            all_articles.extend(articles)
        except Exception as e:
            print(f"   ⚠ RSS 解析失败: {e}")

    if not all_articles:
        print("📭 本周无新文章")
        return ""

    print(f"\n📚 共 {len(all_articles)} 篇文章，开始抓取+总结...\n")

    results = []
    for i, art in enumerate(all_articles):
        print(f"[{i+1}/{len(all_articles)}] {art['title'][:80]}")
        try:
            # 1) 抓全文；失败则用 RSS 摘要
            summary_text = art.get("summary", "")
            try:
                content = fetch_article(art["url"])
            except Exception as e:
                print(f"   ⚠ 抓取失败({e})，降级用 RSS 摘要")
                content = clean_text(summary_text, max_chars=3000) if summary_text else ""

            # 2) DeepSeek 总结
            ai_summary = summarize_article(art["title"], content, api_key)
            results.append({**art, "ai_summary": ai_summary})

            print(f"   ✅ 总结完成 ({len(ai_summary)} 字)")
            time.sleep(0.5)  # 限速
        except Exception as e:
            print(f"   ⚠ 失败: {e}")
            continue

    if not results:
        return ""

    # 格式化为 Markdown
    lines = [
        "# 📝 本周 AI 博客精选\n",
        f"> {datetime.now().strftime('%Y-%m-%d')} 自动生成\n",
        f"> 来源: {', '.join(r['source'] for r in results[:1])} 等 {len(rss_urls)} 个博客\n",
    ]

    for r in results:
        pub = r.get("published", "")[:16]
        lines.append(f"## [{r['title']}]({r['url']})\n")
        lines.append(f"**来源:** {r['source']}  |  **日期:** {pub}\n")
        lines.append(r["ai_summary"])
        lines.append("")

    md = "\n".join(lines)

    # 保存
    date_str = datetime.now().strftime("%Y-%m-%d")
    blog_path = output_dir / f"weekly_blogs_{date_str}.md"
    blog_path.write_text(md, encoding="utf-8")
    print(f"\n✅ 博客报告已保存: {blog_path}")

    # 同步到 Obsidian
    obsidian_path = Path(config["obsidian_vault_path"]) / config.get("obsidian_subfolder", "")
    if obsidian_path.exists():
        obs_file = obsidian_path / f"本周AI博客_{date_str}.md"
        obs_file.write_text(md, encoding="utf-8")
        print(f"✅ Obsidian: {obs_file}")

    return md
