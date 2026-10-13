"""
播客管线 - RSS 下载 → Whisper 转录 → DeepSeek 总结
支持 Lex Fridman Podcast 和 TWIML AI Podcast
"""
import json
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

import feedparser
import whisper


# ── 全局：延迟加载 Whisper 模型 ──────────────────────────
_whisper_model = None
_whisper_model_name = None


def get_whisper_model(model_name="large-v3"):
    """延迟加载 Whisper 模型（切换 model_name 会重新加载）"""
    global _whisper_model, _whisper_model_name
    if _whisper_model is None or _whisper_model_name != model_name:
        _whisper_model = None  # 释放旧模型
        print(f"🔊 加载 Whisper 模型: {model_name}（FP16）...")
        _whisper_model = whisper.load_model(model_name, device="cuda")
        _whisper_model_name = model_name
        print(f"✅ Whisper {model_name} 已加载")
    return _whisper_model


# ── RSS ─────────────────────────────────────────────────


def parse_rss_feed(rss_url):
    """解析 RSS 订阅，返回最新一期播客信息"""
    print(f"🔍 解析 RSS: {rss_url}")
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        print(f"⚠ RSS 无条目: {rss_url}")
        return None

    entry = feed.entries[0]
    # 获取音频链接
    audio_url = ""
    if entry.get("enclosures"):
        audio_url = entry.enclosures[0].get("href", "")
    elif entry.get("links"):
        for link in entry.links:
            if link.get("type", "").startswith("audio/"):
                audio_url = link.get("href", "")
                break

    return {
        "title": entry.get("title", "Unknown"),
        "published": entry.get("published", entry.get("updated", "")),
        "audio_url": audio_url,
        "summary": entry.get("summary", entry.get("description", "")),
        "duration": entry.get("itunes_duration", ""),
    }


def is_this_week(published_str):
    """判断播客是否上周一以来发布（确保下周一不会漏掉本周一之后更新的内容）"""
    if not published_str:
        return True  # 无法判断则默认处理

    try:
        pub_date = parsedate_to_datetime(published_str)
        # 转为 naive datetime 比较
        if pub_date.tzinfo:
            pub_date = pub_date.replace(tzinfo=None)
        now = datetime.now()
        # 上周一 00:00
        days_since_monday = now.weekday()  # Monday=0
        this_monday = (now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_monday = this_monday - timedelta(days=7)
        return pub_date >= last_monday
    except Exception as e:
        print(f"⚠ 日期解析失败 '{published_str}': {e}，默认处理")
        return True


def download_episode(episode_info, save_dir):
    """下载播客音频到本地"""
    audio_url = episode_info.get("audio_url", "")
    if not audio_url:
        print(f"⚠ 无音频链接: {episode_info['title'][:60]}")
        return None

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    # 用标题前半部分做文件名，去掉非法字符
    safe_name = (
        episode_info["title"][:60]
        .replace("/", "_")
        .replace(":", "_")
        .replace("?", "")
    )
    filepath = save_dir / f"{safe_name}.mp3"

    if filepath.exists():
        print(f"📁 音频已存在: {filepath}")
        return filepath

    print(f"📥 下载: {episode_info['title'][:80]}")
    print(f"   链接: {audio_url[:100]}...")

    req = Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=1800) as resp:
        data = resp.read()

    filepath.write_bytes(data)
    size_mb = len(data) / 1024 / 1024
    print(f"✅ 下载完成: {filepath.name} ({size_mb:.1f} MB)")
    return filepath


# ── Whisper 转录 ────────────────────────────────────────


def transcribe_audio(audio_path, output_dir, model_name="large-v3"):
    """用 Whisper 本地转录，返回文本"""
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = get_whisper_model(model_name)
    print(f"🎤 转录中: {audio_path.name}（这可能需要几分钟到半小时...）")
    start = time.time()

    result = model.transcribe(
        str(audio_path),
        language=None,  # auto-detect
        verbose=False,
        fp16=True,  # RTX 5060 FP16 加速
    )

    transcript = result.get("text", "")
    detected_lang = result.get("language", "unknown")
    elapsed = (time.time() - start) / 60
    print(
        f"✅ 转录完成 [{detected_lang}]，{len(transcript)} 字符，耗时 {elapsed:.1f} 分钟"
    )

    # 保存转录文本
    txt_path = output_dir / f"{audio_path.stem}.txt"
    txt_path.write_text(transcript, encoding="utf-8")
    print(f"📝 转录已保存: {txt_path}")

    return transcript


# ── DeepSeek 总结 ───────────────────────────────────────


def summarize_transcript(transcript_text, deepseek_key, podcast_title):
    """用 DeepSeek 总结转录文本（分段处理长文本）"""
    max_chars = 8000

    # 如果文本不长，直接总结
    if len(transcript_text) <= max_chars:
        return _call_deepseek_summary(
            transcript_text, deepseek_key, podcast_title, part=""
        )

    # 长文本分段总结
    chunks = []
    for i in range(0, len(transcript_text), max_chars):
        chunks.append(transcript_text[i : i + max_chars])
        if len(chunks) >= 3:  # 最多取 3 段
            break

    print(f"📄 转录文本 {len(transcript_text)} 字符，分 {len(chunks)} 段总结")

    part_summaries = []
    for i, chunk in enumerate(chunks):
        summary = _call_deepseek_summary(
            chunk,
            deepseek_key,
            f"{podcast_title}（第 {i+1}/{len(chunks)} 段）",
            part=f"（第 {i+1}/{len(chunks)} 段）",
        )
        if summary:
            part_summaries.append(summary)

    if len(part_summaries) <= 1:
        return part_summaries[0] if part_summaries else "总结生成失败"

    # 合并多段摘要
    merge_prompt = (
        f"请将以下播客「{podcast_title}」各段摘要合并为一份 500 字以内的完整总结：\n\n"
        + "\n---\n".join(part_summaries)
        + "\n\n格式要求：\n- 本播客核心话题\n- 嘉宾（如有人物）\n- 3-5 条核心观点\n- 一句话总结"
    )

    return _call_deepseek_summary(
        merge_prompt, deepseek_key, podcast_title, part="合并"
    )


def _call_deepseek_summary(text, api_key, title, part=""):
    """单次 DeepSeek 总结调用"""
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一位专业播客分析师，擅长从长篇对话中提炼核心观点。"
                    "请生成简洁的摘要（300 字以内），提取核心话题、关键观点和嘉宾信息。"
                ),
            },
            {
                "role": "user",
                "content": f"播客：{title}{part}\n\n{text}",
            },
        ],
        "temperature": 0.5,
        "max_tokens": 1024,
    }

    try:
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
    except Exception as e:
        print(f"⚠ DeepSeek 总结失败{part}: {e}")
        return None


# ── 主入口 ──────────────────────────────────────────────


def process_single_podcast(rss_url, output_dir, deepseek_key, model_name="large-v3"):
    """处理单个播客的完整管线：RSS → 下载 → 转录 → 总结"""
    print(f"\n{'─'*50}")
    print(f"🎙️ 处理播客: {rss_url}")

    try:
        # 1. 解析 RSS
        episode = parse_rss_feed(rss_url)
        if not episode:
            return None

        print(f"📻 最新一期: {episode['title'][:80]}")
        print(f"   发布日期: {episode['published']}")

        # 2. 检查是否本周
        if not is_this_week(episode["published"]):
            print(f"⏭ 非本周发布，跳过")
            return None

        # 3. 下载音频
        audio_dir = Path(output_dir) / "audio"
        audio_path = download_episode(episode, audio_dir)
        if not audio_path:
            return None

        # 4. Whisper 转录
        transcript_dir = Path(output_dir) / "transcripts"
        transcript = transcribe_audio(audio_path, transcript_dir, model_name)
        if not transcript or len(transcript) < 100:
            print("⚠ 转录结果过短，可能失败")
            return None

        # 5. DeepSeek 总结
        print("🤖 DeepSeek 总结播客内容...")
        summary = summarize_transcript(transcript, deepseek_key, episode["title"])

        # 6. 清理音频（节省空间）
        audio_path.unlink(missing_ok=True)
        print(f"🗑 已删除音频: {audio_path.name}")

        return {
            "podcast": episode["title"],
            "published": episode["published"],
            "duration": episode.get("duration", ""),
            "summary": summary,
        }

    except Exception as e:
        print(f"❌ 播客处理失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_all_podcasts(config):
    """处理配置中所有播客，返回格式化 Markdown 摘要文本"""
    rss_urls = config.get("podcast_rss_urls", [])
    if not rss_urls:
        print("⚠ 未配置播客 RSS，跳过")
        return ""

    output_dir = config.get("podcast_output_dir", "podcasts")
    deepseek_key = config["deepseek_api_key"]
    model_name = config.get("whisper_model", "large-v3")

    results = []
    for url in rss_urls:
        result = process_single_podcast(url, output_dir, deepseek_key, model_name)
        if result:
            results.append(result)

    if not results:
        return ""

    # 格式化为 Markdown
    lines = [
        "\n---\n",
        "## 🎙️ 本周 AI 播客精选\n",
    ]

    for r in results:
        pub = r.get("published", "")[:10]
        lines.append(f"### 📻 {r['podcast']}\n")
        lines.append(f"> 发布日期: {pub}  |  时长: {r.get('duration', '未知')}\n")
        lines.append(r.get("summary", "总结生成失败"))
        lines.append("")

    return "\n".join(lines)
