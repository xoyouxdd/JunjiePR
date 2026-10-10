"""Deterministic, local poster composition. No model, account or network calls."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

TEMPLATE_DIR = Path(__file__).parent / "static/images/announcement-templates"
ILLUSTRATED_TEMPLATES = [
    {"id": "safety-rain", "name": "雨天守护", "category": "安全 & 合规"},
    {"id": "safety-shield", "name": "合规守护", "category": "安全 & 合规"},
    {"id": "show-stage", "name": "舞台就绪", "category": "演出 & 5S"},
    {"id": "efficiency-plan", "name": "有序协作", "category": "排班 & 效率"},
    {"id": "service-welcome", "name": "暖心迎宾", "category": "礼仪 & MM"},
    {"id": "hr-festival", "name": "活力相聚", "category": "HR & 活动"},
    {"id": "hr-growth", "name": "一起成长", "category": "HR & 活动"},
    {"id": "universal-garden", "name": "园区晨光", "category": "通用"},
]


def template_options():
    return [{**item, "preview_url": f"/static/images/announcement-templates/{item['id']}.png"}
            for item in ILLUSTRATED_TEMPLATES]


PALETTES = {
    "安全 & 合规": ("#173f52", "#326c78", "#e5f1ed", "#a6d7c6"),
    "演出 & 5S": ("#3c355b", "#776a9a", "#f0edf6", "#d5c5ed"),
    "排班 & 效率": ("#193c67", "#406eb0", "#eaf1fb", "#b7d4f5"),
    "礼仪 & MM": ("#1e5048", "#4a8171", "#edf5ef", "#b3d8c5"),
    "HR & 活动": ("#803c28", "#bc6442", "#fbf0e5", "#f4c78d"),
}


class PosterError(ValueError):
    pass


def poster_font(size: int):
    configured = os.environ.get("ANNOUNCEMENT_POSTER_FONT", "")
    if configured:
        candidates = [Path(configured)]
    else:
        candidates = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
                      Path("C:/Windows/Fonts/simhei.ttf"),
                      Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                      Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")]
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    raise PosterError("服务器缺少可用中文字体，请配置ANNOUNCEMENT_POSTER_FONT后重试")


def wrap_text(draw, value, font, width):
    lines = []
    for paragraph in value.splitlines() or [""]:
        line = ""
        for char in paragraph:
            if line and draw.textlength(line + char, font=font) > width:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
    return lines


def fitted_text(draw, value, width, height, start, minimum):
    for size in range(start, minimum - 1, -2):
        font = poster_font(size)
        lines = wrap_text(draw, value, font, width)
        spacing = round(size * 1.4)
        if len(lines) * spacing <= height:
            return font, lines, spacing
    raise PosterError("文字过长，当前版式放不下，请精简标题或重点")


def render_poster(*, title: str, summary: str, category: str, layout="portrait",
                  style="notice", scope="", effective_date="", template_id="") -> bytes:
    title, summary, scope = title.strip(), summary.strip(), scope.strip()
    if not title or not summary:
        raise PosterError("请填写标题和重点内容")
    if category not in PALETTES or layout not in {"portrait", "landscape"} or style not in {"notice", "event", "guide"}:
        raise PosterError("请选择有效的板块、尺寸和版式")
    if template_id:
        if template_id not in {item['id'] for item in ILLUSTRATED_TEMPLATES}:
            raise PosterError("请选择有效的插画模板")
        return render_illustrated(title, summary, category, layout, scope, effective_date, template_id)
    width, height = (1080, 1440) if layout == "portrait" else (1280, 720)
    ink, accent, paper, highlight = PALETTES[category]
    image = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(image)
    margin = 76 if layout == "portrait" else 60
    header_h = 720 if layout == "portrait" else 360
    title_y = 215 if layout == "portrait" else 125
    title_h = 320 if layout == "portrait" else 174

    if style == "notice":
        draw.rectangle((0, 0, width, header_h), fill=ink)
        draw.ellipse((width-300, -200, width+500, 600), outline=accent, width=64)
        draw.ellipse((width-170, -60, width+370, 480), outline=accent, width=3)
        draw.rounded_rectangle((margin, header_h-94, margin+110, header_h-84), radius=4, fill=highlight)
    elif style == "event":
        draw.rectangle((0, 0, width, header_h), fill=ink)
        draw.ellipse((width-230, header_h-260, width+130, header_h+100), fill=highlight)
        draw.ellipse((width-125, header_h-155, width+30, header_h), fill=accent)
        for x, y, r in [(width-130, 50, 22), (width-290, 110, 13), (width-60, 220, 12)]:
            draw.ellipse((x-r,y-r,x+r,y+r), fill=highlight)
        draw.polygon([(margin,header_h-76),(margin+60,header_h-116),(margin+76,header_h-52)],fill=highlight)
    else:
        # An editorial title area with quiet ruled structure.
        draw.rectangle((0, 0, 22, height), fill=accent)
        draw.line((margin, 90, width-margin, 90), fill=accent, width=2)
        draw.rectangle((width-margin-135, 110, width-margin, 122), fill=accent)
        title_y = 205 if layout == "portrait" else 134
        draw.line((margin, header_h-24, width-margin, header_h-24), fill=accent, width=2)

    title_color = paper if style != "guide" else ink
    label_color = highlight if style != "guide" else accent
    label_font = poster_font(28 if layout == "portrait" else 23)
    label_y = 110 if layout == "portrait" else 46
    draw.text((margin, label_y), category, font=label_font, fill=label_color)
    # Leave the right-side decorative area clear rather than laying text on it.
    title_width = width-2*margin-(80 if style == "event" else 0)
    font, lines, step = fitted_text(draw, title, title_width, title_h,
                                    82 if layout == "portrait" else 62, 42 if layout == "portrait" else 32)
    for i, line in enumerate(lines):
        draw.text((margin, title_y+i*step), line, font=font, fill=title_color)

    content_y = header_h+72 if layout == "portrait" else header_h+35
    draw.text((margin, content_y), "公告重点", font=label_font, fill=accent)
    summary_y = content_y+65 if layout == "portrait" else content_y+43
    bottom_y = height-(170 if layout == "portrait" else 92)
    font, lines, step = fitted_text(draw, summary, width-2*margin, bottom_y-summary_y-24,
                                    38 if layout == "portrait" else 28, 26 if layout == "portrait" else 22)
    for i, line in enumerate(lines):
        draw.text((margin, summary_y+i*step), line, font=font, fill=ink)

    draw.line((margin,bottom_y,width-margin,bottom_y),fill=accent,width=2)
    footer = (f"适用范围：{scope}" if scope else "")
    if effective_date:
        footer += ("\n" if footer else "") + f"生效日期：{effective_date}"
    if footer:
        footer_font, footer_lines, footer_step = fitted_text(draw,footer,width-2*margin,
                                                            height-bottom_y-28,24,18)
        for i,line in enumerate(footer_lines):
            draw.text((margin,bottom_y+20+i*footer_step),line,font=footer_font,fill=accent)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_illustrated(title, summary, category, layout, scope, effective_date, template_id):
    """Keep artwork and official text in separate panels; never crop the source art."""
    width, height = (1080, 1440) if layout == "portrait" else (1280, 720)
    ink, accent, paper, highlight = PALETTES[category]
    image = Image.new("RGB", (width, height), paper)
    try:
        with Image.open(TEMPLATE_DIR / f"{template_id}.png") as source:
            art = source.convert("RGB")
    except (OSError, ValueError) as exc:
        raise PosterError("插画模板暂时不可用，请选择其他模板") from exc
    portrait = layout == "portrait"
    panel = (width, 650) if portrait else (620, height)
    art = ImageOps.contain(art, panel, Image.Resampling.LANCZOS)
    art_x = (width-art.width)//2 if portrait else width-panel[0]+(panel[0]-art.width)//2
    art_y = (panel[1]-art.height)//2
    image.paste(art, (art_x, art_y))
    draw = ImageDraw.Draw(image)
    x, y, text_width = (64, 690, 952) if portrait else (48, 48, 544)
    draw.text((x, y), category, font=poster_font(27 if portrait else 23), fill=accent)
    title_y, title_h = y+56, 188 if portrait else 174
    font, lines, step = fitted_text(draw, title, text_width, title_h, 66 if portrait else 48, 36 if portrait else 30)
    for i, line in enumerate(lines):
        draw.text((x, title_y+i*step), line, font=font, fill=ink)
    summary_y = title_y+title_h+24
    footer_y = height-(125 if portrait else 112)
    font, lines, step = fitted_text(draw, summary, text_width, footer_y-summary_y-28,
                                    34 if portrait else 27, 26 if portrait else 22)
    for i, line in enumerate(lines):
        draw.text((x, summary_y+i*step), line, font=font, fill=ink)
    draw.line((x, footer_y, x+text_width, footer_y), fill=accent, width=2)
    footer = (f"适用范围：{scope}" if scope else "")
    if effective_date:
        footer += ("\n" if footer else "")+f"生效日期：{effective_date}"
    if footer:
        font, lines, step = fitted_text(draw, footer, text_width, height-footer_y-22, 22, 18)
        for i, line in enumerate(lines):
            draw.text((x, footer_y+16+i*step), line, font=font, fill=accent)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
