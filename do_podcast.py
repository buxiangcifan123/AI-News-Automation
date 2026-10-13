"""立即下载 Lex Fridman #498 → 转录 → 摘要 → 追加到 Obsidian"""
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).parent

# 配置
with open(ROOT / "config.json") as f:
    cfg = json.load(f)

DEEPSEEK_KEY = cfg["deepseek_api_key"]
VAULT = Path(cfg["obsidian_vault_path"])
REPORT = VAULT / cfg["obsidian_subfolder"] / "2026-07-06.md"
AUDIO_DIR = Path(cfg["podcast_output_dir"]) / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# 1. 下载 Lex #498
import feedparser
print("📡 RSS...")
feed = feedparser.parse("https://lexfridman.com/feed/podcast/")
ep = feed.entries[0]
title = ep.get("title", "Unknown")
audio_url = ep.enclosures[0].get("href", "") if ep.get("enclosures") else ""
print(f"📻 {title}")

safe_name = title[:60].replace("/", "_").replace(":", "_").replace("?", "")
mp3 = AUDIO_DIR / f"{safe_name}.mp3"
if not mp3.exists():
    print(f"📥 下载中...")
    req = Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=1800) as r:
        mp3.write_bytes(r.read())
    print(f"✅ 下载完成 ({mp3.stat().st_size / 1024 / 1024:.0f} MB)")
else:
    print(f"📁 音频已存在")

# 2. Whisper 转录
import whisper
print("🔊 加载 Whisper turbo...")
model = whisper.load_model("turbo", device="cuda")
print("🎤 转录中...")
result = model.transcribe(str(mp3), language=None, verbose=False, fp16=True)
text = result.get("text", "")
print(f"✅ 转录完成: {len(text)} 字符 ({result.get('language', '?')})")

# 3. DeepSeek 总结（只取前 15K 字符）
print("🤖 DeepSeek 总结...")
chunk = text[:15000]
prompt = f"请用中文总结以下播客内容（300字以内），提取核心话题和关键观点：\n\n{chunk}"
payload = json.dumps({"model": "deepseek-v4-flash", "messages": [
    {"role": "system", "content": "你是一位专业播客分析师，擅长从长篇对话中提炼核心观点。"},
    {"role": "user", "content": prompt}
], "temperature": 0.5, "max_tokens": 1024}).encode()
req = Request("https://api.deepseek.com/v1/chat/completions", data=payload,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_KEY}"})
with urlopen(req, timeout=120) as r:
    summary = json.loads(r.read())["choices"][0]["message"]["content"]
print(f"✅ 摘要: {summary[:80]}...")

# 4. 追加到报告
sec = f"\n\n---\n\n## 🎙️ 本周 AI 播客精选\n\n### 📻 {title}\n\n{summary}\n"
REPORT.write_text(REPORT.read_text("utf-8") + sec, "utf-8")
print(f"✅ 已追加到 {REPORT}")

# 5. 清理音频
mp3.unlink(missing_ok=True)
print("🗑 音频已清理")
