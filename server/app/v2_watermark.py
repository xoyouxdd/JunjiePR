from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

import fitz
from openpyxl.drawing.image import Image as WorksheetImage
from PIL import Image, ImageDraw, ImageFont
from pillow_heif import register_heif_opener


register_heif_opener()


def watermark_label(employee_no: str, timestamp: datetime | None = None) -> str:
    current = timestamp or datetime.now()
    return f"CONFIDENTIAL | {employee_no} | {current:%Y-%m-%d %H:%M}"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _watermark_tile(label: str, width: int = 980, height: int = 520) -> bytes:
    tile = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    text_layer = Image.new("RGBA", tile.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)
    font = _font(28)
    for y in range(45, height, 135):
        for x in range(-80, width, 430):
            draw.text((x, y), label, font=font, fill=(90, 105, 125, 30))
    text_layer = text_layer.rotate(28, resample=Image.Resampling.BICUBIC, expand=False)
    tile.alpha_composite(text_layer)
    output = BytesIO()
    tile.save(output, format="PNG")
    return output.getvalue()


def watermark_workbook(workbook, employee_no: str, timestamp: datetime | None = None) -> None:
    """Apply export attribution without restricting workbook editing.

    Exported workbooks retain the visible account watermark and print
    attribution, but deliberately do not enable worksheet or object
    protection.  Recipients can edit cells, insert/delete rows or columns,
    manage filters, and change/remove drawing objects in their own copy.
    """
    label = watermark_label(employee_no, timestamp)
    workbook.properties.creator = label
    workbook.properties.lastModifiedBy = label
    workbook.properties.description = f"受控数据导出；账号水印：{employee_no}"
    tile = _watermark_tile(label)
    for worksheet in workbook.worksheets:
        worksheet.oddHeader.center.text = label
        worksheet.oddHeader.center.size = 10
        worksheet.oddHeader.center.color = "B7BEC8"
        worksheet.oddFooter.center.text = label
        worksheet.oddFooter.center.size = 9
        worksheet.oddFooter.center.color = "B7BEC8"
        image = WorksheetImage(BytesIO(tile))
        image.width = 735
        image.height = 390
        worksheet.add_image(image, "A1")


def watermark_image(
    source: Path,
    employee_no: str,
    timestamp: datetime | None = None,
    *,
    max_dimension: int | None = None,
) -> tuple[bytes, str]:
    label = watermark_label(employee_no, timestamp)
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    if max_dimension and max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    font_size = max(14, min(42, image.width // 28))
    font = _font(font_size)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_width = max(1, bbox[2] - bbox[0])
    step_x = text_width + max(70, image.width // 12)
    step_y = max(90, font_size * 5)
    for y in range(-step_y, image.height + step_y, step_y):
        for x in range(-step_x, image.width + step_x, step_x):
            draw.text((x, y), label, font=font, fill=(235, 235, 235, 68), stroke_width=1, stroke_fill=(70, 70, 70, 32))
    overlay = overlay.rotate(24, resample=Image.Resampling.BICUBIC, expand=False)
    image = Image.alpha_composite(image, overlay)

    suffix = source.suffix.lower()
    output = BytesIO()
    if suffix in {".jpg", ".jpeg"}:
        image.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue(), "image/jpeg"
    if suffix == ".webp":
        image.save(output, format="WEBP", quality=92)
        return output.getvalue(), "image/webp"
    if suffix in {".heic", ".heif"} and max_dimension:
        image.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue(), "image/jpeg"
    if suffix in {".heic", ".heif"}:
        image.convert("RGB").save(output, format="HEIF", quality=92)
        return output.getvalue(), "image/heic"
    image.save(output, format="PNG")
    return output.getvalue(), "image/png"


def watermark_pdf(source: Path, employee_no: str, timestamp: datetime | None = None) -> bytes:
    label = watermark_label(employee_no, timestamp)
    document = fitz.open(source)
    try:
        for page in document:
            page_width = page.rect.width
            page_height = page.rect.height
            for y in range(45, int(page_height), 95):
                for x in range(20, int(page_width), 310):
                    page.insert_text(
                        (x, y),
                        label,
                        fontsize=8,
                        fontname="helv",
                        color=(0.58, 0.61, 0.66),
                        fill_opacity=0.18,
                        overlay=True,
                    )
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
