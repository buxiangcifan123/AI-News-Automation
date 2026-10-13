"""
AI 每周新闻自动化报告
每周一运行 TrendRadar 抓取热点 → DeepSeek 生成详细分析 → 写入 Obsidian + 发送邮件。
"""
import json
import os
import smtplib
import sqlite3
import ssl
import subprocess
import sys
import time

# ── Windows UTF-8 编码修复 ─────────────────────────────────
if sys.platform == "win32":
    import io
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import chromadb
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chroma_client import get_client

# ── 新增模块 ─────────────────────────────────────────────
from image_generator import generate_images_for_report
from podcast_pipeline import process_all_podcasts
import ingest

# ── 配置加载 ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
with open(SCRIPT_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

DEEPSEEK_API_KEY = config["deepseek_api_key"]
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
TRENDRADAR_DIR = Path(config.get("trendradar_dir", str(SCRIPT_DIR / "TrendRadar")))
EMAIL_HOST = config["email_smtp_host"]
EMAIL_PORT = config["email_smtp_port"]
EMAIL_SENDER = config["email_sender"]
EMAIL_PASSWORD = config["email_password"]
EMAIL_RECEIVER = config["email_receiver"]
VAULT_PATH = Path(config["obsidian_vault_path"])
VAULT_SUBFOLDER = config["obsidian_subfolder"]
LAST_RUN_FILE = SCRIPT_DIR / "last_run.txt"

# ── DeepSeek Prompt ───────────────────────────────────────
SYSTEM_PROMPT = """你是一位资深的全球 AI 行业分析师，每周为读者解读最重要的 AI 前沿动态。

请根据以下本周 AI 热点新闻列表，生成一份详细的每周 AI 新闻报告。

要求：
1. 新闻来源包含两类：
   - 主流科技媒体（TrendRadar 抓取）：华尔街见闻、IT之家、Product Hunt、GitHub Trending、掘金、少数派等
   - 社区热点（agent-reach 采集）：Reddit、V2EX、GitHub、Hacker News 等海外技术社区
   请综合分析两类来源，提炼出本周最重要的 AI 动态。社区来源的内容可能更偏讨论和技术分享，请注意区分。
2. 按以下 7 个主题分类整理新闻，如果某个分类本周没有相关新闻可省略：
   - 大模型与训练（新模型发布、训练技术突破、基准测试）
   - 产品与应用（新产品、新功能、用户体验革新）
   - 论文与开源（重要论文、开源项目、数据集）
   - AI 安全与对齐（安全研究、红队测试、对齐进展）
   - AI 硬件与算力（芯片、GPU、数据中心、训练成本）
   - AI 行业人物（大佬观点、关键访谈、人事变动）
   - 政策与伦理（法规、监管、伦理讨论）
2. 每条新闻请包含：
   - **标题**：简洁概括
   - **原文摘要**：1-2 句话概括新闻内容
   - **背景补充**：为什么这件事重要，它意味着什么
   - **我的观点**：你的分析见解
3. 报告开头写一段「📊 本周趋势一句话」总结
4. 报告末尾列出「🔮 下周值得关注」3-5 件事
5. 整篇报告使用 markdown 格式，标题层级从 ## 开始（# 留给文件标题）
6. 语言风格：专业但不枯燥，适合 AI 从业者和爱好者阅读
7. 总字数控制在 2000-4000 字之间"""


def check_should_run() -> bool:
    """检查今天是否已经跑过。同一天不重复执行。"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if LAST_RUN_FILE.exists():
        last_run_str = LAST_RUN_FILE.read_text(encoding="utf-8").strip()
        if last_run_str == today_str:
            print(f"⏭ 今天({today_str})已跑过，跳过")
            return False
    return True


def load_news_data() -> list[dict]:
    """从 ChromaDB news + content_farm 加载过去 7 天数据（由每日 aihot_fetch 预先入库）。"""
    cutoff = datetime.now() - timedelta(days=7)
    news_data = []
    seen_urls = set()

    try:
        client = get_client()

        # 1) ChromaDB news 集合（每日 AI HOT 入库数据）
        col = client.get_collection("news")
        results = col.get(include=["metadatas"])
        news_count = 0
        for meta in results["metadatas"]:
            date_str = meta.get("date") or meta.get("fetched_at", "")
            try:
                dt = datetime.fromisoformat(date_str) if "T" in date_str else datetime.strptime(date_str, "%Y-%m-%d")
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                continue

            title = meta.get("title", "")
            url = meta.get("url", "")
            if not title:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            category = meta.get("category", "ai")
            source = meta.get("source") or meta.get("platform", "AI HOT")
            news_data.append({
                "title": title,
                "platform_name": f"[{source}/{category}]" if source != "AI HOT" else f"[AI HOT/{category}]",
                "rank": 100,
                "url": url,
                "first_crawl_time": date_str,
                "last_crawl_time": date_str,
            })
            news_count += 1
        print(f"🔥 news（AI HOT）: {news_count} 条")
    except Exception as e:
        print(f"⚠ news 集合读取失败（不阻塞）: {e}")

    # 2) ChromaDB content_farm 补充社区热点
    try:
        col = client.get_collection("content_farm")
        results = col.get(include=["metadatas"])
        farm_count = 0
        for meta in results["metadatas"]:
            url = meta.get("url", "")
            title = meta.get("title", "")
            fetched_str = meta.get("fetched_at", "")
            source = meta.get("source", "社区")

            if not title:
                continue
            if url and url in seen_urls:
                continue
            try:
                fa = datetime.fromisoformat(fetched_str)
                if fa < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

            if url:
                seen_urls.add(url)
            news_data.append({
                "title": title,
                "platform_name": f"[{source}]",
                "rank": 500,
                "url": url,
                "first_crawl_time": fetched_str,
                "last_crawl_time": fetched_str,
            })
            farm_count += 1
        print(f"🌐 content_farm（社区）: {farm_count} 条")
    except Exception as e:
        print(f"⚠ content_farm 读取失败（不阻塞）: {e}")

    print(f"📊 共 {len(news_data)} 条")
    return news_data


def analyze_with_deepseek(news_data: list[dict]) -> str:
    """调用 DeepSeek API 生成详细分析报告。"""
    # 精简数据：只传标题、平台、排名
    simplified = [
        {"title": n.get("title", ""), "platform": n.get("platform_name", ""), "rank": n.get("rank", 0)}
        for n in news_data
    ]
    news_text = json.dumps(simplified, ensure_ascii=False, indent=2)
    if len(news_text) > 30000:
        news_text = news_text[:30000] + "\n... (截断)"

    today_str = datetime.now().strftime("%Y年%m月%d日")
    user_message = f"以下是本周（{today_str}）的 AI 热点新闻数据（JSON 格式，共 {len(simplified)} 条）：\n\n{news_text}\n\n请按照要求生成本周的 AI 新闻详细报告。"

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
    }

    for attempt in range(3):
        try:
            print(f"🤖 调用 DeepSeek API（第 {attempt + 1} 次）...")
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
                content = result["choices"][0]["message"]["content"]
                print(f"✅ DeepSeek 分析完成，{len(content)} 字符")
                return content
        except Exception as e:
            print(f"⚠ DeepSeek 调用失败（第 {attempt + 1} 次）: {e}")
            if attempt < 2:
                time.sleep(5)

    raise RuntimeError("DeepSeek API 调用失败，已重试 3 次")


def save_to_obsidian(markdown_content: str) -> Path:
    """将报告写入 Obsidian vault。"""
    today = datetime.now().strftime("%Y-%m-%d")
    target_dir = VAULT_PATH / VAULT_SUBFOLDER
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{today}.md"
    full_content = f"# 🗞 AI 每周新闻报告 · {today}\n\n{markdown_content}"
    file_path.write_text(full_content, encoding="utf-8")
    print(f"📝 报告已写入 Obsidian: {file_path}")
    return file_path


def send_email(markdown_content: str) -> bool:
    """通过 QQ 邮箱发送报告邮件。"""
    today = datetime.now().strftime("%Y年%m月%d日")

    msg = MIMEText(markdown_content, "plain", "utf-8")
    msg["Subject"] = f"🗞 AI 每周新闻报告 · {today}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        print(f"📧 发送邮件到 {EMAIL_RECEIVER}...")
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("✅ 邮件发送成功")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


def update_last_run():
    """更新上次运行日期。"""
    today = datetime.now().strftime("%Y-%m-%d")
    LAST_RUN_FILE.write_text(today, encoding="utf-8")
    print(f"📅 已记录运行日期: {today}")


def main():
    print("=" * 60)
    print("🚀 AI 每周新闻自动化报告")
    print("=" * 60)

    # ① 检查是否需要执行
    if not check_should_run():
        return

    # ② 加载新闻数据（AI HOT RSS + ChromaDB 社区热点）
    news_data = load_news_data()
    if not news_data:
        send_email("⚠️ **AI 新闻自动化报告失败**\n\n未获取到新闻数据，请检查：\n1. AI HOT RSS 是否可用\n2. ChromaDB 服务是否运行")
        return

    # ③ DeepSeek 分析
    try:
        report = analyze_with_deepseek(news_data)
    except RuntimeError as e:
        send_email(
            "⚠️ **AI 新闻自动化报告失败**\n\n"
            f"DeepSeek API 调用失败（已重试 3 次）: {e}\n"
            "请检查 API key 是否有效、账户余额是否充足。"
        )
        raise

    # ⑤ 🆕 图片生成（基于主报告中涉及的热点新闻）
    try:
        report = generate_images_for_report(news_data, report, config)
    except Exception as e:
        print(f"⚠ 图片生成环节异常（不阻塞主流程）: {e}")

    # ⑥ 🆕 播客处理（RSS → Whisper 转录 → DeepSeek 总结 → 追加到报告）
    try:
        podcast_section = process_all_podcasts(config)
        if podcast_section:
            report += podcast_section
            print("✅ 播客摘要已追加到报告")
    except Exception as e:
        print(f"⚠ 播客处理环节异常（不阻塞主流程）: {e}")

    # ⑦ 写入 Obsidian
    save_to_obsidian(report)

    # ⑧ 发送邮件
    send_email(report)

    # ⑧.⑤ 🆕 向量数据库索引（不阻塞主流程）
    try:
        print("🔢 更新向量索引...")
        ingest.add_weekly_news()
        ingest.add_weekly_podcasts()
        ingest.refresh_obsidian_vault()
        ingest.add_external_raw()
    except Exception as e:
        print(f"⚠ 向量索引更新失败（不阻塞主流程）: {e}")

    # ⑨ 记录日期
    update_last_run()

    print("=" * 60)
    print("🎉 全部完成！")


if __name__ == "__main__":
    main()
