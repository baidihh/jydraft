"""剪辑计划校验与素材登记。

计划格式沿用 jy14-headless-plan/v1：
所有时间为整数微秒，素材路径必须是绝对路径。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import motion, runtime

SCHEMA = 'jy14-headless-plan/v1'
STILL_CAPACITY_US = 10_800_000_000
MICROS = 1_000_000


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('%s 必须是 >= %s 的整数' % (label, minimum))
    return value


def number(value, label, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('%s 超出范围 %s..%s' % (label, low, high))
    return value


def keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('%s 包含不支持的字段: %s' % (label, sorted(set(value) - set(allowed))))


def validate(plan):
    keys(plan, {'schema', 'name', 'canvas', 'tracks'}, 'plan')
    if plan.get('schema') != SCHEMA:
        raise ValueError('不支持的 plan schema: %s' % plan.get('schema'))
    name = plan.get('name')
    if not (isinstance(name, str) and name.strip() == name and name and len(name.encode()) < 200
            and name not in {'.', '..'} and not any(c in name for c in '/\\\x00\n\r') and not name.startswith('.')):
        raise ValueError('草稿名必须是单个可见目录名')

    keys(plan.get('canvas'), {'width', 'height', 'fps'}, 'canvas')
    for field in ('width', 'height'):
        if not 16 <= integer(plan['canvas'].get(field), field) <= 8192:
            raise ValueError('画布尺寸必须在 16..8192')
    if plan['canvas'].get('fps') not in {24, 25, 30, 50, 60}:
        raise ValueError('仅支持 24/25/30/50/60 fps')

    tracks = plan.get('tracks')
    if not isinstance(tracks, list):
        raise ValueError('tracks 必须是数组')
    assets = {}
    seen_main = False
    duration = 0

    for index, track in enumerate(tracks):
        keys(track, {'type', 'name', 'segments'}, 'track')
        kind = track.get('type')
        if kind not in {'video', 'audio', 'text'}:
            raise ValueError('当前版本只实现 video / audio / text 轨道（不含滤镜、特效、蒙版、转场）')
        if not isinstance(track.get('name', kind), str) or not track.get('name', kind).strip():
            raise ValueError('轨道名不能为空')
        segments = track.get('segments')
        if not isinstance(segments, list) or not segments:
            raise ValueError('每条轨道至少需要一个片段')
        if kind == 'video' and not seen_main:
            if index != 0:
                raise ValueError('主视频轨道必须是第一条轨道')
            seen_main = True

        prior_end = 0
        for seg in segments:
            allowed = {'start_us', 'duration_us', 'keyframes', 'opacity'}
            if kind == 'video':
                allowed |= {'source', 'source_start_us', 'source_duration_us', 'speed', 'volume',
                            'scale', 'x', 'y', 'rotation', 'mask'}
            elif kind == 'audio':
                allowed |= {'source', 'source_start_us', 'source_duration_us', 'speed', 'volume'}
            else:
                allowed |= {'text', 'size', 'x', 'y', 'color', 'border_color', 'border_width'}
            unknown = set(seg) - allowed
            if unknown:
                raise ValueError('片段包含当前版本未实现的字段: %s' % sorted(unknown))

            start = integer(seg.get('start_us', 0), 'start_us')
            span = integer(seg.get('duration_us'), 'duration_us', 1)
            if start < prior_end:
                raise ValueError('同轨片段必须有序且不重叠')
            if index == 0 and kind == 'video' and abs(start - prior_end) > 1:
                raise ValueError('主视频必须连续，空隙请放到画中画轨道')
            prior_end = start + span
            duration = max(duration, prior_end)

            motion.validate(seg, kind)

            if kind == 'text':
                if not isinstance(seg.get('text'), str) or not seg['text'].strip():
                    raise ValueError('字幕文本不能为空')
                number(seg.get('size', 6), 'text size', 1, 100)
                number(seg.get('border_width', .05), 'border_width', 0, 1)
                for field in ('color', 'border_color'):
                    if not re.fullmatch(r'#[0-9a-fA-F]{6}', seg.get(field, '#FFFFFF')):
                        raise ValueError('颜色必须是 #RRGGBB')
                for field in ('x', 'y'):
                    number(seg.get(field, 0 if field == 'x' else -.78), field, -5, 5)
                continue

            source = Path(seg.get('source', ''))
            if not source.is_absolute():
                raise ValueError('素材路径必须是绝对路径')
            if source.is_symlink():
                raise ValueError('素材不能是符号链接: ' + str(source))
            source = source.resolve(strict=True)
            source_text = str(source)
            if source_text not in assets:
                assets[source_text] = runtime.probe(source_text)
            asset = assets[source_text]
            if asset['kind'] != kind:
                raise ValueError('轨道类型与素材类型不一致: ' + source_text)
            source_start = integer(seg.get('source_start_us', 0), 'source_start_us')
            source_span = integer(seg.get('source_duration_us', span), 'source_duration_us', 1)
            speed = number(seg.get('speed', 1), 'speed', .1, 8)
            if asset['media_type'] == 'photo' and (source_start != 0 or speed != 1):
                raise ValueError('静态图片不支持源偏移或变速')
            if abs(source_span / speed - span) > 2:
                raise ValueError('source_duration_us / speed 必须等于 duration_us: ' + source_text)
            if source_start + source_span > asset['duration_us'] + 1:
                raise ValueError('裁剪区间超出素材时长: ' + source_text)
            number(seg.get('volume', 1), 'volume', 0, 4)
            if kind == 'video':
                number(seg.get('scale', 1), 'scale', .01, 10)
                number(seg.get('rotation', 0), 'rotation', -360, 360)
            for field in ('x', 'y'):
                number(seg.get(field, 0), field, -5, 5)

    if tracks:
        if not seen_main:
            raise ValueError('非空草稿需要一条主视频轨道')
        main_end = max(seg.get('start_us', 0) + seg['duration_us'] for seg in tracks[0]['segments'])
        if duration > main_end:
            raise ValueError('画中画、字幕和音频不能超过主视频长度')
        duration = main_end
    return assets, duration
