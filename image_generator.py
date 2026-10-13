"""
图片生成模块 - 用 DeepSeek 生成 prompt → Agnes API 生成配图
"""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


def generate_image_prompts(news_items, deepseek_key):
    """用 DeepSeek 生成图片 prompt（轻量调用，~500 tokens 输入）"""
    # 只取标题，控制输入长度
    titles = []
    for item in news_items:
        title = item.get("title", "")[:80]
        titles.append(title)

    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

    system_prompt = (
        "你是一位 AI 插图设计师。请为每条 AI 新闻生成一个适合 AI 画图的英文 prompt。"
        "要求：风格统一为 modern tech illustration，简洁专业，50 词以内。"
        "你必须只输出一个 JSON 数组，格式："
        '[{"index": 1, "title": "原标题", "prompt": "English image prompt here"}, ...]'
    )

    user_message = f"请为以下 {len(titles)} 条 AI 新闻生成配图 prompt：\n\n{titles_text}"

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    req = Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {deepseek_key}",
        },
    )
    with urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"]

    # 尝试从返回文本中提取 JSON（可能带 ```json 包裹）
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if match:
        content = match.group(0)

    prompts = json.loads(content)
    print(f"✅ 生成 {len(prompts)} 个图片 prompt")
    return prompts


def call_agnes_api(prompt, api_key, size="1024x768"):
    """调用 Agnes.ai API (Image 2.1 Flash) 生成一张图片，返回图片字节数据"""
    payload = {
        "model": "agnes-image-2.1-flash",
        "prompt": prompt,
        "size": size,
        "extra_body": {
            "response_format": "url",
        },
    }

    req = Request(
        "https://apihub.agnes-ai.com/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    with urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    # Agnes API 返回格式: {"data": [{"url": "https://...", "b64_json": null}]}
    if "data" in result and isinstance(result["data"], list) and len(result["data"]) > 0:
        image_url = result["data"][0].get("url")
        if image_url:
            # 下载图片
            req2 = Request(image_url)
            with urlopen(req2, timeout=60) as img_resp:
                return img_resp.read()
        # 备用：base64 格式
        b64 = result["data"][0].get("b64_json")
        if b64:
            import base64
            return base64.b64decode(b64)

    raise ValueError(f"无法从 Agnes 响应中提取图片: {list(result.keys())}")


def generate_images_for_report(news_data, report_text, config):
    """
    完整图片管线：
    1. 取 rank 最高的前 N 条新闻
    2. 用 DeepSeek 生成 image prompt（轻量）
    3. 逐个调 Agnes API 生成图片
    4. 将图片插入到报告中
    """
    max_images = config.get("max_images_per_report", 5)
    deepseek_key = config["deepseek_api_key"]
    agnes_key = config.get("agnes_api_key", "")

    if not agnes_key or agnes_key == "填写你的 Agnes API Key":
        print("⚠ 未配置 Agnes API Key，跳过图片生成")
        return report_text

    # 按 rank 排序，取前 N 条不同标题的新闻
    sorted_news = sorted(news_data, key=lambda x: x.get("rank", 999))
    seen_titles = set()
    top_news = []
    for item in sorted_news:
        title = item.get("title", "").strip()
        if title and title not in seen_titles and len(title) > 5:
            top_news.append(item)
            seen_titles.add(title)
        if len(top_news) >= max_images:
            break

    print(f"🖼️ 将为 Top {len(top_news)} 条新闻生成配图")

    # Step 1: 生成图片 prompt
    try:
        prompt_data = generate_image_prompts(top_news, deepseek_key)
    except Exception as e:
        print(f"❌ 图片 prompt 生成失败: {e}")
        return report_text

    # Step 2: 生成图片
    today = datetime.now().strftime("%Y-%m-%d")
    img_dir = Path(config.get("image_output_dir", "output/images")) / today
    img_dir.mkdir(parents=True, exist_ok=True)

    image_map = {}  # {title: image_local_path}

    for item in prompt_data:
        idx = item.get("index", 0)
        prompt = item.get("prompt", "")
        title = item.get("title", "")

        if not prompt:
            continue

        try:
            print(f"🎨 生成图片 {idx}/{len(prompt_data)}: {title[:40]}...")
            img_bytes = call_agnes_api(prompt, agnes_key)
            img_path = img_dir / f"news_{idx}.png"
            img_path.write_bytes(img_bytes)
            image_map[title] = str(img_path)
            print(f"✅ 已保存: {img_path} ({len(img_bytes)/1024:.0f} KB)")
        except Exception as e:
            print(f"⚠ 图片 {idx} 生成失败: {e}")

    if not image_map:
        print("⚠ 未能生成任何图片")
        return report_text

    # Step 3: 将图片插入报告
    # 复制图片到 Obsidian vault 的 images 目录
    obsidian_subfolder = config.get("obsidian_subfolder", "AI新闻")
    vault_path = config.get("obsidian_vault_path", "")

    if vault_path:
        vault_img_dir = Path(vault_path) / obsidian_subfolder / "images" / today
        vault_img_dir.mkdir(parents=True, exist_ok=True)
        for title, src_path in image_map.items():
            dst_path = vault_img_dir / Path(src_path).name
            dst_path.write_bytes(Path(src_path).read_bytes())

    # 在报告中插入图片：找到匹配新闻标题的 ### 标题行，其后插入图片
    lines = report_text.split("\n")
    result_lines = []
    inserted_titles = set()

    for line in lines:
        result_lines.append(line)

        # 遇到 ### 标题行时，检查是否匹配要配图的新闻
        if line.startswith("### "):
            for title, img_path in image_map.items():
                if title in inserted_titles:
                    continue
                # 用标题前 10 个字符匹配
                keyword = title[:10].strip()
                if len(keyword) >= 4 and keyword in line:
                    img_file = Path(img_path).name
                    result_lines.append(
                        f"\n![配图](images/{today}/{img_file})\n"
                    )
                    inserted_titles.add(title)
                    break

    print(f"📸 已插入 {len(inserted_titles)} 张图片到报告")
    return "\n".join(result_lines)
