"""跨平台媒体工具：ffprobe 探测、封面、合成素材、场景切点、字幕 PNG。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import platform_support

ROOT = Path(__file__).resolve().parent.parent
MICROS = 1_000_000
STILL_CAPACITY_US = 10_800_000_000
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.webp', '.bmp', '.tif', '.tiff'}
GIF_EXT = {'.gif'}


def _tool(name, explicit=None):
    env = os.environ.get('JIANYING_' + name.upper())
    candidates = [explicit, env, ROOT / 'bin' / name, ROOT / 'bin' / (name + '.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise RuntimeError('需要 %s；请安装 ffmpeg 或把 %s 放到 bin/ 目录' % (name, name))


def ffmpeg(explicit=None):
    return _tool('ffmpeg', explicit)


def ffprobe(explicit=None):
    return _tool('ffprobe', explicit)


def version(executable):
    result = subprocess.run([str(executable), '-version'], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0].strip() if result.returncode == 0 else 'unknown'


def run(executable, args, timeout=300):
    return subprocess.run([str(executable)] + [str(item) for item in args],
                          capture_output=True, text=True, timeout=timeout)


def _fraction(value):
    if not value or value == '0/0':
        return 0.0
    left, _, right = str(value).partition('/')
    return float(left) / float(right) if right else float(left)


def probe(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError('素材必须是绝对路径的普通文件: ' + str(path))
    result = run(ffprobe(), ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)])
    if result.returncode != 0:
        raise ValueError('无法读取素材: %s\n%s' % (path, result.stderr[-1000:]))
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    ext = path.suffix.lower()
    codec = ''
    width = height = 0
    rotation = 0.0
    pix_fmt = ''
    duration = 0.0
    if videos:
        stream = videos[0]
        codec = stream.get('codec_name', '')
        width = int(stream.get('width', 0))
        height = int(stream.get('height', 0))
        pix_fmt = stream.get('pix_fmt', '')
        duration = float(stream.get('duration') or data.get('format', {}).get('duration') or 0)
        rotation = float(stream.get('tags', {}).get('rotate', 0) or 0)
        for side in stream.get('side_data_list', []):
            if 'rotation' in side:
                rotation = float(side['rotation'])
    elif audios:
        duration = float(audios[0].get('duration') or data.get('format', {}).get('duration') or 0)
    else:
        raise ValueError('没有可用的音视频流: ' + str(path))
    if ext in IMAGE_EXT or (codec in {'png', 'mjpeg', 'bmp', 'tiff'} and duration <= 0):
        media_type = 'photo'
    elif ext in GIF_EXT or codec == 'gif':
        media_type = 'gif'
    elif videos:
        media_type = 'video'
    else:
        media_type = 'audio'
    if media_type == 'video' and codec not in {'h264', 'hevc'}:
        raise ValueError('仅支持 H.264 / HEVC 视频: %s (%s)' % (path, codec))
    if media_type == 'video' and abs(rotation) > 0.01:
        raise ValueError('素材带旋转元数据，需要先归一化: ' + str(path))
    duration_us = STILL_CAPACITY_US if media_type == 'photo' else int(round(duration * MICROS))
    if duration_us <= 0:
        raise ValueError('无法读取素材时长: ' + str(path))
    return {
        'source': str(path),
        'sha256': _digest(path),
        'size': path.stat().st_size,
        'duration_us': duration_us,
        'decoded_duration_us': 0 if media_type == 'photo' else duration_us,
        'kind': 'audio' if media_type == 'audio' else 'video',
        'media_type': media_type,
        'width': width,
        'height': height,
        'codec': codec,
        'pix_fmt': pix_fmt,
        'fps': _fraction(videos[0].get('avg_frame_rate')) if videos else 0.0,
        'rotation': rotation,
        'has_video': bool(videos),
        'has_audio': bool(audios),
    }


def _digest(path):
    import hashlib
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def make_cover(input_path, output):
    output = Path(output)
    result = run(ffmpeg(), ['-y', '-v', 'error', '-ss', '0.5', '-i', str(input_path),
                            '-frames:v', '1', '-vf', 'scale=320:-2', str(output)])
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        return make_blank_cover(output)
    return str(output)


def make_blank_cover(output):
    output = Path(output)
    result = run(ffmpeg(), ['-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180',
                            '-frames:v', '1', str(output)])
    if result.returncode != 0:
        raise RuntimeError('封面生成失败: ' + result.stderr[-1000:])
    return str(output)


def sample(output, seconds=3):
    output = Path(output)
    result = run(ffmpeg(), ['-y', '-v', 'error',
                            '-f', 'lavfi', '-i', 'color=c=0x304060:s=1280x720:r=30:d=%s' % seconds,
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=%s' % seconds,
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(output)],
                 timeout=300)
    if result.returncode != 0:
        raise RuntimeError('合成素材失败: ' + result.stderr[-1500:])
    return str(output)


def scene_cuts(path, threshold=0.32, limit=24):
    result = run(ffmpeg(), ['-hide_banner', '-v', 'info', '-i', str(path),
                            '-filter:v', "select='gt(scene,%.2f)',metadata=print" % threshold,
                            '-an', '-f', 'null', '-'], timeout=300)
    cuts = [0.0]
    for line in result.stderr.splitlines():
        match = re.search(r'pts_time:([0-9.]+)', line)
        if match:
            value = float(match.group(1))
            if not cuts or value - cuts[-1] > 0.4:
                cuts.append(value)
        if len(cuts) >= limit:
            break
    return cuts


def _rgb(value):
    value = value.lstrip('#')
    if len(value) != 6:
        return (255, 255, 255)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def default_font():
    if platform_support.IS_MAC:
        candidates = ['/System/Library/Fonts/PingFang.ttc',
                      '/System/Library/Fonts/Hiragino Sans GB.ttc',
                      '/System/Library/Fonts/STHeiti Medium.ttc',
                      '/Library/Fonts/Arial Unicode.ttf']
    elif platform_support.IS_WIN:
        candidates = [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc',
                      r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\arial.ttf']
    else:
        candidates = []
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def text_image(text, output, size, fill, border, border_width, font=None):
    from PIL import Image, ImageDraw, ImageFont

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    font_path = font or default_font()
    try:
        typeface = ImageFont.truetype(font_path, int(size)) if font_path else ImageFont.load_default()
    except OSError:
        typeface = ImageFont.load_default()
    stroke = max(0, int(round(border_width)))
    padding = stroke + 6
    probe_image = Image.new('RGBA', (1, 1))
    draw = ImageDraw.Draw(probe_image)
    box = draw.textbbox((0, 0), text, font=typeface, stroke_width=stroke)
    width = max(1, box[2] - box[0] + padding * 2)
    height = max(1, box[3] - box[1] + padding * 2)
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((padding - box[0], padding - box[1]), text, font=typeface,
              fill=_rgb(fill) + (255,), stroke_width=stroke,
              stroke_fill=_rgb(border) + (255,))
    image.save(output, 'PNG')
    return {'path': str(output), 'width': width, 'height': height}
