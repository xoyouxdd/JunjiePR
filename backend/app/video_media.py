"""Bounded FFmpeg subprocesses. Originals and masters have no public URL."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageStat

from app.v2_database import FILE_DIR

VIDEO_DIR = FILE_DIR / "learning-videos"
MAX_BYTES = 500 * 1024 * 1024
MAX_SECONDS = 1200
CHUNK_BYTES = 4 * 1024 * 1024
MIN_FREE_BYTES = 2 * 1024**3
STORAGE_LIMIT = 50 * 1024**3


def tool(name):
    configured = os.getenv("RECOGNITION_" + name.upper(), "")
    found = configured if configured and Path(configured).is_file() else shutil.which(name)
    if not found:
        raise RuntimeError("视频处理工具未配置，请联系管理员配置FFmpeg和FFprobe")
    return str(found)


def tools_ready():
    try:
        tool("ffmpeg"); tool("ffprobe")
        return True
    except RuntimeError:
        return False


def video_dir(video_id):
    return VIDEO_DIR / "videos" / str(int(video_id))


def ensure_space(required=0):
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in VIDEO_DIR.rglob("*") if p.is_file())
    if used + required > STORAGE_LIMIT or shutil.disk_usage(VIDEO_DIR).free < MIN_FREE_BYTES + required:
        raise RuntimeError("视频存储空间不足，请联系管理员清理过期缓存或扩容")


def run(args, *, cwd=None, timeout=1800, stop=None):
    # No shell and no user-controlled command/filter options. Two codec threads
    # keep the existing single-instance business service responsive.
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0
    with subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=flags) as process:
        started = time.monotonic()
        while True:
            try:
                stdout, stderr = process.communicate(timeout=.5)
                break
            except subprocess.TimeoutExpired:
                if (stop and stop.is_set()) or time.monotonic() - started > timeout:
                    process.kill(); process.communicate()
                    raise RuntimeError("视频处理已中止或超时，请稍后重试")
        if process.returncode:
            # Do not expose local paths or FFmpeg's input metadata to clients.
            raise RuntimeError("视频无法解码或处理，请确认原片可正常播放后重试")
        return stdout


def probe(path):
    data = json.loads(run([tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
                           "-format_whitelist", "mov", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30))
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not streams:
        raise RuntimeError("文件中没有可播放的视频画面")
    stream = streams[0]
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    rotation = stream.get('tags', {}).get('rotate', 0)
    for side in stream.get('side_data_list', []):
        if 'rotation' in side:
            rotation = side['rotation']
    if round(float(rotation)) % 180 == 90:
        width, height = height, width
    duration = float(data.get("format", {}).get("duration", 0))
    if not (0 < duration <= MAX_SECONDS) or not (0 < width * height <= 9_000_000):
        raise RuntimeError("视频须在20分钟以内，画面不超过4K像素范围")
    rate = stream.get("avg_frame_rate", "30/1").split("/")
    fps = float(rate[0]) / max(1, float(rate[-1]))
    return {"duration": max(1, round(duration)), "width": width, "height": height, "fps": min(60, max(1, fps))}


def ffmpeg_base(source):
    return [tool("ffmpeg"), "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]


def codec_args():
    return ["-c:v", "libx264", "-threads", "2", "-filter_threads", "1", "-preset", "fast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-map_metadata", "-1", "-movflags", "+faststart"]


def font_path():
    candidates = [os.getenv("RECOGNITION_VIDEO_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
                  "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    return next((Path(p) for p in candidates if p and Path(p).is_file()), None)


def build_cover(video_id, title, second, stop=None):
    directory = video_dir(video_id)
    frame = directory / f"frame-{uuid4().hex}.jpg"
    run([tool("ffmpeg"), "-nostdin", "-y", "-v", "error", "-ss", str(second),
         "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov", "-i", str(directory / "source.mov"),
         "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
         "-threads", "2", str(frame)], timeout=45, stop=stop)
    with Image.open(frame) as opened:
        image = Image.new("RGB", (1280, 720), (16, 35, 55))
        opened.thumbnail((1280, 720))
        image.paste(opened, ((1280-opened.width)//2, (720-opened.height)//2))
    layer = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(layer)
    for y in range(720):
        draw.line((0, y, 1280, y), fill=(9, 24, 41, int(205 * (y/720)**2)))
    image = Image.alpha_composite(image.convert("RGBA"), layer)
    draw = ImageDraw.Draw(image)
    path = font_path()
    font = ImageFont.truetype(str(path), 49) if path else ImageFont.load_default(size=38)
    small = ImageFont.truetype(str(path), 24) if path else ImageFont.load_default(size=24)
    lines, line = [], ""
    for char in title:
        if draw.textlength(line + char, font=font) > 1140:
            lines.append(line); line = char
        else:
            line += char
    if line:
        lines.append(line)
    # cover_title has a 40-character bound; two lines remain readable.
    for index, text in enumerate(lines[:2]):
        draw.text((60, 480 + index*68), text, font=font, fill="white")
    draw.text((60, 645), "FZ工作台 · 内部学习", font=small, fill=(210, 229, 242))
    temp = directory / "cover.new.jpg"
    image.convert("RGB").save(temp, "JPEG", quality=87, optimize=True)
    temp.replace(directory / "cover.jpg")
    frame.unlink(missing_ok=True)


def process_source(video, stop=None):
    directory = video_dir(video.id)
    info = probe(directory / "source.mov")
    ensure_space(video.original_bytes * 2)
    profiles = {}
    for quality in (720, 1080):
        long_edge = round(quality*16/9)
        width, height = ((long_edge, quality) if info["width"] >= info["height"] else (quality, long_edge))
        output = directory / f"master-{quality}.mp4"
        temp = directory / f"master-{quality}.{uuid4().hex}.new.mp4"
        scale = f"scale=w='min(iw,{width})':h='min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps={info['fps']}"
        run(ffmpeg_base(directory / "source.mov") + ["-vf", scale, "-af", "loudnorm=I=-18:TP=-2:LRA=11"] + codec_args() + [str(temp)], stop=stop)
        output_info = probe(temp)
        if abs(output_info["duration"]-info["duration"]) > 2:
            raise RuntimeError("处理后视频时长异常，请重新处理")
        temp.replace(output)
        profiles[str(quality)] = {"bytes": output.stat().st_size, "width": output_info["width"], "height": output_info["height"]}
    # Try a few real frames; avoid obvious black opening frames.
    candidates = [min(info['duration']*.1, 5), min(info['duration']*.35, 30), info['duration']*.6]
    chosen = candidates[0]
    for second in candidates:
        build_cover(video.id, video.cover_title, second, stop)
        with Image.open(directory / "cover.jpg") as image:
            if sum(ImageStat.Stat(image.crop((0, 0, 1280, 400))).mean)/3 > 24:
                chosen = second; break
    return info, profiles, int(chosen)


def playback_key(video, employee_no, quality):
    return hashlib.sha256(f"{video.id}:{video.version}:{employee_no}:{quality}".encode()).hexdigest()


def playback_path(key):
    return VIDEO_DIR / "playbacks" / key / "video.mp4"


def process_playback(video, payload, stop=None):
    quality, employee_no = str(payload["quality"]), str(payload["employee_no"])
    key = playback_key(video, employee_no, quality)
    path = playback_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_space((video_dir(video.id) / f"master-{quality}.mp4").stat().st_size * 2)
    (path.parent / "watermark.txt").write_text(f"内部学习 · {employee_no} · 请勿外传", encoding="utf-8")
    font = font_path()
    if not font:
        raise RuntimeError("视频水印字体未配置")
    # Fixed relative filenames keep employee numbers and paths out of filter syntax.
    shutil.copyfile(font, path.parent / "font.ttf")
    filters = ("drawtext=fontfile=font.ttf:textfile=watermark.txt:expansion=none:"
               "fontsize='max(14,w/30)':fontcolor=white@0.47:borderw=1:bordercolor=black@0.35:"
               "x='(w-tw)*(0.08+0.48*mod(floor(t/9),2))':y=h*0.26,"
               "drawtext=fontfile=font.ttf:textfile=watermark.txt:expansion=none:"
               "fontsize='max(13,w/32)':fontcolor=white@0.45:borderw=1:bordercolor=black@0.35:"
               "x='(w-tw)*(0.55-0.45*mod(floor(t/9),2))':y=h*0.72")
    source = (video_dir(video.id) / f"master-{quality}.mp4").resolve()
    temp = path.parent / f"video.{uuid4().hex}.new.mp4"
    run(ffmpeg_base(source) + ["-vf", filters] + codec_args() + [temp.name], cwd=path.parent, stop=stop)
    probe(temp)
    temp.replace(path)
    (path.parent / "font.ttf").unlink(missing_ok=True)
