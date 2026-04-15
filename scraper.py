"""
政务周报爬虫模块
解析湖北省人民政府门户网站政务周报栏目页和详情页
"""
from __future__ import annotations

import os
import re
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 已知的省级领导姓名列表（可动态扩展）
KNOWN_LEADERS = [
    "王忠林", "李殿勋", "马国强", "孙伟",
    "侯淅珉", "张文兵", "宁咏", "何良军",
    "琚朝晖", "盛阅春", "彭勇", "陈平",
    "黎东辉", "胡亚波", "雷文洁",
]


def fetch_page(url: str) -> str:
    """获取指定URL的HTML内容，使用curl绕过Python SSL兼容性问题"""
    curl_cmd = "curl.exe" if os.name == "nt" else "curl"
    result = subprocess.run(
        [curl_cmd, "-k", "-s", "-L", "--max-time", "30",
         "-H", f"User-Agent: {HEADERS['User-Agent']}",
         url],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed with code {result.returncode}: {result.stderr.decode('utf-8', errors='replace')}")
    # 尝试检测编码
    content = result.stdout
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_column_page(column_url: str) -> list[dict]:
    """
    解析栏目列表页，返回周报列表。
    返回格式: [{"title": "...", "url": "...", "pub_date": "..."}]
    """
    html = fetch_page(column_url)
    soup = BeautifulSoup(html, "lxml")
    results = []

    for li in soup.select("ul.hbgov-newslist-itemheight-18px li"):
        a_tag = li.select_one("a")
        span_tag = li.select_one("span")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if not href:
            continue
        full_url = urljoin(column_url, href)
        title = a_tag.get_text(strip=True)
        pub_date = span_tag.get_text(strip=True) if span_tag else ""
        results.append({
            "title": title,
            "url": full_url,
            "pub_date": pub_date,
        })

    return results


def parse_weekly_report(report_url: str) -> dict:
    """
    解析周报详情页，按日期分组提取领导活动。
    返回格式:
    {
        "title": "省政府政务周报（...）",
        "dates": [
            {
                "date": "3月30日",
                "activities": [
                    {
                        "leader": "李殿勋",
                        "summary": "主持召开省政府常务会议...",
                        "detail_url": "https://...",
                    }
                ]
            }
        ]
    }
    """
    html = fetch_page(report_url)
    soup = BeautifulSoup(html, "lxml")

    # 获取标题
    title_tag = soup.select_one("h1") or soup.select_one("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    # 清理标题中的站点后缀
    title = re.sub(r"\s*-\s*湖北省人民政府门户网站.*$", "", title)

    # 获取正文容器
    content_div = (
        soup.select_one(".bt_content")
        or soup.select_one("#myText")
        or soup.select_one(".TRS_Editor")
        or soup.select_one(".article-content")
        or soup.select_one(".hbgov-article-content")
    )
    if not content_div:
        # 兜底：尝试查找包含日期模式的最大容器
        for div in soup.find_all("div"):
            text = div.get_text()
            if re.search(r"\d{1,2}月\d{1,2}日", text) and len(text) > 500:
                content_div = div
                break
    if not content_div:
        return {"title": title, "dates": []}

    # 将HTML内容转为段落列表进行分析
    paragraphs = _extract_paragraphs(content_div)
    dates_data = _group_by_date(paragraphs)

    return {"title": title, "dates": dates_data}


def _extract_paragraphs(container) -> list[dict]:
    """
    从内容容器提取段落信息。
    每个段落包含: text, html, links, is_date_header, leader_name

    去重策略：跳过包含可处理子元素的容器元素，避免父+子重复。
    不使用全局 seen_texts 去重，否则同一段落（如 ►►► 、领导名）在不同日期区间多次出现时会被错误丢弃。
    """
    # 可直接处理的元素类型（语义段落单元）
    PROCESSABLE = {"p", "h1", "h2", "h3", "h4", "td", "th", "li"}
    paragraphs = []

    for elem in container.descendants:
        if elem.name not in PROCESSABLE:
            continue
        # 跳过包含可处理子元素的容器（其内容将由子元素单独处理，避免重复）
        if any(c.name in PROCESSABLE for c in elem.children if hasattr(c, 'name') and c.name):
            continue

        text = elem.get_text(strip=True)
        if not text or len(text) < 2:
            continue

        # 检测日期头
        date_match = re.match(r"^(\d{1,2}月\d{1,2}日)$", text)
        is_date = bool(date_match)

        # 提取链接：优先"详情<<"类，同时保留所有政府文章 URL（.shtml/.html）
        links = []
        for a in elem.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("javascript") or href == "#":
                continue
            link_text = a.get_text(strip=True)
            if ("详情" in link_text or "<<" in link_text
                    or href.endswith(".shtml") or href.endswith(".html")):
                links.append(href)

        paragraphs.append({
            "text": text,
            "html": str(elem),
            "links": links,
            "is_date_header": is_date,
            "date_value": date_match.group(1) if date_match else None,
        })

    return paragraphs


def _group_by_date(paragraphs: list[dict]) -> list[dict]:
    """将段落按日期分组，提取领导活动"""
    dates = []
    current_date = None
    current_activities = []
    buffer_texts = []
    buffer_links = []
    current_leader_hint = None  # 由 ►► 领导名 行设置，作为 _identify_leader 的兜底

    def flush_buffer():
        nonlocal buffer_texts, buffer_links
        if not buffer_texts:
            return
        combined = "\n".join(buffer_texts)
        leader = current_leader_hint
        if leader:
            # 提取概览标题：第一条短文本（不以日期开头、不含"详情"）
            overview = ""
            if buffer_texts:
                first = buffer_texts[0].strip()
                first = re.sub(r'\s*详情\s*[<＜《]+.*$', '', first).strip()
                if not re.match(r'^\d{1,2}月\d{1,2}日', first):
                    overview = first
            current_activities.append({
                "leader": leader,
                "summary": combined,
                "overview_title": overview,
                "detail_url": buffer_links[0] if buffer_links else "",
            })
        buffer_texts = []
        buffer_links = []

    # ► 或 ►► 标记是活动块分隔符（一个或多个 ►）
    arrow_pattern = re.compile(r"^►+")

    for para in paragraphs:
        text = para["text"]

        if para["is_date_header"]:
            flush_buffer()
            if current_date and current_activities:
                dates.append({"date": current_date, "activities": current_activities})
            current_date = para["date_value"]
            current_activities = []
            buffer_texts = []
            buffer_links = []
            current_leader_hint = None
            continue

        if current_date is None:
            continue

        # 检测 ►+ 开头：分隔符，箭头后可能直接跟着领导名（如 "►► 李殿勋"）
        if arrow_pattern.match(text):
            flush_buffer()
            remainder = arrow_pattern.sub("", text).strip()
            if remainder in KNOWN_LEADERS:
                # 纯领导名：只更新 hint，不写入 buffer（避免重复条目）
                current_leader_hint = remainder
            elif remainder:
                # 箭头后跟的是活动描述，直接进 buffer
                buffer_texts.append(remainder)
                buffer_links.extend(para["links"])
            else:
                # 纯箭头分隔符（无名字），重置 hint
                current_leader_hint = None
            continue

        # 检测独立领导名行（单独成段的领导姓名，如 "李殿勋"）
        stripped = text.strip()
        if stripped in KNOWN_LEADERS:
            flush_buffer()
            current_leader_hint = stripped  # 设置 hint，不写入 buffer
            continue

        # 检测以领导名开头的新标题行（通常较长，包含活动描述）
        starts_with_leader = False
        for name in KNOWN_LEADERS:
            if text.startswith(name) and len(text) > len(name) + 2:
                starts_with_leader = True
                break

        if starts_with_leader and buffer_texts:
            flush_buffer()

        # 跳过纯图片或编辑信息行
        if re.match(r"^(编辑|责编|审核|扫一扫)[:：]", text):
            continue

        buffer_texts.append(text)
        buffer_links.extend(para["links"])

        # "详情<<"类链接段落标志一条活动的结束（参与人员+详情链接行）
        # 立即 flush，但保留 current_leader_hint，以便同一领导的下一条活动继续归属
        if para["links"] and buffer_texts and ("详情" in text or "<<" in text):
            flush_buffer()

    # 收尾
    flush_buffer()
    if current_date and current_activities:
        dates.append({"date": current_date, "activities": current_activities})

    return dates


def _identify_leader(text: str) -> str | None:
    """从文本中识别领导姓名"""
    for name in KNOWN_LEADERS:
        if name in text:
            return name
    return None


def fetch_article_content(url: str) -> dict:
    """
    获取详情文章的正文内容。
    返回: {"title": "...", "content": "...", "pub_date": "..."}
    """
    html = fetch_page(url)
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.select_one("h1") or soup.select_one("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    title = re.sub(r"\s*-\s*湖北省人民政府门户网站.*$", "", title)

    # 发布时间
    pub_date = ""
    date_span = soup.find(string=re.compile(r"\d{4}-\d{2}-\d{2}"))
    if date_span:
        m = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})", date_span.get_text())
        if m:
            pub_date = m.group(1).strip()

    # 正文
    content_div = (
        soup.select_one(".bt_content")
        or soup.select_one("#myText")
        or soup.select_one(".TRS_Editor")
        or soup.select_one(".article-content")
        or soup.select_one(".hbgov-article-content")
    )

    content_paragraphs = []
    if content_div:
        # 优先取 <p> 标签保留原始段落结构
        p_tags = content_div.find_all("p")
        if p_tags:
            for p in p_tags:
                text = p.get_text(strip=True)
                if not text or len(text) <= 2:
                    continue
                # 过滤编辑/审核等署名信息
                if re.match(r"^(编辑|责编|审核|扫一扫|来源|（编辑|（责编|（审核)", text):
                    continue
                # 过滤末尾短署名行（纯中文人名，含全角空格，如「姚　盼」）
                stripped = re.sub(r'[\s\u3000]+', '', text)
                if len(stripped) <= 4 and re.match(r'^[\u4e00-\u9fff]+$', stripped):
                    continue
                # 过滤"图解：..."等附加信息行
                if re.match(r"^图解[：:]", text):
                    continue
                # 过滤记者署名，如（肖丽琼）（湖北日报记者邓伟）
                if re.match(r'^[（(].*?记者.*?[）)]$', text) or re.match(r'^[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]$', text):
                    continue
                # 检测对齐方式
                style = p.get("style", "")
                align = "center" if "text-align: center" in style or "text-align:center" in style else "left"
                content_paragraphs.append({"text": text, "align": align})
        else:
            # 无 <p> 标签时，用 <br> 拆分
            raw_text = content_div.get_text(separator="\n")
            for line in raw_text.split("\n"):
                line = line.strip()
                if not line or len(line) <= 2:
                    continue
                if re.match(r"^(编辑|责编|审核|扫一扫|来源)", line):
                    continue
                if re.match(r"^图解[：:]", line):
                    continue
                # 过滤记者署名
                if re.match(r'^[（(].*?记者.*?[）)]$', line) or re.match(r'^[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]$', line):
                    continue
                content_paragraphs.append({"text": line, "align": "left"})

    # 从正文开头提取居中段落作为真正标题（舍弃 <h1> 标题）
    body_title_parts = []
    while content_paragraphs:
        first = content_paragraphs[0]
        if isinstance(first, dict) and first.get("align") == "center":
            body_title_parts.append(first["text"])
            content_paragraphs.pop(0)
        else:
            break
    if body_title_parts:
        title = "".join(body_title_parts)

    # 清理末尾段落中拼接的记者署名，如 "...发展。（湖北日报记者邓伟）"
    if content_paragraphs:
        last = content_paragraphs[-1]
        if isinstance(last, dict):
            cleaned = re.sub(r'[（(][^）)]*?记者[^）)]*?[）)]\s*$', '', last["text"]).strip()
            cleaned = re.sub(r'[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]\s*$', '', cleaned).strip()
            if cleaned:
                content_paragraphs[-1] = {**last, "text": cleaned}
            else:
                content_paragraphs.pop()

    content = "\n\n".join(
        p["text"] if isinstance(p, dict) else p for p in content_paragraphs
    )

    return {"title": title, "content": content, "pub_date": pub_date, "paragraphs": content_paragraphs}


if __name__ == "__main__":
    # 测试用
    import json

    url = "https://www.hubei.gov.cn/hbfb/zwzb/index.shtml"
    reports = parse_column_page(url)
    print(f"找到 {len(reports)} 篇周报:")
    for r in reports[:3]:
        print(f"  {r['title']} - {r['pub_date']}")

    if reports:
        print(f"\n解析第一篇: {reports[0]['url']}")
        data = parse_weekly_report(reports[0]["url"])
        print(json.dumps(data, ensure_ascii=False, indent=2))
