"""素材证据包：把素材目录转成 AI 能直接读的结构化事实。

输出 evidence.json，包含：
- 每个视频的镜头切点与代表帧（供模型看图）；
- 图片缩略图；
- 音频/视频的音量统计（抽样）；
- SRT 字幕的分句时间轴。

它只做“理解素材”，不做剪辑决策；决策由 Agent 基于证据包输出
jy14-headless-plan/v1，再交给 build（草稿）或 export（FFmpeg）执行。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

from . import auto, media

SCHEMA = 'jy14-evidence/v1'
MICROS = 1_000_000


def frame_path(work, source, index):
    stem = Path(source).stem[:32]
    safe = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]+', '_', stem) or 'frame'
    return work / 'frames' / ('%s-%03d.jpg' % (safe, index))


def grab_frame(source, at_us, output, width=480):
    output.parent.mkdir(parents=True, exist_ok=True)
    result = media.run(media.ffmpeg(), [
        '-y', '-v', 'error', '-ss', '%.6f' % (at_us / MICROS), '-i', str(source),
        '-frames:v', '1', '-vf', 'scale=%d:-2' % width, '-q:v', '3', str(output),
    ], timeout=120)
    return result.returncode == 0 and output.is_file() and output.stat().st_size > 0


def grab_still(source, output, width=480):
    output.parent.mkdir(parents=True, exist_ok=True)
    result = media.run(media.ffmpeg(), [
        '-y', '-v', 'error', '-i', str(source),
        '-frames:v', '1', '-vf', 'scale=%d:-2' % width, '-q:v', '3', str(output),
    ], timeout=120)
    return result.returncode == 0 and output.is_file() and output.stat().st_size > 0


def loudness(source, sample_us=120 * MICROS):
    result = media.run(media.ffmpeg(), [
        '-hide_banner', '-v', 'info', '-t', '%.3f' % (sample_us / MICROS), '-i', str(source),
        '-vn', '-af', 'volumedetect', '-f', 'null', '-',
    ], timeout=300)
    mean = re.search(r'mean_volume:\s*(-?[0-9.]+) dB', result.stderr)
    peak = re.search(r'max_volume:\s*(-?[0-9.]+) dB', result.stderr)
    if not mean and not peak:
        return None
    return {
        'sampled_us': sample_us,
        'mean_volume_db': float(mean.group(1)) if mean else None,
        'max_volume_db': float(peak.group(1)) if peak else None,
    }


def shots_of(video, duration_us):
    cuts = sorted({max(0.0, float(value)) for value in (video.get('cuts') or [0.0])})
    starts = [int(round(value * MICROS)) for value in cuts if value * MICROS < duration_us]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    shots = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else duration_us
        if end - start < 200_000:
            continue
        shots.append({'index': len(shots), 'start_us': start, 'end_us': end})
    return shots


def sample_shot_indexes(count, limit):
    if count <= limit:
        return list(range(count))
    step = (count - 1) / float(limit - 1) if limit > 1 else 0
    return sorted({int(round(index * step)) for index in range(limit)})


def analyze(input_dir, work, frames=4, frame_width=480, audio_sample=120.0, with_audio=True):
    scanned = auto.scan(input_dir)
    work = Path(work).expanduser().resolve()
    if work.exists():
        raise ValueError('输出目录已存在: ' + str(work))
    work.mkdir(parents=True, mode=0o700)
    frames = max(1, min(int(frames), 24))
    frame_width = max(120, min(int(frame_width), 1920))
    sample_us = int(max(0.0, audio_sample) * MICROS)

    videos, images, audios, subtitles = [], [], [], []
    frame_count = 0

    for video in scanned['videos']:
        duration = int(video['duration_us'])
        shots = shots_of(video, duration)
        chosen = sample_shot_indexes(len(shots), frames)
        for index in chosen:
            shot = shots[index]
            at = shot['start_us'] + (shot['end_us'] - shot['start_us']) // 2
            output = frame_path(work, video['source'], len(videos) * 100 + shot['index'])
            if grab_frame(video['source'], at, output, frame_width):
                shot['frame'] = str(output)
                shot['frame_at_us'] = at
                frame_count += 1
        entry = dict(video)
        entry['shots'] = shots
        if with_audio and video.get('has_audio') and sample_us:
            info = loudness(video['source'], sample_us)
            if info:
                entry['audio'] = info
        videos.append(entry)

    for image in scanned['images']:
        output = frame_path(work, image['source'], 900 + len(images))
        entry = dict(image)
        if grab_still(image['source'], output, frame_width):
            entry['frame'] = str(output)
            frame_count += 1
        images.append(entry)

    for audio in scanned['audios']:
        entry = dict(audio)
        if with_audio and sample_us:
            info = loudness(audio['source'], sample_us)
            if info:
                entry['audio'] = info
        audios.append(entry)

    for path in scanned['subtitles']:
        cues = [{'start_us': start, 'end_us': end, 'text': text}
                for start, end, text in auto.parse_srt(path)]
        subtitles.append({'source': str(path), 'cues': cues})

    duration_us = max([int(item['duration_us']) for item in videos] or [0])
    evidence = {
        'schema': SCHEMA,
        'status': 'analyzed',
        'root': scanned['root'],
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'summary': {
            'videos': len(videos),
            'images': len(images),
            'audios': len(audios),
            'subtitles': len(subtitles),
            'shots': sum(len(item['shots']) for item in videos),
            'frames': frame_count,
            'longest_video_us': duration_us,
        },
        'videos': videos,
        'images': images,
        'audios': audios,
        'subtitles': subtitles,
    }
    target = work / 'evidence.json'
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    evidence['evidence'] = str(target)
    evidence['work'] = str(work)
    return evidence


def report(result):
    return {
        'status': result['status'],
        'evidence': result['evidence'],
        'work': result['work'],
        'root': result['root'],
        'summary': result['summary'],
    }
