"""本机采集的原生蒙版资源。

只使用剪映缓存里已存在的资源目录，逐文件校验后复制进草稿；
不下载、不改写缓存，也不分发资源。
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import stat

from . import runtime

HERE = Path(__file__).resolve().parent
CATALOG = HERE / 'native-resource-catalog.json'
SHAPES = ('circle', 'rectangle', 'line', 'mirror', 'star', 'heart')
BASIC = {'videos', 'audios', 'texts', 'speeds', 'placeholder_infos', 'canvases',
         'sound_channel_mappings', 'material_colors', 'vocal_separations',
         'material_animations', 'beats', 'loudnesses'}


def require(value, message):
    if not value:
        raise ValueError(message)


def expand(value):
    if isinstance(value, dict):
        return {key: expand(child) for key, child in value.items()}
    if isinstance(value, list):
        return [expand(child) for child in value]
    if isinstance(value, str) and value.startswith('@home/'):
        relative = Path(value[len('@home/'):])
        require(not relative.is_absolute() and '..' not in relative.parts, '资源路径不合法')
        return str(Path.home() / relative)
    return value


def catalog():
    return expand(json.loads(CATALOG.read_bytes()))


def definition(key):
    data = catalog()
    require(key in data['resources'], '本机没有该原生资源: ' + key)
    return deepcopy(data['resources'][key])


def wanted_keys(plan):
    keys = set()
    for track in plan.get('tracks', []):
        for segment in track.get('segments', []):
            if 'mask' in segment:
                keys.add('mask/' + segment['mask']['shape'])
    return sorted(keys)


def tree_manifest(folder):
    folder = Path(folder)
    require(folder.is_dir() and not folder.is_symlink(), '资源目录不存在或为符号链接')
    result = {}
    for path in sorted(folder.rglob('*')):
        info = path.lstat()
        require(not path.is_symlink(), '资源不允许符号链接')
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode), '资源包含非普通文件')
        result[path.relative_to(folder).as_posix()] = {
            'sha256': runtime.digest(path),
            'size': info.st_size,
        }
    return result


def verify_cached(entry, actual):
    original = entry['files']
    for name, info in original.items():
        require(actual.get(name) == info, '本机缓存资源字节与采集记录不一致: ' + name)
    extra = {name: info for name, info in actual.items() if name not in original}
    allowed = entry.get('native_generated_cache_files', {})
    for name, info in extra.items():
        require(allowed.get(name) == info, '本机缓存出现未登记的新文件: ' + name)
    return sorted(extra)


def relative_path(entry):
    fingerprint = entry['tree_sha256']
    require(len(fingerprint) == 64 and set(fingerprint) <= set('0123456789abcdef'), '资源指纹不合法')
    return 'Resources/headless-native/' + fingerprint


def prepare(plan, folder):
    keys = wanted_keys(plan)
    if not keys:
        return []
    records = []
    for key in keys:
        entry = definition(key)
        source = Path(entry['source'])
        require(source.is_dir() and not source.is_symlink(), '本机资源目录不存在: ' + str(source))
        generated = verify_cached(entry, tree_manifest(source))
        relative = relative_path(entry)
        destination = Path(folder) / relative
        if not destination.exists():
            destination.mkdir(parents=True, mode=0o700)
            for name in entry['files']:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source / name, target, follow_symlinks=False)
        require(tree_manifest(destination) == entry['files'], '草稿内资源复制后校验失败: ' + key)
        for path in [destination] + list(destination.rglob('*')):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        record = {
            'key': key,
            'relative': relative,
            'tree_sha256': entry['tree_sha256'],
            'files': entry['files'],
            'resource_id': entry['material']['resource_id'],
            'origin': 'native-application-selection',
            'redistribution_authorized': False,
        }
        if generated:
            record['source_generated_cache_files_not_copied'] = generated
        records.append(record)
    return records


def material(key, target):
    entry = definition(key)
    value = deepcopy(entry['material'])
    value['path'] = str(Path(target) / relative_path(entry))
    return value


def verify_files(records, folder, plan):
    require([record['key'] for record in records] == wanted_keys(plan), '原生资源清单与计划不一致')
    for record in records:
        entry = definition(record['key'])
        require(tree_manifest(Path(folder) / record['relative']) == entry['files'],
                '草稿内原生资源字节变化: ' + record['key'])
