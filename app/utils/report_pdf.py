from __future__ import annotations

import html
import re
from pathlib import Path


def _load_reportlab() -> dict[str, object]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 reportlab 依赖，请先安装 requirements.txt 中的新依赖。") from exc

    return {
        "colors": colors,
        "TA_LEFT": TA_LEFT,
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "StyleSheet1": StyleSheet1,
        "getSampleStyleSheet": getSampleStyleSheet,
        "mm": mm,
        "pdfmetrics": pdfmetrics,
        "UnicodeCIDFont": UnicodeCIDFont,
        "TTFont": TTFont,
        "HRFlowable": HRFlowable,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
    }


def _register_fonts(reportlab: dict[str, object]) -> tuple[str, str]:
    pdfmetrics = reportlab["pdfmetrics"]
    UnicodeCIDFont = reportlab["UnicodeCIDFont"]
    TTFont = reportlab["TTFont"]

    body_font = "ReportBodyFont"
    bold_font = "ReportBoldFont"

    try:
        pdfmetrics.getFont(body_font)
    except Exception:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        pdfmetrics.registerFontFamily(body_font, normal="STSong-Light", bold="STSong-Light")
        body_font = "STSong-Light"

    bold_font_path = Path("C:/Windows/Fonts/simhei.ttf")
    if bold_font_path.exists():
        try:
            pdfmetrics.getFont(bold_font)
        except Exception:
            pdfmetrics.registerFont(TTFont(bold_font, str(bold_font_path)))
    else:
        bold_font = body_font
    return body_font, bold_font


def _build_styles(reportlab: dict[str, object], body_font: str, bold_font: str) -> dict[str, object]:
    colors = reportlab["colors"]
    ParagraphStyle = reportlab["ParagraphStyle"]
    getSampleStyleSheet = reportlab["getSampleStyleSheet"]

    styles = getSampleStyleSheet()
    out: dict[str, object] = {}
    out["body"] = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=body_font,
        fontSize=11.5,
        leading=18,
        textColor=colors.HexColor("#1b1f27"),
        spaceAfter=8,
        wordWrap="CJK",
    )
    out["quote"] = ParagraphStyle(
        "ReportQuote",
        parent=out["body"],
        backColor=colors.HexColor("#f3f6fb"),
        borderColor=colors.HexColor("#d8e0ef"),
        borderWidth=0.8,
        borderPadding=8,
        leftIndent=10,
        rightIndent=4,
        textColor=colors.HexColor("#253246"),
        spaceAfter=10,
    )
    out["h1"] = ParagraphStyle(
        "ReportH1",
        parent=out["body"],
        fontName=bold_font,
        fontSize=21,
        leading=28,
        textColor=colors.HexColor("#111827"),
        spaceBefore=10,
        spaceAfter=14,
    )
    out["h2"] = ParagraphStyle(
        "ReportH2",
        parent=out["body"],
        fontName=bold_font,
        fontSize=16,
        leading=22,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=10,
        spaceAfter=10,
    )
    out["h3"] = ParagraphStyle(
        "ReportH3",
        parent=out["body"],
        fontName=bold_font,
        fontSize=13.5,
        leading=19,
        textColor=colors.HexColor("#1f2937"),
        spaceBefore=8,
        spaceAfter=8,
    )
    out["h4"] = ParagraphStyle(
        "ReportH4",
        parent=out["body"],
        fontName=bold_font,
        fontSize=12,
        leading=17,
        textColor=colors.HexColor("#334155"),
        spaceBefore=6,
        spaceAfter=6,
    )
    out["meta"] = ParagraphStyle(
        "ReportMeta",
        parent=out["body"],
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#5b6472"),
        spaceAfter=4,
    )
    return out


def _inline_markup(text: str, bold_font: str) -> str:
    escaped = html.escape(text)
    pattern = re.compile(r"\*\*(.+?)\*\*")

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        return f'<font name="{bold_font}">{inner}</font>'

    return pattern.sub(repl, escaped)


def markdown_to_pdf(markdown_text: str, output_path: Path, *, title: str, source_filename: str) -> None:
    reportlab = _load_reportlab()
    colors = reportlab["colors"]
    A4 = reportlab["A4"]
    mm = reportlab["mm"]
    HRFlowable = reportlab["HRFlowable"]
    Paragraph = reportlab["Paragraph"]
    SimpleDocTemplate = reportlab["SimpleDocTemplate"]
    Spacer = reportlab["Spacer"]

    body_font, bold_font = _register_fonts(reportlab)
    styles = _build_styles(reportlab, body_font, bold_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Codex Report Explain",
    )

    story: list[object] = []
    story.append(Paragraph(_inline_markup(title, bold_font), styles["h1"]))
    story.append(Paragraph(_inline_markup(f"来源文件：{source_filename}", bold_font), styles["meta"]))
    story.append(Spacer(1, 6))

    paragraph_buffer: list[str] = []
    lines = markdown_text.splitlines()

    def flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        text = " ".join(x.strip() for x in paragraph_buffer if x.strip())
        paragraph_buffer.clear()
        if not text:
            return
        story.append(Paragraph(_inline_markup(text, bold_font), styles["body"]))

    idx = 0
    while idx < len(lines):
        raw = lines[idx].rstrip()
        stripped = raw.strip()
        if not stripped:
            flush_paragraph()
            story.append(Spacer(1, 4))
            idx += 1
            continue

        if stripped == "---":
            flush_paragraph()
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.7,
                    color=colors.HexColor("#cdd8ea"),
                    spaceBefore=4,
                    spaceAfter=10,
                )
            )
            idx += 1
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            style_key = {1: "h1", 2: "h2", 3: "h3", 4: "h4"}[level]
            story.append(Paragraph(_inline_markup(text, bold_font), styles[style_key]))
            idx += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while idx < len(lines):
                quote_raw = lines[idx].strip()
                if not quote_raw.startswith(">"):
                    break
                quote_lines.append(quote_raw[1:].strip())
                idx += 1
            quote_html = "<br/>".join(_inline_markup(line, bold_font) for line in quote_lines if line)
            story.append(Paragraph(quote_html, styles["quote"]))
            continue

        paragraph_buffer.append(stripped)
        idx += 1

    flush_paragraph()
    doc.build(story)
