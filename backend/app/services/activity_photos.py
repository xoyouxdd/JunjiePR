"""Normalize uploaded prize photos, with bounded decoding and no retained EXIF."""
from io import BytesIO
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException

MAX_UPLOAD = 20 * 1024 * 1024
MAX_BYTES = 500 * 1024
MAX_SIDE = 1600
MAX_PIXELS = 50_000_000


def normalize_photo(data):
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, '图片为空或超过20MB，请选择较小的照片')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise HTTPException(422, '请使用JPG、PNG或WebP照片')
                if source.width * source.height > MAX_PIXELS:
                    raise HTTPException(413, '图片像素过大，请先缩小后上传')
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                rgba = image.convert('RGBA')
                clean = Image.new('RGB', rgba.size, 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
                for _ in range(8):
                    for quality in (86, 76, 66, 56, 46):
                        output = BytesIO()
                        clean.save(output, format='JPEG', quality=quality, optimize=True)
                        if output.tell() <= MAX_BYTES:
                            return output.getvalue(), clean.width, clean.height
                    clean = clean.resize((max(1, int(clean.width*.8)), max(1, int(clean.height*.8))), Image.Resampling.LANCZOS)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, '无法读取这张图片，请重新拍照或选择JPG、PNG、WebP图片')
    raise HTTPException(422, '图片压缩失败，请选择其他照片')
