"""
文档生成模块 — DOCX + OFD
按照公文格式模板生成文件
"""

import io
import os
import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

# ============ 字体注册 ============

# Windows 字体路径（系统目录 + 用户目录）
_FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
]


def _find_font(candidates: list[str]) -> str | None:
    for name in candidates:
        for d in _FONT_DIRS:
            p = d / name
            if p.exists():
                return str(p)
    return None


# 方正小标宋简体 — 标题
FZXBS_PATH = _find_font(["FZXBSJW.TTF", "fzxbsjw.ttf", "FZXBSFW.TTF", "方正小标宋简体.ttf", "方正小标宋_GBK.TTF"])

# 仿宋_GB2312 — 正文（优先 GB2312 版本）
FS_PATH = _find_font(["标准仿宋体简.TTF", "仿宋_GB2312.ttf", "FSGB2312.TTF", "simfang.ttf", "SIMFANG.TTF", "fangsong.ttf"])

# Times New Roman
TNR_PATH = _find_font(["times.ttf", "TIMES.TTF", "Times.ttf"])

# 注册 ReportLab 字体
_fonts_registered = False


def _register_reportlab_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    if FZXBS_PATH:
        pdfmetrics.registerFont(TTFont("FZXiaoBiaoSong", FZXBS_PATH))
        from reportlab.lib.fonts import addMapping
        addMapping("FZXiaoBiaoSong", 0, 0, "FZXiaoBiaoSong")
    if FS_PATH:
        pdfmetrics.registerFont(TTFont("FangSong", FS_PATH))
        from reportlab.lib.fonts import addMapping
        addMapping("FangSong", 0, 0, "FangSong")
    if TNR_PATH:
        pdfmetrics.registerFont(TTFont("TimesNewRoman", TNR_PATH))
        from reportlab.lib.fonts import addMapping
        addMapping("TimesNewRoman", 0, 0, "TimesNewRoman")
    _fonts_registered = True


# ============ DOCX 生成 ============

def generate_docx(title: str, content: str, output_path: str, paragraphs: list[dict] | None = None):
    """
    按公文格式生成 DOCX 文件

    页面: A4, 页边距上3.7cm 下3.0cm 左3.2cm 右3.2cm
    标题: 方正小标宋简体, 二号(22pt), 居中, 第二行, 与正文空一行
    正文: 仿宋GB2312, 小二号(18pt), 首行缩进2字符, 行距固定32磅, 两端对齐
    数字英文: Times New Roman
    颜色: 黑色
    无页码, 无图片
    """
    doc = Document()

    # 页面设置
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(3.7)
    section.bottom_margin = Cm(3.0)
    section.left_margin = Cm(3.2)
    section.right_margin = Cm(3.2)

    # 删除默认段落
    if doc.paragraphs:
        doc.paragraphs[0].clear()

    # 第一行空行（标题在第二行）
    blank = doc.add_paragraph()
    blank.paragraph_format.space_before = Pt(0)
    blank.paragraph_format.space_after = Pt(0)
    _set_line_spacing_fixed(blank, 32)

    # 标题
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_line_spacing_fixed(title_para, 32)
    title_para.paragraph_format.space_before = Pt(0)
    title_para.paragraph_format.space_after = Pt(0)
    title_run = title_para.add_run(title)
    title_run.font.size = Pt(22)  # 二号
    _set_font_name(title_run, "方正小标宋简体", "FZXiaoBiaoSong")
    title_run.font.color.rgb = None  # 黑色

    # 标题与正文间空一行
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(0)
    _set_line_spacing_fixed(spacer, 32)

    # 正文段落
    if paragraphs:
        para_list = paragraphs
    else:
        para_list = [{"text": p.strip(), "align": "left"} for p in content.split("\n") if p.strip()]
    for para_info in para_list:
        para_text = para_info["text"] if isinstance(para_info, dict) else para_info
        para_align = para_info.get("align", "left") if isinstance(para_info, dict) else "left"

        p = doc.add_paragraph()
        if para_align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.first_line_indent = Pt(0)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.first_line_indent = Pt(18 * 2)  # 首行缩进2个字符（小二号=18pt）
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        _set_line_spacing_fixed(p, 32)

        # 拆分中文和英文/数字，分别设置字体
        segments = _split_cn_en(para_text)
        for text, is_ascii in segments:
            run = p.add_run(text)
            run.font.size = Pt(18)  # 小二号
            if is_ascii:
                _set_font_name(run, "Times New Roman", "Times New Roman")
            else:
                _set_font_name(run, "仿宋_GB2312", "FangSong_GB2312")
            run.font.bold = False
            run.font.color.rgb = None  # 黑色

    doc.save(output_path)


def _set_line_spacing_fixed(paragraph, pt_value: int):
    """设置固定行距"""
    pf = paragraph.paragraph_format
    pf.line_spacing = Pt(pt_value)
    # 确保是固定值
    pPr = paragraph._element.get_or_add_pPr()
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = pPr.makeelement(qn("w:spacing"), {})
        pPr.append(spacing)
    spacing.set(qn("w:lineRule"), "exact")
    spacing.set(qn("w:line"), str(int(pt_value * 20)))  # 转为 twips


def _set_font_name(run, cn_name: str, en_name: str):
    """设置中英文字体名"""
    run.font.name = en_name
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rPr.makeelement(qn("w:rFonts"), {})
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), en_name)
    rFonts.set(qn("w:hAnsi"), en_name)
    rFonts.set(qn("w:eastAsia"), cn_name)
    rFonts.set(qn("w:cs"), en_name)


def _split_cn_en(text: str) -> list[tuple[str, bool]]:
    """将文本拆分为中文段和ASCII段"""
    segments = []
    current = ""
    current_is_ascii = False

    for ch in text:
        is_ascii = ord(ch) < 128 and ch not in " \t"
        # 空格/制表符跟随前一段
        if ch in " \t":
            current += ch
            continue
        if not current:
            current = ch
            current_is_ascii = is_ascii
        elif is_ascii == current_is_ascii:
            current += ch
        else:
            segments.append((current, current_is_ascii))
            current = ch
            current_is_ascii = is_ascii

    if current:
        segments.append((current, current_is_ascii))

    return segments


def _rl_mixed_font_text(text: str, cn_font: str, en_font: str | None) -> str:
    """为 ReportLab Paragraph 生成混合字体的 XML 标记文本。
    中文部分用 cn_font，ASCII 数字/字母部分用 en_font。
    文本需先经过 _xml_escape。"""
    if not en_font:
        return _xml_escape(text)
    segments = _split_cn_en(text)
    parts = []
    for seg_text, is_ascii in segments:
        safe = _xml_escape(seg_text)
        if is_ascii:
            parts.append(f'<font name="{en_font}">{safe}</font>')
        else:
            parts.append(safe)
    return ''.join(parts)


# ============ OFD 生成 ============

def generate_ofd(title: str, content: str, output_path: str, paragraphs: list[dict] | None = None):
    """
    生成 OFD 文件：先用 ReportLab 生成 PDF，再用 easyofd 转换为 OFD
    """
    _register_reportlab_fonts()

    # 补充 ReportLab CJK 禁则字符表（缺少常用中文标点）
    import reportlab.platypus.paragraph as _rl_para
    import reportlab.lib.textsplit as _rl_ts
    _extra_no_start = '\uff0c\uff1b\uff1a\u201d\u2019\u300b\u3011\uff5d\u2026\u2014\u2013\uff5e'
    if '\uff0c' not in _rl_para.ALL_CANNOT_START:
        _rl_para.ALL_CANNOT_START += _extra_no_start
    if '\uff0c' not in _rl_ts.ALL_CANNOT_START:
        _rl_ts.ALL_CANNOT_START += _extra_no_start

    # 确定可用字体
    title_font = "FZXiaoBiaoSong" if FZXBS_PATH else "FangSong" if FS_PATH else "Helvetica"
    body_font = "FangSong" if FS_PATH else "Helvetica"

    # 构建 PDF 样式
    title_style = ParagraphStyle(
        "GovTitle",
        fontName=title_font,
        fontSize=22,
        leading=32,
        alignment=TA_CENTER,
        textColor="black",
        spaceAfter=0,
        spaceBefore=0,
        wordWrap='CJK',
    )

    body_style = ParagraphStyle(
        "GovBody",
        fontName=body_font,
        fontSize=18,
        leading=32,
        alignment=TA_JUSTIFY,
        firstLineIndent=18 * 2,
        textColor="black",
        spaceAfter=0,
        spaceBefore=0,
        wordWrap='CJK',
    )

    # 生成 PDF 到内存
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        topMargin=3.7 * cm,
        bottomMargin=3.0 * cm,
        leftMargin=3.2 * cm,
        rightMargin=3.2 * cm,
    )

    story = []

    # 第一行空白（标题在第二行）
    story.append(Spacer(1, 32))

    # 标题
    en_font = "TimesNewRoman" if TNR_PATH else None
    safe_title = _rl_mixed_font_text(title, title_font, en_font)
    story.append(Paragraph(safe_title, title_style))

    # 标题与正文间空一行
    story.append(Spacer(1, 32))

    # 居中样式（用于居中段落）
    center_body_style = ParagraphStyle(
        "GovBodyCenter",
        fontName=body_font,
        fontSize=18,
        leading=32,
        alignment=TA_CENTER,
        firstLineIndent=0,
        textColor="black",
        spaceAfter=0,
        spaceBefore=0,
        wordWrap='CJK',
    )

    # 正文段落
    if paragraphs:
        para_list = paragraphs
    else:
        para_list = [{"text": p.strip(), "align": "left"} for p in content.split("\n") if p.strip()]
    for para_info in para_list:
        para_text = para_info["text"] if isinstance(para_info, dict) else para_info
        para_align = para_info.get("align", "left") if isinstance(para_info, dict) else "left"
        safe_text = _rl_mixed_font_text(para_text, body_font, en_font)
        style = center_body_style if para_align == "center" else body_style
        story.append(Paragraph(safe_text, style))

    doc.build(story)

    # PDF → OFD
    pdf_bytes = pdf_buffer.getvalue()
    _pdf_to_ofd(pdf_bytes, output_path)


def _xml_escape(text: str) -> str:
    """转义文本中的 XML 特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _is_wide_char(ch):
    """判断字符是否为全角（CJK、全角标点等）"""
    cp = ord(ch)
    # CJK统一汉字、CJK扩展、全角标点、中文标点等
    if cp >= 0x4E00 and cp <= 0x9FFF: return True
    if cp >= 0x3400 and cp <= 0x4DBF: return True
    if cp >= 0x3000 and cp <= 0x303F: return True  # CJK标点
    if cp >= 0xFF01 and cp <= 0xFF60: return True  # 全角ASCII
    if cp >= 0x2000 and cp <= 0x206F: return True  # 通用标点（中文逗号句号等）
    if cp >= 0x2018 and cp <= 0x201F: return True  # 引号
    if cp >= 0xFE30 and cp <= 0xFE4F: return True  # CJK兼容
    if cp >= 0x20000 and cp <= 0x2A6DF: return True  # CJK扩展B
    return False


def _calc_delta_x(text, total_length, font_size_mm):
    """计算每个字符的 DeltaX，CJK字符宽度约=font_size，ASCII约=font_size*0.5"""
    if len(text) <= 1:
        return "0"
    # 估算每个字符的理论宽度
    widths = []
    for ch in text:
        if _is_wide_char(ch):
            widths.append(font_size_mm)  # 全角
        else:
            widths.append(font_size_mm * 0.5)  # 半角
    # 按比例缩放到实际总宽度
    est_total = sum(widths)
    if est_total > 0:
        scale = total_length / est_total
        widths = [w * scale for w in widths]
    else:
        widths = [total_length / len(text)] * len(text)
    # DeltaX 是从第 1 个字符到第 n-1 个字符的步进值（共 n-1 个值）
    # 第 i 个 DeltaX = 第 i 个字符的宽度（从第 i 个字符起点到第 i+1 个字符起点）
    deltas = [f"{widths[i]:.4f}" for i in range(len(text) - 1)]
    return " ".join(deltas)


def _build_content_res_fixed(ofd_writer, pdf_info_list, id_obj, pfd_res_uuid_map):
    """替代 build_content_res，修复 DeltaX 均匀分配和文本颜色问题"""
    from easyofd.draw.draw_ofd import ContentTemplate
    content_res_list = []
    for idx, content in enumerate(pdf_info_list):
        ImageObject = []
        TextObject = []
        PhysicalBox = pfd_res_uuid_map["other"]["page_size"][idx]
        PhysicalBox = f"0 0 {PhysicalBox[0]} {PhysicalBox[1]}"
        for block in content:
            bbox = block['bbox']
            OP = ofd_writer.OP
            x0 = bbox[0] / OP
            y0 = bbox[1] / OP
            length = (bbox[2] - bbox[0]) / OP
            height = (bbox[3] - bbox[1]) / OP
            if block["type"] == "text":
                text = block.get("text")
                count = len(text)
                font_size_mm = block.get("size") / OP
                delta_x = _calc_delta_x(text, length, font_size_mm)
                TextObject.append({
                    "@ID": 0,
                    "res_uuid": block.get("res_uuid"),
                    "@Font": "",
                    "ofd:FillColor": {"Value": "0 0 0"},  # 黑色文字
                    "ofd:TextCode": {
                        "#text": text,
                        "@X": "0",
                        "@Y": f"{font_size_mm}",
                        "@DeltaX": delta_x
                    },
                    "@size": font_size_mm,
                    "@Boundary": f"{x0} {y0} {length} {height}",
                })
            elif block["type"] == "img":
                ImageObject.append({
                    "@ID": 0,
                    "res_uuid": block.get("res_uuid"),
                    "@Boundary": f"{x0} {y0} {length} {height}",
                    "@ResourceID": ""
                })
        conten = ContentTemplate(PhysicalBox=PhysicalBox, ImageObject=ImageObject,
                                 CGTransform=[], PathObject=[], TextObject=TextObject, id_obj=id_obj)
        content_res_list.append(conten)
    return content_res_list


def _pdf_to_ofd(pdf_bytes: bytes, output_path: str):
    """用 easyofd 将 PDF 转为 OFD"""
    import tempfile
    import shutil
    try:
        from easyofd.ofd import OFD
    except ImportError as e:
        import sys
        raise ImportError(
            f"easyofd 模块导入失败: {e}\n"
            f"当前 Python: {sys.executable}\n"
            f"请确保使用 venv 环境运行: .venv\\Scripts\\python.exe app.py"
        ) from e

    # monkey-patch easyofd 修复 optional_text 模式下 Document PhysicalBox 不正确的 bug
    from easyofd.draw.draw_ofd import OFDWrite
    _orig_call = OFDWrite.__call__

    def _patched_call(self, pdf_bytes=None, pil_img_list=None, optional_text=False):
        if optional_text and pdf_bytes:
            from easyofd.draw.pdf_parse import DPFParser
            from easyofd.draw.draw_ofd import CurId

            # monkey-patch PDF 解析器，修复 span bbox 使用行 bbox 的 bug
            import easyofd.draw.pdf_parse as _pdf_parse_mod
            _orig_extract = DPFParser.extract_text_with_details

            def _fixed_extract(self_parser, pdf_bytes_inner):
                """修复: 使用 span['bbox'] 替代 line_rect，使每个文字段有独立定位"""
                import fitz
                import io as _io
                from uuid import uuid1
                details_list = []
                pdf_stream = _io.BytesIO(pdf_bytes_inner)
                with fitz.open(stream=pdf_stream, filetype="pdf") as doc:
                    res_uuid_map = {"img": {}, "font": {}, "other": {}}
                    for page_num in range(len(doc)):
                        page_details = []
                        page = doc.load_page(page_num)
                        rect = page.rect
                        if res_uuid_map["other"].get("page_size"):
                            res_uuid_map["other"]["page_size"][page_num] = [rect.width, rect.height]
                        else:
                            res_uuid_map["other"]["page_size"] = {page_num: [rect.width, rect.height]}
                        blocks = page.get_text("dict").get("blocks", [])
                        image_list = page.get_images(full=True)
                        for block in blocks:
                            for line in block.get("lines", []):
                                line_bbox = line["bbox"]
                                for span in line.get("spans", []):
                                    span_text = span.get("text", "")
                                    if not span_text:
                                        continue
                                    font_name = span.get("font", "")
                                    font_size = span.get("size")
                                    font_color = span.get("color")
                                    # 使用 span bbox 的 x 坐标（精确水平定位），
                                    # 但使用 line bbox 的 y 坐标（基线对齐）
                                    span_bbox = span.get("bbox", line_bbox)
                                    aligned_bbox = [span_bbox[0], line_bbox[1], span_bbox[2], line_bbox[3]]
                                    # 维护 font uuid map
                                    if font_name not in res_uuid_map["font"].values():
                                        res_uuid = str(uuid1())
                                        res_uuid_map["font"][res_uuid] = font_name
                                    else:
                                        vs = list(res_uuid_map["font"].values())
                                        ks = list(res_uuid_map["font"].keys())
                                        res_uuid = ks[vs.index(font_name)]
                                    page_details.append({
                                        "page": page_num, "text": span_text,
                                        "font": font_name, "res_uuid": res_uuid,
                                        "size": font_size, "color": font_color,
                                        "bbox": aligned_bbox, "type": "text"
                                    })
                        # 处理图片
                        for img_info in image_list:
                            xref = img_info[0]
                            try:
                                base_image = doc.extract_image(xref)
                                if base_image:
                                    img_bytes = base_image["image"]
                                    img_uuid = str(uuid1())
                                    res_uuid_map["img"][img_uuid] = _io.BytesIO(img_bytes)
                                    page_details.append({
                                        "type": "img", "res_uuid": img_uuid,
                                        "bbox": [0, 0, rect.width, rect.height]
                                    })
                            except Exception:
                                pass
                        details_list.append(page_details)
                return details_list, res_uuid_map

            DPFParser.extract_text_with_details = _fixed_extract

            pdf_obj = DPFParser()
            pdf_info_list, pfd_res_uuid_map = pdf_obj.extract_text_with_details(pdf_bytes)

            # 恢复原始方法
            DPFParser.extract_text_with_details = _orig_extract

            # 修复字体名称：将 PostScript 名映射为 OFD 查看器能识别的友好名称
            _font_name_map = {
                "FZXBSJW--GB1-0": "方正小标宋简体",
                "FangSong_GB2312": "仿宋_GB2312",
                "FangSong": "仿宋",
                "TimesNewRomanPSMT": "Times New Roman",
                "TimesNewRomanPS-BoldMT": "Times New Roman",
            }
            font_map = pfd_res_uuid_map.get("font", {})
            for uuid_key, ps_name in list(font_map.items()):
                if ps_name in _font_name_map:
                    font_map[uuid_key] = _font_name_map[ps_name]

            id_obj = CurId()

            # 修复 easyofd bug: OP=200/25.4 用于 200DPI 图片转 mm，
            # 但 PDF 文本坐标是 72DPI(points)，正确因子应为 72/25.4
            orig_op = self.OP
            self.OP = 72.0 / 25.4  # points → mm 的正确转换因子

            # 修复文本 bbox 高度不足：PDF bbox 是 tight bbox，需要扩展以容纳行距
            for page_blocks in pdf_info_list:
                for block in page_blocks:
                    if block.get("type") == "text":
                        bbox = block["bbox"]
                        font_size = block.get("size", 0)
                        extra = font_size * 0.4
                        block["bbox"] = (bbox[0], bbox[1] - extra * 0.3, bbox[2], bbox[3] + extra * 0.7)

            # page_size 在 build_content_res 中直接使用（不除以 OP），需要手动转为 mm
            page_sizes = pfd_res_uuid_map.get("other", {}).get("page_size", {})
            for pg_idx in page_sizes:
                page_sizes[pg_idx] = [page_sizes[pg_idx][0] / self.OP, page_sizes[pg_idx][1] / self.OP]
            if page_sizes:
                first_size = page_sizes[0]
                phys_box = f"0 0 {first_size[0]:.2f} {first_size[1]:.2f}"
            else:
                phys_box = "0 0 210 297"  # A4 mm fallback
            ofd_entrance = self.build_ofd_entrance(id_obj=id_obj)
            document = self.build_document(len(pdf_info_list), id_obj=id_obj, PhysicalBox=phys_box)
            public_res = self.build_public_res(id_obj=id_obj, pfd_res_uuid_map=pfd_res_uuid_map)
            document_res = self.build_document_res(len(pdf_info_list), id_obj=id_obj, pfd_res_uuid_map=pfd_res_uuid_map)
            content_res_list = _build_content_res_fixed(self, pdf_info_list=pdf_info_list, id_obj=id_obj,
                                                         pfd_res_uuid_map=pfd_res_uuid_map)
            self.OP = orig_op  # 恢复原始 OP

            res_static = {}
            img_dict = pfd_res_uuid_map.get("img")
            if img_dict:
                for key, v_io in img_dict.items():
                    res_static[f"Image_{key}.jpg"] = v_io.getvalue()
            from easyofd.draw.draw_ofd import OFDStructure
            ofd_byte = OFDStructure("123", ofd=ofd_entrance, document=document, public_res=public_res,
                                    document_res=document_res, content_res=content_res_list, res_static=res_static)(
                test=True)
            return ofd_byte
        return _orig_call(self, pdf_bytes, pil_img_list, optional_text)

    OFDWrite.__call__ = _patched_call

    # easyofd 会在当前目录创建临时 ./test 文件夹，需要在临时目录中执行
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="ofd_gen_")
    try:
        os.chdir(tmp_dir)
        ofd = OFD()
        ofd_bytes = ofd.pdf2ofd(pdf_bytes, optional_text=True)
        ofd.del_data()
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        OFDWrite.__call__ = _orig_call  # 恢复原始方法

    with open(output_path, "wb") as f:
        f.write(ofd_bytes)


if __name__ == "__main__":
    # 快速测试
    test_title = "李殿勋主持召开省政府常务会议研究推进我省美丽中国先行区建设等工作"
    test_content = (
        "3月30日，省委副书记、省长李殿勋主持召开省政府常务会议，传达学习习近平总书记近期重要讲话精神；"
        "研究推进我省美丽中国先行区建设、自然保护地整合优化等工作；听取全省安全生产和森林防灭火工作情况汇报。\n\n"
        "会议指出，要深入学习贯彻习近平生态文明思想，牢固树立绿水青山就是金山银山的理念，"
        "加快推进美丽中国先行区建设，为全国生态文明建设贡献湖北力量。"
    )

    os.makedirs("test_output", exist_ok=True)
    print("生成 DOCX...")
    generate_docx(test_title, test_content, "test_output/test.docx")
    print("生成 OFD...")
    generate_ofd(test_title, test_content, "test_output/test.ofd")
    print("完成！文件已生成到 test_output/")
