"""无头 FFmpeg 导出。

剪映 11.6 没有可用的原生导出 ABI（上游项目也只支持 11.5/11.4.2），
因此这里提供独立的 FFmpeg 后端：从已校验的 build 时间线渲染 MP4。
它不调用剪映引擎，也不保证与剪映原生导出的像素完全一致；
文字使用 CoreText 渲染成透明 PNG 后 overlay，不依赖 ffmpeg 的 drawtext。
"""
from __future__ import annotations

from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

from . import codec, draft, media, runtime

MICROS = 1_000_000
SCHEMA = 'jydraft-ffmpeg-export/v1'


def require(value, message):
    if not value:
        raise ValueError(message)


def seconds(value):
    return '%.6f' % (value / MICROS)


def ffmpeg_tool(name, explicit=None):
    return media.ffmpeg(explicit) if name == 'ffmpeg' else media.ffprobe(explicit)


def tool_version(executable):
    result = subprocess.run([str(executable), '-version'], capture_output=True, text=True, timeout=30)
    return result.stdout.splitlines()[0].strip() if result.returncode == 0 else 'unknown'


def media_index(timeline):
    result = {}
    for bucket in ('videos', 'audios', 'texts'):
        for material in timeline.get('materials', {}).get(bucket, []):
            result[material['id']] = (bucket, material)
    return result


def resolve_media(material, build_folder):
    raw = Path(material['path'])
    parts = raw.parts
    if 'Resources' in parts:
        relative = Path(*parts[parts.index('Resources'):])
        candidate = build_folder / relative
        if candidate.is_file():
            return candidate
    if raw.is_absolute() and raw.is_file():
        return raw
    raise RuntimeError('build 中缺少素材: ' + str(raw))


def text_fields(material):
    content = json.loads(material['content'])
    return {
        'text': content.get('text', ''),
        'font_size': float(material.get('font_size', 6)),
        'color': material.get('text_color', '#FFFFFF'),
        'border_color': material.get('border_color', '#000000'),
        'border_width': float(material.get('border_width', .05)),
    }


def build_graph(timeline, build_folder, work, font=None):
    canvas = timeline['canvas_config']
    width, height = int(canvas['width']), int(canvas['height'])
    fps = float(timeline.get('fps', 30))
    total = int(timeline['duration'])
    materials = media_index(timeline)
    inputs, filters, audio_parts = [], [], []
    warnings = []

    def add_input(path, still=False):
        inputs.append({'path': str(path), 'index': len(inputs), 'still': still})
        return len(inputs) - 1

    filters.append('color=c=black:s=%dx%d:r=%g:d=%s[vcanvas]' % (width, height, fps, seconds(total)))
    current = '[vcanvas]'

    video_tracks = [track for track in timeline.get('tracks', []) if track.get('type') == 'video']
    for ti, track in enumerate(video_tracks):
        for si, segment in enumerate(track.get('segments', [])):
            material = materials[segment['material_id']][1]
            source = segment['source_timerange']
            target = segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            speed = float(segment.get('speed', 1))
            path = resolve_media(material, build_folder)
            still = material.get('type') in {'photo', 'gif'}
            index = add_input(path, still)
            raw, placed = 'vr%d_%d' % (ti, si), 'vp%d_%d' % (ti, si)
            chain = ('[%d:v]trim=start=%s:duration=%s,setpts=(PTS-STARTPTS)/%.6f'
                     % (index, seconds(source['start']), seconds(source['duration']), speed))
            if ti == 0:
                chain += (',scale=%d:%d:force_original_aspect_ratio=decrease,'
                          'pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1' % (width, height, width, height))
                x, y = '0', '0'
            else:
                scale = float(segment.get('scale', 1))
                chain += ',scale=iw*%.6f:ih*%.6f,setsar=1' % (scale, scale)
                x = '(W-w)/2+(%.6f)*W/2' % float(segment.get('x', 0))
                y = '(H-h)/2-(%.6f)*H/2' % float(segment.get('y', 0))
            chain += ',setpts=PTS-STARTPTS+%s/TB[%s]' % (seconds(start), raw)
            filters.append(chain)
            filters.append("%s[%s]overlay=x='%s':y='%s':eof_action=pass:shortest=0:"
                           "enable='between(t,%s,%s)'[%s]"
                           % (current, raw, x, y, seconds(start), seconds(start + duration), placed))
            current = '[%s]' % placed
            if material.get('has_audio'):
                label = 'av%d_%d' % (ti, si)
                filters.append(
                    '[%d:a]atrim=start=%s:duration=%s,asetpts=PTS-STARTPTS,atempo=%.6f,'
                    'volume=%.6f,apad,atrim=duration=%s,adelay=%d:all=1[%s]'
                    % (index, seconds(source['start']), seconds(source['duration']), speed,
                       float(segment.get('volume', 1)), seconds(duration), round(start / 1000), label))
                audio_parts.append('[%s]' % label)

    for ti, track in enumerate(timeline.get('tracks', [])):
        if track.get('type') != 'audio':
            continue
        for si, segment in enumerate(track.get('segments', [])):
            material = materials[segment['material_id']][1]
            source, target = segment['source_timerange'], segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            index = add_input(resolve_media(material, build_folder))
            label = 'aa%d_%d' % (ti, si)
            filters.append(
                '[%d:a]atrim=start=%s:duration=%s,asetpts=PTS-STARTPTS,atempo=%.6f,'
                'volume=%.6f,apad,atrim=duration=%s,adelay=%d:all=1[%s]'
                % (index, seconds(source['start']), seconds(source['duration']),
                   float(segment.get('speed', 1)), float(segment.get('volume', 1)),
                   seconds(duration), round(start / 1000), label))
            audio_parts.append('[%s]' % label)

    text_count = 0
    for track in timeline.get('tracks', []):
        if track.get('type') != 'text':
            continue
        for segment in track.get('segments', []):
            material = materials[segment['material_id']][1]
            fields = text_fields(material)
            if not fields['text']:
                continue
            target = segment['target_timerange']
            start, duration = int(target['start']), int(target['duration'])
            size = max(12, round(fields['font_size'] * height / 100))
            border = max(0, round(fields['border_width'] * size))
            image = work / 'captions' / ('%04d.png' % text_count)
            image.parent.mkdir(parents=True, exist_ok=True)
            runtime.make_text_image(fields['text'], image, size, fields['color'],
                                    fields['border_color'], border, font)
            index = add_input(image, True)
            x = '(W-w)/2+(%.6f)*W/2' % float(segment.get('clip', {}).get('transform', {}).get('x', 0))
            y = '(H-h)/2-(%.6f)*H/2' % float(segment.get('clip', {}).get('transform', {}).get('y', -.78))
            label = 'vtext%d' % text_count
            filters.append("%s[%d:v]overlay=x='%s':y='%s':eof_action=pass:shortest=0:"
                           "enable='between(t,%s,%s)'[%s]"
                           % (current, index, x, y, seconds(start), seconds(start + duration), label))
            current = '[%s]' % label
            text_count += 1

    if timeline.get('tracks') and any(segment.get('common_keyframes') for track in timeline['tracks']
                                      for segment in track.get('segments', [])):
        warnings.append('关键帧未参与 FFmpeg 导出，使用片段首帧的静态值')
    if any('mask' in segment for track in timeline.get('tracks', [])
           for segment in track.get('segments', [])):
        warnings.append('原生蒙版未参与 FFmpeg 导出')

    audio_label = None
    if audio_parts:
        filters.append('anullsrc=r=48000:cl=stereo:d=%s[asilence]' % seconds(total))
        filters.append('[asilence]' + ''.join(audio_parts) +
                       'amix=inputs=%d:duration=longest:dropout_transition=0:normalize=0,'
                       'atrim=duration=%s[aout]' % (len(audio_parts) + 1, seconds(total)))
        audio_label = '[aout]'
    filters.append('%strim=duration=%s,setpts=PTS-STARTPTS,format=yuv420p,setrange=range=limited[vout]' % (current, seconds(total)))
    return inputs, ';'.join(filters), audio_label, warnings


def probe_output(ffprobe, output, timeline, expect_audio, timeout):
    result = subprocess.run(
        [str(ffprobe), '-v', 'error', '-count_frames', '-show_streams', '-show_format',
         '-of', 'json', str(output)],
        capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'ffprobe 失败: ' + result.stderr.strip())
    value = json.loads(result.stdout)
    video = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    audio = [s for s in value.get('streams', []) if s.get('codec_type') == 'audio']
    require(len(video) == 1 and video[0].get('codec_name') == 'h264', '输出不是单路 H.264')
    require(video[0].get('pix_fmt') == 'yuv420p', '输出不是 yuv420p')
    canvas = timeline['canvas_config']
    require((int(video[0]['width']), int(video[0]['height'])) ==
            (int(canvas['width']), int(canvas['height'])), '输出分辨率不一致')
    rate = video[0].get('avg_frame_rate', '0/1')
    left, _, right = rate.partition('/')
    fps = float(left) / float(right or 1)
    require(abs(fps - float(timeline.get('fps', 30))) < .01, '输出帧率不一致')
    duration = float(value.get('format', {}).get('duration', 0))
    expected = timeline['duration'] / MICROS
    require(abs(duration - expected) <= max(.12, 2 / fps), '输出时长不一致')
    frames = int(video[0].get('nb_read_frames') or video[0].get('nb_frames') or 0)
    nearest = round(Fraction(timeline['duration'], MICROS) * float(timeline.get('fps', 30)))
    require(abs(frames - nearest) <= 2, '输出帧数不一致: %d vs %d' % (frames, nearest))
    require(len(audio) == (1 if expect_audio else 0), '输出音频流数量不一致')
    return {
        'duration': duration,
        'frames': frames,
        'fps': fps,
        'video_codec': video[0].get('codec_name'),
        'audio_codec': audio[0].get('codec_name') if audio else None,
        'width': int(video[0]['width']),
        'height': int(video[0]['height']),
    }


def export(build_dir, out, ffmpeg=None, ffprobe=None, font=None, crf=18, preset='medium', timeout=900):
    build_dir = Path(build_dir).resolve(strict=True)
    record = draft.verify_build(build_dir)
    timeline = codec.load_json_file(build_dir / 'draft' / 'draft_info.json')
    out = draft.fresh_directory(out)
    started = time.monotonic()
    ffmpeg = ffmpeg_tool('ffmpeg', ffmpeg)
    ffprobe = ffmpeg_tool('ffprobe', ffprobe)
    expect_audio = any(track.get('type') == 'audio' for track in timeline.get('tracks', [])) or any(
        material.get('has_audio')
        for material in timeline.get('materials', {}).get('videos', []))
    work = out
    inputs, graph, audio_label, warnings = build_graph(timeline, build_dir / 'draft', work, font)
    (work / 'filter_complex.txt').write_text(graph, encoding='utf-8')
    command = []
    for item in inputs:
        if item['still']:
            command += ['-loop', '1']
        command += ['-i', item['path']]
    command += ['-filter_complex', graph, '-map', '[vout]']
    if audio_label:
        command += ['-map', audio_label]
    command += ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-pix_fmt', 'yuv420p',
                '-r', str(timeline.get('fps', 30))]
    if audio_label:
        command += ['-c:a', 'aac', '-b:a', '192k']
    command += ['-t', seconds(timeline['duration']), '-movflags', '+faststart', 'render.mp4']
    (work / 'ffmpeg-command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding='utf-8')
    result = subprocess.run([str(ffmpeg)] + command, cwd=work, capture_output=True, text=True, timeout=timeout)
    (work / 'ffmpeg.stdout.log').write_text(result.stdout, encoding='utf-8')
    (work / 'ffmpeg.stderr.log').write_text(result.stderr, encoding='utf-8')
    output = work / 'render.mp4'
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError('FFmpeg 导出失败，日志见 ' + str(work / 'ffmpeg.stderr.log'))
    stats = probe_output(ffprobe, output, timeline, expect_audio, timeout)
    report = {
        'schema': SCHEMA,
        'status': 'exported',
        'backend': 'ffmpeg',
        'native_editable_draft': False,
        'source_build': str(build_dir),
        'output': str(output),
        'size': output.stat().st_size,
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'ffmpeg': {'path': str(ffmpeg), 'version': tool_version(ffmpeg)},
        'ffprobe': {'path': str(ffprobe), 'version': tool_version(ffprobe)},
        'warnings': warnings,
        'stats': stats,
        'draft_name': record['name'],
    }
    (work / 'export.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
