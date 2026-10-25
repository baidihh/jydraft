"""线性关键帧与原生静态蒙版。"""
from __future__ import annotations

import math
import uuid

from . import resources

KEYFRAMES = {
    'x': ('KFTypePositionX', -5, 5),
    'y': ('KFTypePositionY', -5, 5),
    'scale': ('KFTypeScaleX', .01, 10),
    'rotation': ('KFTypeRotation', -360, 360),
    'opacity': ('KFTypeAlpha', 0, 1),
    'volume': ('KFTypeVolume', 0, 4),
}
MASKS = {'circle': '圆形', 'rectangle': '矩形', 'line': '线性', 'mirror': '镜面', 'star': '星形', 'heart': '爱心'}


def require(value, message):
    if not value:
        raise ValueError(message)


def numeric(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('%s 超出范围 %s..%s' % (label, low, high))


def validate(spec, kind):
    if 'opacity' in spec:
        require(kind in {'video', 'text'}, 'opacity 只能用于画面片段')
        numeric(spec['opacity'], 0, 1, 'opacity')
    if 'mask' in spec:
        require(kind == 'video', '蒙版只能用于视频片段')
        mask = spec['mask']
        require(isinstance(mask, dict) and not set(mask) - {
            'shape', 'width', 'height', 'x', 'y', 'rotation', 'feather', 'invert', 'round_corner',
        }, '不支持的蒙版字段')
        require(mask.get('shape') in MASKS, '不支持的蒙版形状')
        require(type(mask.get('invert', False)) is bool, '蒙版 invert 必须是布尔值')
        require(mask['shape'] != 'line' or not {'width', 'height'}.intersection(mask),
                '线性蒙版不接受 width/height')
        for name, default, low, high in [('width', .28, .001, 5), ('height', .5, .001, 5),
                                         ('x', 0, -5, 5), ('y', 0, -5, 5), ('rotation', 0, -360, 360),
                                         ('feather', 0, 0, 1), ('round_corner', 0, 0, 1)]:
            numeric(mask.get(name, default), low, high, '蒙版 ' + name)
        require(mask.get('round_corner', 0) == 0 or mask['shape'] == 'rectangle',
                '圆角只适用于矩形蒙版')
        resources.definition('mask/' + mask['shape'])
    if 'keyframes' not in spec:
        return
    series = spec['keyframes']
    allowed = ({'x', 'y', 'scale', 'rotation', 'opacity', 'volume'} if kind == 'video'
               else {'volume'} if kind == 'audio' else {'x', 'y', 'scale', 'rotation'})
    require(isinstance(series, dict) and series and not set(series) - allowed, '不支持的 keyframes 通道')
    require(spec.get('speed', 1) == 1 and spec.get('source_start_us', 0) == 0,
            '变速或裁剪过的素材暂不支持关键帧时间映射')
    for channel, points in series.items():
        require(isinstance(points, list) and len(points) >= 2, '每条关键帧曲线至少需要两个点')
        previous = -1
        for point in points:
            require(isinstance(point, dict) and set(point) == {'at_us', 'value'},
                    '关键帧只接受 at_us / value')
            at = point['at_us']
            require(type(at) is int and previous < at <= spec['duration_us'],
                    '关键帧时间必须严格递增且落在片段内')
            previous = at
            numeric(point['value'], KEYFRAMES[channel][1], KEYFRAMES[channel][2], '关键帧 ' + channel)
        require(points[0]['at_us'] == 0, '第一个关键帧必须从片段起点开始')
        if channel in spec:
            require(abs(points[0]['value'] - spec[channel]) < 1e-7, '静态值与首个关键帧不一致: ' + channel)


def keyframe_groups(series):
    groups = []
    for channel, points in series.items():
        groups.append({
            'id': str(uuid.uuid4()).upper(),
            'material_id': '',
            'property_type': KEYFRAMES[channel][0],
            'keyframe_list': [{
                'id': str(uuid.uuid4()).upper(),
                'curveType': 'Line',
                'graphID': '',
                'left_control': {'x': 0.0, 'y': 0.0},
                'right_control': {'x': 0.0, 'y': 0.0},
                'time_offset': point['at_us'],
                'values': [point['value']],
            } for point in points],
        })
    return groups


def mask_config(mask):
    entry = resources.definition('mask/' + mask['shape'])
    return {
        'aspectRatio': entry['aspect_ratio'],
        'centerX': mask.get('x', 0),
        'centerY': mask.get('y', 0),
        'width': mask.get('width', .28),
        'height': mask.get('height', 1 if mask['shape'] == 'line' else .5),
        'rotation': mask.get('rotation', 0),
        'invert': mask.get('invert', False),
        'feather': mask.get('feather', 0),
        'roundCorner': mask.get('round_corner', 0),
    }


def mask_material(mask, target):
    node = resources.material('mask/' + mask['shape'], target)
    node.update(id=str(uuid.uuid4()).upper(), constant_material_id=str(uuid.uuid4()).upper(),
                config=mask_config(mask))
    return node


def apply(segment, materials, spec, kind, target=None):
    if 'opacity' in spec:
        segment['clip']['alpha'] = spec['opacity']
    if 'keyframes' in spec:
        segment['common_keyframes'] = keyframe_groups(spec['keyframes'])
        for channel, points in spec['keyframes'].items():
            value = points[0]['value']
            if channel in {'x', 'y'}:
                segment['clip']['transform'][channel] = value
            elif channel == 'scale':
                segment['clip']['scale'] = {'x': value, 'y': value}
            elif channel == 'opacity':
                segment['clip']['alpha'] = value
            elif channel == 'rotation':
                segment['clip']['rotation'] = value
            else:
                segment['volume'] = value
        if 'scale' in spec['keyframes']:
            segment['uniform_scale'] = {'on': True, 'value': 1.0}
    if 'mask' in spec:
        require(target is not None, '蒙版需要草稿目录')
        node = mask_material(spec['mask'], target)
        materials.setdefault('common_mask', []).append(node)
        segment.setdefault('extra_material_refs', []).append(node['id'])
        segment['enable_video_mask'] = True
