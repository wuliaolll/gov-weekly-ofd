"""
政务周报 OFD 生成器 — Flask 主应用
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, request, render_template, send_file, abort

from scraper import parse_column_page, parse_weekly_report, fetch_article_content
from doc_generator import generate_docx, generate_ofd
from scheduler import init_scheduler

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output" / "门户网站周报"
CONFIG_PATH = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"


def _init_logging() -> None:
    """初始化应用日志：同时输出到控制台和文件。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    has_file_handler = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(APP_LOG_PATH)
        for h in root.handlers
    )
    if not has_file_handler:
        file_handler = RotatingFileHandler(
            APP_LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    has_stream_handler = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


_init_logging()
logger = logging.getLogger(__name__)

# 任务状态存储（轻量，不用数据库）
task_status = {
    "running": False,
    "current": "",
    "progress": 0,
    "total": 0,
    "log": [],
}
task_lock = threading.Lock()

# 领导姓名→职务映射
LEADER_TITLES = {
    "王忠林": "省委书记",
    "李殿勋": "省长",
    "张文兵": "常务副省长",
    "盛阅春": "副省长",
    "彭勇": "秘书长",
    "陈平": "副省长",
    "黎东辉": "副省长",
    "胡亚波": "副省长",
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "column_url": "https://www.hubei.gov.cn/hbfb/zwzb/index.shtml",
        "schedule_hour": 8,
        "schedule_minute": 0,
        "auto_collect": True,
    }


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_filename(name: str, max_len: int = 80) -> str:
    """生成安全的文件/目录名"""
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "")
    name = name.strip(". ")
    if len(name) > max_len:
        name = name[:max_len]
    return name


def _add_log(msg: str):
    task_status["log"].append(msg)
    if len(task_status["log"]) > 200:
        task_status["log"] = task_status["log"][-100:]


def _extract_year_and_range(title: str) -> tuple[str, str]:
    """从周报标题中提取年份和日期范围"""
    import re
    # 标题格式: "省政府政务周报（3月30日-4月5日）" 或 "省政府政务周报（2025年12月29日-2026年1月4日）"
    m = re.search(r"[（(](.+?)[）)]", title)
    if not m:
        return "未知年份", "未知日期"
    date_range = m.group(1)

    # 尝试提取年份
    year_match = re.search(r"(\d{4})年", date_range)
    if year_match:
        year = year_match.group(1)
    else:
        from datetime import datetime
        year = str(datetime.now().year)

    # 清理为目录名格式
    range_clean = date_range.replace("年", "年")
    # 移除完整年份前缀，保留月日范围
    range_clean = re.sub(r"\d{4}年", "", range_clean).strip()

    return year, range_clean


def do_collect_and_generate(column_url: str, report_url: str = None):
    """执行采集和生成（在后台线程中运行）"""
    with task_lock:
        if task_status["running"]:
            return
        task_status["running"] = True
        task_status["progress"] = 0
        task_status["total"] = 0
        task_status["current"] = "开始采集..."
        task_status["log"] = []

    try:
        logger.info("collect start, column_url=%s, report_url=%s", column_url, report_url or "")
        # 获取周报列表
        if report_url:
            reports = [{"title": "手动指定", "url": report_url, "pub_date": ""}]
        else:
            _add_log(f"正在解析栏目页: {column_url}")
            task_status["current"] = "解析栏目页..."
            reports = parse_column_page(column_url)
            _add_log(f"找到 {len(reports)} 篇周报")

        task_status["total"] = len(reports)

        for idx, report_info in enumerate(reports):
            task_status["progress"] = idx
            task_status["current"] = f"处理: {report_info['title']}"
            _add_log(f"--- 处理第 {idx+1}/{len(reports)} 篇: {report_info['title']}")

            # 解析周报详情
            report_data = parse_weekly_report(report_info["url"])
            if not report_data["dates"]:
                _add_log(f"  未解析到日期数据，跳过")
                continue

            # 确定年份 — 从发布日期或标题中推断
            pub_year = ""
            if report_info.get("pub_date"):
                pub_year = report_info["pub_date"][:4]

            year_from_title, date_range = _extract_year_and_range(
                report_data["title"] or report_info["title"]
            )
            year = pub_year or year_from_title or "未知年份"
            year_dir = f"{year}年"
            range_dir = safe_filename(date_range) if date_range else "未知日期"

            _add_log(f"  目录: {year_dir}/{range_dir}")

            for date_group in report_data["dates"]:
                date_str = date_group["date"]  # 如 "3月30日"

                for activity in date_group["activities"]:
                    leader = activity["leader"]
                    leader_title = LEADER_TITLES.get(leader, "")
                    leader_dir = f"{leader}{leader_title}"
                    detail_url = activity.get("detail_url", "")

                    if not detail_url:
                        _add_log(f"  [{leader}] {date_str} - 无详情链接，跳过")
                        continue

                    _add_log(f"  [{leader}] {date_str} - 获取详情...")
                    task_status["current"] = f"{leader} - {date_str}"

                    try:
                        article = fetch_article_content(detail_url)
                    except Exception as e:
                        _add_log(f"  获取详情失败: {e}")
                        continue

                    if not article["content"]:
                        _add_log(f"  文章内容为空，跳过")
                        continue

                    # 构建输出路径：文件名 = 领导姓名 + 概览标题
                    overview_title = activity.get("overview_title", "")
                    if overview_title:
                        file_title = leader + overview_title
                    else:
                        file_title = article["title"] or activity["summary"][:60]
                    title_safe = safe_filename(file_title)
                    out_dir = OUTPUT_DIR / year_dir / range_dir / leader_dir / safe_filename(date_str)
                    out_dir.mkdir(parents=True, exist_ok=True)

                    docx_path = out_dir / f"{title_safe}.docx"
                    ofd_path = out_dir / f"{title_safe}.ofd"

                    # 生成 DOCX
                    try:
                        generate_docx(
                            title=article["title"],
                            content=article["content"],
                            output_path=str(docx_path),
                            paragraphs=article.get("paragraphs"),
                        )
                        _add_log(f"  ✓ DOCX: {docx_path.name}")
                    except Exception as e:
                        _add_log(f"  ✗ DOCX失败: {e}")

                    # 生成 OFD
                    try:
                        generate_ofd(
                            title=article["title"],
                            content=article["content"],
                            output_path=str(ofd_path),
                            paragraphs=article.get("paragraphs"),
                        )
                        _add_log(f"  ✓ OFD: {ofd_path.name}")
                    except Exception as e:
                        import traceback
                        _add_log(f"  ✗ OFD失败: {e}\n{''.join(traceback.format_exc()[-300:])}")

            task_status["progress"] = idx + 1

        _add_log("=== 采集生成完成 ===")
        logger.info("collect done, reports=%s", len(reports))
        task_status["current"] = "完成"

    except Exception as e:
        _add_log(f"采集异常: {e}")
        logger.exception("collect failed")
        task_status["current"] = f"错误: {e}"
    finally:
        task_status["running"] = False


# ============ 路由 ============

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "无效数据"}), 400
    cfg = load_config()
    for key in ("column_url", "schedule_hour", "schedule_minute", "auto_collect"):
        if key in data:
            cfg[key] = data[key]
    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/reports", methods=["GET"])
def get_reports():
    """获取已生成的周报文件树"""
    if not OUTPUT_DIR.exists():
        return jsonify([])

    tree = []
    for year_dir in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        year_node = {"name": year_dir.name, "ranges": []}
        for range_dir in sorted(year_dir.iterdir(), reverse=True):
            if not range_dir.is_dir():
                continue
            range_node = {"name": range_dir.name, "leaders": []}
            for leader_dir in sorted(range_dir.iterdir()):
                if not leader_dir.is_dir():
                    continue
                leader_node = {"name": leader_dir.name, "dates": []}
                for date_dir in sorted(leader_dir.iterdir()):
                    if not date_dir.is_dir():
                        continue
                    files = []
                    for f in sorted(date_dir.iterdir()):
                        if f.is_file() and f.suffix in (".docx", ".ofd"):
                            rel = f.relative_to(OUTPUT_DIR)
                            files.append({
                                "name": f.name,
                                "type": f.suffix[1:],
                                "path": str(rel).replace("\\", "/"),
                                "size": f.stat().st_size,
                            })
                    if files:
                        leader_node["dates"].append({
                            "name": date_dir.name,
                            "files": files,
                        })
                if leader_node["dates"]:
                    range_node["leaders"].append(leader_node)
            if range_node["leaders"]:
                year_node["ranges"].append(range_node)
        if year_node["ranges"]:
            tree.append(year_node)

    return jsonify(tree)


@app.route("/api/collect", methods=["POST"])
def collect():
    """手动触发采集"""
    if task_status["running"]:
        return jsonify({"error": "已有任务在运行"}), 409

    cfg = load_config()
    data = request.get_json() or {}
    report_url = data.get("report_url")

    t = threading.Thread(
        target=do_collect_and_generate,
        args=(cfg["column_url"], report_url),
        daemon=True,
    )
    t.start()
    return jsonify({"ok": True, "message": "采集任务已启动"})


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(task_status)


@app.route("/api/download/<path:filepath>")
def download(filepath):
    """下载生成的文件"""
    full_path = OUTPUT_DIR / filepath
    # 安全检查：确保路径在 OUTPUT_DIR 内
    try:
        full_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        abort(403)
    if not full_path.exists() or not full_path.is_file():
        abort(404)
    return send_file(full_path, as_attachment=True)


@app.route("/api/download-zip/<path:period_path>")
def download_zip(period_path):
    """打包下载某期全部文件，zip 内目录与生成目录保持一致"""
    import zipfile
    import io as _io

    period_dir = OUTPUT_DIR / period_path
    try:
        period_dir.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        abort(403)
    if not period_dir.exists() or not period_dir.is_dir():
        abort(404)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(period_dir.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in (".docx", ".ofd"):
                continue
            rel = f.relative_to(period_dir)
            zf.write(f, str(rel).replace("\\", "/"))
    buf.seek(0)

    zip_name = safe_filename(period_path.replace("/", "_").replace("\\", "_")) + ".zip"
    return send_file(buf, as_attachment=True, download_name=zip_name, mimetype="application/zip")


@app.route("/api/column-preview", methods=["POST"])
def column_preview():
    """预览栏目页解析结果"""
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "请提供URL"}), 400
    try:
        logger.info("column-preview start, url=%s", url)
        reports = parse_column_page(url)
        if not reports:
            logger.warning("column-preview empty result, url=%s", url)
            return jsonify({
                "error": "栏目预览返回0条，疑似被WAF拦截或页面结构变化",
                "log_path": str(APP_LOG_PATH),
            }), 502
        logger.info("column-preview done, url=%s, count=%s", url, len(reports))
        return jsonify(reports)
    except Exception as e:
        logger.exception("column-preview failed, url=%s", url)
        return jsonify({"error": str(e), "log_path": str(APP_LOG_PATH)}), 500


@app.route("/api/generate-article", methods=["POST"])
def generate_article():
    """针对单篇文章 URL 生成 DOCX + OFD，打包为 ZIP 下载"""
    import zipfile
    import io as _io
    import tempfile

    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "请提供文章URL"}), 400

    # 基本 URL 校验
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL 必须以 http:// 或 https:// 开头"}), 400

    try:
        article = fetch_article_content(url)
    except Exception as e:
        return jsonify({"error": f"获取文章失败: {e}"}), 500

    if not article.get("content"):
        return jsonify({"error": "文章内容为空"}), 400

    title = article.get("title") or "未命名文章"
    title_safe = safe_filename(title)

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, f"{title_safe}.docx")
        ofd_path = os.path.join(tmpdir, f"{title_safe}.ofd")

        errors = []
        try:
            generate_docx(
                title=title,
                content=article["content"],
                output_path=docx_path,
                paragraphs=article.get("paragraphs"),
            )
        except Exception as e:
            errors.append(f"DOCX: {e}")

        try:
            generate_ofd(
                title=title,
                content=article["content"],
                output_path=ofd_path,
                paragraphs=article.get("paragraphs"),
            )
        except Exception as e:
            errors.append(f"OFD: {e}")

        if not os.path.exists(docx_path) and not os.path.exists(ofd_path):
            return jsonify({"error": f"生成失败: {'; '.join(errors)}"}), 500

        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(docx_path):
                zf.write(docx_path, f"{title_safe}.docx")
            if os.path.exists(ofd_path):
                zf.write(ofd_path, f"{title_safe}.ofd")
        buf.seek(0)

        zip_name = f"{title_safe}.zip"
        return send_file(buf, as_attachment=True, download_name=zip_name, mimetype="application/zip")


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("app start, log file=%s", APP_LOG_PATH)

    cfg = load_config()
    if cfg.get("auto_collect"):
        init_scheduler(
            lambda: do_collect_and_generate(cfg["column_url"]),
            hour=cfg.get("schedule_hour", 8),
            minute=cfg.get("schedule_minute", 0),
        )

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    app.run(host=host, port=port, debug=False)
