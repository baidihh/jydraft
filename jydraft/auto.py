"""一句话自动剪辑：扫描素材文件夹，按指令选段、排轨、生成草稿并导出。

这是规则驱动的粗剪器，不是大模型创作：
- 从指令里解析时长、节奏、横竖屏、静音、字幕等意图；
- 用 ffmpeg 场景检测找镜头切换点，按节奏抽取片段；
- 主轨硬切拼接，图片插入，SRT 字幕自动上轨，BGM 自动垫底；
- 输出仍然是可继续编辑的剪映原生草稿，并可选择 FFmpeg 导出。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from . import draft, export as export_module, media, runtime

MICROS = 1_000_000
VIDEO = {'.mp4', '.mov', '.m4v', '.mkv', '.avi', '.webm'}
IMAGE = {'.jpg', '.jpeg', '.png', '.heic', '.webp'}
AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.flac'}
SUBTITLE = {'.srt'}


def scene_cuts(path, threshold=0.32, limit=24):
    return media.scene_cuts(path, threshold=threshold, limit=limit)


def parse_brief(brief, total_hint=None):
    text = (brief or '').lower()
    duration = 30.0
    match = re.search(r'(\d+(?:\.\d+)?)\s*(秒|s\b|sec)', text)
    if match:
        duration = float(match.group(1))
    else:
        match = re.search(r'(\d+(?:\.\d+)?)\s*(分钟|min)', text)
        if match:
            duration = float(match.group(1)) * 60
    if total_hint:
        duration = min(duration, total_hint)
    pace = 3.0
    if any(word in text for word in ('快', '卡点', '节奏', '燃', 'short')):
        pace = 2.0
    if any(word in text for word in ('慢', 'vlog', '治愈', '舒缓')):
        pace = 4.5
    vertical = any(word in text for word in ('竖屏', '纵向', '9:16', '抖音', '快手', '小红书'))
    horizontal = any(word in text for word in ('横屏', '横向', '16:9', 'b站', 'bilibili', 'youtube'))
    mute = any(word in text for word in ('静音', '无声', '不要原声'))
    want_captions = any(word in text for word in ('字幕', 'caption', 'srt'))
    want_bgm = any(word in text for word in ('配乐', 'bgm', '音乐', '背景乐'))
    return {
        'duration_us': int(duration * MICROS),
        'pace_us': int(pace * MICROS),
        'vertical': vertical and not horizontal,
        'horizontal': horizontal,
        'mute': mute,
        'captions': want_captions,
        'bgm': want_bgm,
    }


def scan(input_dir):
    root = Path(input_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('素材目录不存在: ' + str(root))
    videos, images, audios, subtitles = [], [], [], []
    for path in sorted(root.rglob('*')):
        if path.name.startswith('.') or not path.is_file() or path.is_symlink():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix in VIDEO:
                info = runtime.probe(path)
                info['cuts'] = scene_cuts(path) if info['duration_us'] > 4 * MICROS else [0.0]
                videos.append(info)
            elif suffix in IMAGE:
                info = runtime.probe(path)
                images.append(info)
            elif suffix in AUDIO:
                info = runtime.probe(path)
                audios.append(info)
            elif suffix in SUBTITLE:
                subtitles.append(path)
        except (ValueError, RuntimeError):
            continue
    if not videos and not images:
        raise ValueError('目录里没有可用的视频或图片: ' + str(root))
    return {'root': str(root), 'videos': videos, 'images': images, 'audios': audios,
            'subtitles': subtitles}


def choose_canvas(media, options):
    if options['vertical']:
        return 1080, 1920
    if options['horizontal']:
        return 1920, 1080
    sources = media['videos'] or media['images']
    first = sources[0]
    width, height = int(first.get('width') or 1920), int(first.get('height') or 1080)
    if width <= 0 or height <= 0:
        return 1920, 1080
    scale = min(1.0, 1920 / max(width, height))
    width, height = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
    return max(16, width), max(16, height)


def choose_segments(media, options):
    target = options['duration_us']
    pace = options['pace_us']
    videos = media['videos']
    images = media['images']
    segments = []
    total = 0
    video_index = 0
    anchor_index = 0
    since_image = 0
    guard = 0
    while total < target and guard < 500:
        guard += 1
        if images and since_image >= 4:
            image = images[(len(segments) // 5) % len(images)]
            span = min(int(2.5 * MICROS), target - total)
            if span >= MICROS:
                segments.append({'source': image['source'], 'start_us': total, 'duration_us': span,
                                 'source_start_us': 0, 'media_type': 'photo'})
                total += span
            since_image = 0
            continue
        if not videos:
            image = images[guard % len(images)]
            span = min(int(3 * MICROS), target - total)
            if span < MICROS:
                break
            segments.append({'source': image['source'], 'start_us': total, 'duration_us': span,
                             'source_start_us': 0, 'media_type': 'photo'})
            total += span
            continue
        video = videos[video_index % len(videos)]
        cuts = video.get('cuts') or [0.0]
        anchor = cuts[anchor_index % len(cuts)]
        available = video['duration_us'] - int(anchor * MICROS)
        span = min(pace, target - total, available - 200_000)
        if span < 600_000:
            anchor_index += 1
            if anchor_index % len(cuts) == 0:
                video_index += 1
            continue
        segments.append({
            'source': video['source'],
            'start_us': total,
            'duration_us': span,
            'source_start_us': int(anchor * MICROS),
            'media_type': video.get('media_type', 'video'),
            'has_audio': video.get('has_audio', False),
        })
        total += span
        anchor_index += 1
        since_image += 1
        if anchor_index % len(cuts) == 0:
            video_index += 1
    return segments, total


def parse_srt(path):
    raw = Path(path).read_text(encoding='utf-8', errors='ignore').replace('\r\n', '\n')
    result = []
    for block in re.split(r'\n\s*\n', raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timing = lines[0] if '-->' in lines[0] else lines[1]
        text_start = 1 if '-->' in lines[0] else 2
        match = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)', timing)
        if not match:
            continue
        start = (int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))) * MICROS + \
                int(match.group(4).ljust(3, '0')[:3]) * 1000
        end = (int(match.group(5)) * 3600 + int(match.group(6)) * 60 + int(match.group(7))) * MICROS + \
              int(match.group(8).ljust(3, '0')[:3]) * 1000
        text = ' '.join(lines[text_start:])
        if text:
            result.append((start, end, text))
    return result


def build_plan(media, options, name, title):
    width, height = choose_canvas(media, options)
    segments, total = choose_segments(media, options)
    if not segments:
        raise ValueError('素材不足，无法生成片段')
    bgm = media['audios'][0] if media['audios'] else None
    if options['mute']:
        video_volume = 0.0
    elif bgm:
        video_volume = 0.15
    else:
        video_volume = 1.0
    tracks = [{
        'type': 'video',
        'name': '主视频',
        'segments': [{
            'source': segment['source'],
            'start_us': segment['start_us'],
            'duration_us': segment['duration_us'],
            'source_start_us': segment.get('source_start_us', 0),
            'source_duration_us': segment['duration_us'],
            'volume': video_volume,
        } for segment in segments],
    }]
    if bgm and not options['mute']:
        bgm_segments = []
        cursor = 0
        while cursor < total:
            span = min(bgm['duration_us'], total - cursor)
            if span < MICROS // 2:
                break
            bgm_segments.append({
                'source': bgm['source'],
                'start_us': cursor,
                'duration_us': span,
                'source_start_us': 0,
                'volume': 0.22,
            })
            cursor += span
        if bgm_segments:
            tracks.append({'type': 'audio', 'name': 'BGM', 'segments': bgm_segments})
    text = title or Path(media['root']).name
    tracks.append({
        'type': 'text',
        'name': '标题',
        'segments': [{
            'text': text,
            'start_us': 0,
            'duration_us': min(3 * MICROS, segments[0]['duration_us']),
            'size': 9,
            'y': 0.0,
            'color': '#FFFFFF',
            'border_color': '#000000',
            'border_width': 0.06,
        }],
    })
    captions = []
    if media['subtitles']:
        captions = parse_srt(media['subtitles'][0])
    if captions:
        tracks.append({
            'type': 'text',
            'name': '字幕',
            'segments': [{
                'text': caption[2],
                'start_us': caption[0],
                'duration_us': max(MICROS, min(caption[1], total) - caption[0]),
                'size': 7,
                'y': -0.78,
                'color': '#FFFFFF',
                'border_color': '#000000',
                'border_width': 0.05,
            } for caption in captions if caption[0] < total],
        })
    return {
        'schema': 'jy14-headless-plan/v1',
        'name': name,
        'canvas': {'width': width, 'height': height, 'fps': 30},
        'tracks': tracks,
    }, {
        'segments': len(segments),
        'duration_us': total,
        'canvas': {'width': width, 'height': height},
        'bgm': bgm['source'] if bgm and not options['mute'] else None,
        'captions': len(captions) if captions else 0,
        'captions_file': str(media['subtitles'][0]) if captions else None,
        'video_volume': video_volume,
    }


def auto(input_dir, brief, work, name=None, title=None, publish=False, do_export=False, dry_run=False):
    media = scan(input_dir)
    options = parse_brief(brief)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    name = name or ('自动剪辑-' + stamp)
    plan, summary = build_plan(media, options, name, title or (brief or '').replace('\n', ' ').strip()[:40])
    if dry_run:
        return {
            'status': 'planned',
            'draft_name': name,
            'brief': brief,
            'options': options,
            'summary': summary,
            'plan': plan,
            'live_written': False,
        }
    work = draft.fresh_directory(work)
    plan_path = work / 'plan.json'
    runtime.write_json(plan_path, plan)
    built = draft.build(plan_path, work / 'build')
    result = {
        'status': 'built',
        'plan': str(plan_path),
        'build': str(work / 'build'),
        'draft_name': name,
        'brief': brief,
        'options': options,
        'summary': summary,
        'media': {
            'videos': [video['source'] for video in media['videos']],
            'images': [image['source'] for image in media['images']],
            'audios': [audio['source'] for audio in media['audios']],
            'subtitles': [str(path) for path in media['subtitles']],
        },
        'live_written': False,
    }
    if publish:
        result['publish'] = draft.publish(work / 'build', work / 'audit')
        result['live_written'] = True
    if do_export:
        result['export'] = export_module.export(work / 'build', work / 'export')
    return result
