"""草稿构建与首页登记。

产物目录与剪映 11.6 原生草稿保持一致：
  draft_info.json                    主时间线（明文；剪映保存后可能改写为加密格式）
  draft_meta_info.json               草稿元信息（明文）
  template-2.tmp / *.bak             时间线镜像
  Timelines/<id>/draft_info.json     时间线镜像
  Timelines/project.json             多时间线工程
  Timelines/<id>/template.tmp        旧版时间线
  key_value.json                     素材登记
  timeline_layout.json               时间线布局
  draft_virtual_store.json           素材库视图
  draft_settings                     草稿设置
  draft_cover.jpg                    封面
  Resources/headless-media/<sha>     草稿自有素材副本
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from . import codec, motion, plan as plan_module, resources, runtime

HERE = Path(__file__).resolve().parent
ROOT_WORK = HERE.parent / '.work'
SCHEMA = 'jy14-headless-plan/v1'
BUILD_SCHEMA = 'jydraft-build/v1'
MICROS = 1_000_000
STILL_CAPACITY_US = plan_module.STILL_CAPACITY_US
DRAFT_VERSION = 360000


def schema_version():
    version = str(runtime.app_info().get('version') or '11.6.0')
    parts = [int(part) for part in version.split('.')[:2] if part.isdigit()]
    major, minor = (parts + [0, 0])[:2]
    if (major, minor) >= (11, 6):
        return '189.0.0'
    if (major, minor) >= (11, 5):
        return '187.0.0'
    return '185.0.0'


def require(value, message):
    if not value:
        raise ValueError(message)


def identifier():
    return str(uuid.uuid4()).upper()


def blueprint():
    return runtime.read_json(HERE / 'blueprint.json')


def platform_info():
    info = runtime.app_info()
    version = str(info.get('version') or '11.6.0')
    if version.count('.') >= 3:
        version = '.'.join(version.split('.')[:3])
    return {
        'os': 'mac' if runtime.platform_support.IS_MAC else 'windows',
        'os_version': runtime.macos_version(),
        'app_id': 3704,
        'app_version': version,
        'app_source': 'lv',
    }


def remap(value, ids):
    if isinstance(value, dict):
        return {key: remap(child, ids) for key, child in value.items()}
    if isinstance(value, list):
        return [remap(child, ids) for child in value]
    return ids.get(value, value) if isinstance(value, str) else value


def rgb(color):
    return [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]


def text_material(material, seg):
    content = json.loads(material['content'])
    content['text'] = seg['text']
    length = len(seg['text'].encode('utf-16-le')) // 2
    color = seg.get('color', '#FFFFFF')
    border = seg.get('border_color', '#000000')
    size = seg.get('size', 6)
    width = seg.get('border_width', .05)
    for style in content['styles']:
        style['range'] = [0, length]
        style['size'] = size
        style['fill']['content']['solid']['color'] = rgb(color)
        for stroke in style.get('strokes', []):
            stroke['width'] = width
            stroke['content']['solid']['color'] = rgb(border)
    material.update(content=json.dumps(content, ensure_ascii=False, separators=(',', ':')),
                    font_size=size, text_color=color, border_color=border, border_width=width)


def library_record(asset, target, now):
    media_type = asset.get('media_type', 'video' if asset['kind'] == 'video' else 'music')
    duration = 5 * MICROS if media_type == 'photo' else asset['duration_us']
    return {
        'ai_group_type': '',
        'create_time': now // MICROS,
        'duration': duration,
        'enter_from': 0,
        'extra_info': Path(asset['source']).name,
        'file_Path': str(target / asset['relative']),
        'height': asset['height'],
        'width': asset['width'],
        'id': asset['local_id'],
        'import_time': now // MICROS,
        'import_time_ms': now,
        'item_source': 1,
        'material_color_tag': '',
        'md5': '',
        'metetype': media_type,
        'roughcut_time_range': {'duration': -1, 'start': -1} if media_type in {'photo', 'gif'}
        else {'duration': duration, 'start': 0},
        'sub_time_range': {'duration': -1, 'start': -1},
        'type': 0,
    }


def timeline_for(plan, assets, target, tid):
    bp = blueprint()
    doc = deepcopy(bp['timeline'])
    doc.update(id=tid, tracks=[], materials={}, duration=0, color_space=0,
               canvas_config={key: plan['canvas'][key] for key in ('width', 'height')},
               version=DRAFT_VERSION, new_version=schema_version(),
               platform=platform_info(), last_modified_platform=platform_info())
    doc['fps'] = float(plan['canvas']['fps'])
    registrations = {}

    for track_index, track in enumerate(plan['tracks']):
        kind = track['type']
        sample = bp[kind]
        new_track = deepcopy(sample['track'])
        new_track.update(id=identifier(), segments=[], name=track.get('name', kind), is_default_name=False)
        if kind == 'video' and track_index > 0:
            new_track['flag'] = 2

        for seg_index, spec in enumerate(track['segments']):
            ids = {sample['segment']['id']: identifier()}
            ids.update({material['id']: identifier() for _, material in sample['materials']})
            segment = remap(deepcopy(sample['segment']), ids)
            segment['target_timerange'] = {'start': spec.get('start_us', 0), 'duration': spec['duration_us']}
            segment['track_render_index'] = track_index
            if kind == 'text':
                segment['render_index'] = 14000 + track_index
            elif kind == 'video' and track_index > 0:
                segment['render_index'] = track_index
            else:
                segment.pop('render_index', None)

            if kind in {'video', 'text'}:
                segment['clip']['transform'] = {
                    'x': spec.get('x', 0),
                    'y': spec.get('y', -.78 if kind == 'text' else 0),
                }
                segment['clip']['scale'] = {'x': spec.get('scale', 1), 'y': spec.get('scale', 1)}
                if spec.get('rotation'):
                    segment['clip']['rotation'] = spec['rotation']

            asset = None
            if kind != 'text':
                asset = assets[str(Path(spec['source']).resolve())]
                segment.update(
                    source_timerange={
                        'start': spec.get('source_start_us', 0),
                        'duration': spec.get('source_duration_us', spec['duration_us']),
                    },
                    speed=spec.get('speed', 1),
                    volume=spec.get('volume', 1),
                )

            for bucket, original in sample['materials']:
                material = remap(deepcopy(original), ids)
                if bucket == 'speeds':
                    material['speed'] = spec.get('speed', 1)
                if bucket in {'videos', 'audios'}:
                    material.update(path=str(target / asset['relative']), duration=asset['duration_us'],
                                    local_material_id=asset['local_id'])
                    if bucket == 'videos':
                        media_type = asset.get('media_type', 'video')
                        material.update(width=asset['width'], height=asset['height'],
                                        material_name=Path(asset['source']).name,
                                        type=media_type, has_audio=asset['has_audio'])
                        if media_type in {'photo', 'gif'}:
                            material['duration'] = STILL_CAPACITY_US
                    else:
                        material.update(name=Path(asset['source']).name, music_id=asset['local_id'],
                                        resource_id=asset['local_id'])
                if bucket == 'texts':
                    text_material(material, spec)
                doc['materials'].setdefault(bucket, []).append(material)

            if kind != 'text':
                registration = deepcopy(bp['local_registration'])
                registration.update(segmentId=segment['id'], materialId=asset['sha256'][:32],
                                    materialName=Path(asset['source']).name, rank=str(seg_index + 1))
                registrations[segment['id']] = registration

            motion.apply(segment, doc['materials'], spec, kind, target)
            new_track['segments'].append(segment)
            doc['duration'] = max(doc['duration'], spec.get('start_us', 0) + spec['duration_us'])
        doc['tracks'].append(new_track)
    return doc, registrations


def index_entry(metadata, target):
    from copy import deepcopy as clone

    entry = {key: clone(value) for key, value in metadata.items()
             if key.startswith(('draft_', 'tm_', 'cloud_', 'pippit_'))
             and key not in {'draft_materials', 'draft_materials_copied_info', 'draft_segment_extra_info',
                             'draft_enterprise_info', 'draft_timeline_materials_size_'}}
    entry.update(draft_cover=str(target / 'draft_cover.jpg'),
                 draft_json_file=str(target / 'draft_info.json'),
                 draft_timeline_materials_size=metadata['draft_timeline_materials_size_'],
                 streaming_edit_draft_ready=True)
    return entry


def files_manifest(folder):
    result = {}
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file():
            result[str(path.relative_to(folder))] = {'sha256': runtime.digest(path), 'size': path.stat().st_size}
    return result


def fresh_directory(path):
    path = Path(path).resolve()
    root = runtime.draft_root().resolve()
    if path == root or root in path.parents:
        raise ValueError('build / audit 目录必须放在草稿根目录之外')
    if path.exists():
        raise ValueError('输出目录已存在: ' + str(path))
    path.mkdir(parents=True, mode=0o700)
    return path


def build(plan_path, out, plaintext=False):
    plan = plan_path if isinstance(plan_path, dict) else runtime.read_json(plan_path)
    assets, duration = plan_module.validate(plan)
    if not plaintext:
        try:
            codec.build_codec()
        except Exception:
            plaintext = True
    out = fresh_directory(out)
    folder = out / 'draft'
    folder.mkdir(mode=0o700)
    target = runtime.draft_root() / plan['name']
    now = runtime.now_us()
    draft_id, timeline_id, project_id = identifier(), identifier(), identifier()

    for asset in assets.values():
        asset['local_id'] = str(uuid.uuid4())
        asset['relative'] = 'Resources/headless-media/' + asset['sha256'] + Path(asset['source']).suffix.lower()
        destination = folder / asset['relative']
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with Path(asset['source']).open('rb') as source, destination.open('xb') as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        if runtime.digest(destination) != asset['sha256']:
            raise RuntimeError('素材复制后校验失败: ' + asset['source'])

    native_resources = resources.prepare(plan, folder)
    timeline, registrations = timeline_for(plan, assets, target, timeline_id)
    metadata = deepcopy(blueprint()['metadata'])
    metadata.update(
        draft_id=draft_id,
        draft_name=target.name,
        draft_fold_path=str(target),
        draft_root_path=str(runtime.draft_root()),
        tm_draft_create=now,
        tm_draft_modified=now,
        tm_duration=duration,
        draft_materials=[{'type': 0, 'value': [library_record(asset, target, now) for asset in assets.values()]}]
        + [{'type': value_type, 'value': []} for value_type in (1, 2, 3, 6, 7, 8)],
    )
    project = deepcopy(blueprint()['project'])
    project.update(
        id=project_id,
        main_timeline_id=timeline_id,
        create_time=now,
        update_time=now,
        timelines=[{'id': timeline_id, 'name': '时间线01', 'create_time': now,
                    'update_time': now, 'is_marked_delete': False}],
    )
    legacy = deepcopy(blueprint()['legacy'])
    legacy.update(id=identifier(), duration=0, tracks=[], materials={}, fps=float(plan['canvas']['fps']))

    timeline_plain = runtime.packed(timeline)
    timeline_bytes = timeline_plain if plaintext else codec.encrypt_bytes(timeline_plain)
    if not plaintext and codec.decrypt_bytes(timeline_bytes) != timeline_plain:
        raise RuntimeError('时间线加密/解密往返不一致')
    timeline_dir = folder / 'Timelines' / timeline_id
    timeline_dir.mkdir(parents=True, mode=0o700)
    for path in (folder / 'draft_info.json', folder / 'template-2.tmp', folder / 'draft_info.json.bak',
                 timeline_dir / 'draft_info.json', timeline_dir / 'template-2.tmp',
                 timeline_dir / 'draft_info.json.bak'):
        path.write_bytes(timeline_bytes)
        os.chmod(path, 0o600)
    runtime.write_json(folder / 'key_value.json', registrations)
    for name in ('project.json', 'project.json.bak'):
        runtime.write_json(folder / 'Timelines' / name, project)
    runtime.write_json(timeline_dir / 'template.tmp', legacy)
    runtime.write_json(folder / 'timeline_layout.json', {
        'activeTimeline': timeline_id,
        'dockItems': [{'dockIndex': 0, 'ratio': 1, 'timelineIds': [timeline_id], 'timelineNames': ['时间线01']}],
        'layoutOrientation': 1,
    })
    runtime.write_json(folder / 'draft_virtual_store.json', {
        'draft_materials': [],
        'draft_virtual_store': [
            {'type': 0, 'value': [{'creation_time': 0, 'display_name': '', 'filter_type': 0, 'id': '',
                                  'import_time': 0, 'import_time_us': 0, 'material_color_tag': '',
                                  'sort_sub_type': 0, 'sort_type': 0, 'subdraft_filter_type': 0}]},
            {'type': 1, 'value': [{'child_id': asset['local_id'], 'parent_id': ''} for asset in assets.values()]},
            {'type': 2, 'value': []},
        ],
    })
    (folder / 'draft_settings').write_text(
        '[General]\ncloud_last_modify_platform=mac\ndraft_create_time=%d\ndraft_last_edit_time=%d\n'
        % (now // MICROS, now // MICROS), encoding='utf-8')
    os.chmod(folder / 'draft_settings', 0o600)

    video_assets = [asset for asset in assets.values() if asset['media_type'] in {'video', 'photo', 'gif'}]
    if video_assets:
        runtime.make_cover(folder / video_assets[0]['relative'], folder / 'draft_cover.jpg')
    elif assets:
        runtime.make_cover(next(iter(assets.values()))['source'], folder / 'draft_cover.jpg')
    else:
        runtime.make_blank_cover(folder / 'draft_cover.jpg')
    shutil.copyfile(folder / 'draft_cover.jpg', timeline_dir / 'draft_cover.jpg')

    metadata['draft_timeline_materials_size_'] = sum(asset['size'] for asset in assets.values()) + len(timeline_bytes)
    metadata_plain = runtime.packed(metadata)
    metadata_bytes = metadata_plain if plaintext else codec.encrypt_bytes(metadata_plain)
    if not plaintext and codec.decrypt_bytes(metadata_bytes) != metadata_plain:
        raise RuntimeError('草稿元信息加密/解密往返不一致')
    (folder / 'draft_meta_info.json').write_bytes(metadata_bytes)
    os.chmod(folder / 'draft_meta_info.json', 0o600)

    runtime.write_json(out / 'plan.json', plan)
    record = {
        'schema': BUILD_SCHEMA,
        'name': target.name,
        'target': str(target),
        'draft_id': draft_id,
        'timeline_id': timeline_id,
        'project_id': project_id,
        'created_us': now,
        'duration_us': duration,
        'draft_new_version': schema_version(),
        'app': runtime.app_info(),
        'encoding': 'plain-json' if plaintext else 'encrypted-v1',
        'assets': list(assets.values()),
        'native_resources': native_resources,
        'plan_sha256': runtime.digest(out / 'plan.json'),
        'files': files_manifest(folder),
        'ui_preparation_used': False,
        'live_written': False,
    }
    runtime.write_json(out / 'build.json', record)
    return {
        'status': 'built',
        'build': str(out),
        'name': target.name,
        'duration_us': duration,
        'tracks': len(plan['tracks']),
        'media_files': len(assets),
        'target': str(target),
        'live_written': False,
    }


def publish(build_dir, audit_dir):
    build_dir = Path(build_dir).resolve(strict=True)
    record = runtime.read_json(build_dir / 'build.json')
    if record.get('schema') != BUILD_SCHEMA:
        raise ValueError('不是本工具生成的 build 目录')
    plan = runtime.read_json(build_dir / 'plan.json')
    if runtime.digest(build_dir / 'plan.json') != record['plan_sha256']:
        raise ValueError('plan.json 在 build 后被修改')
    if files_manifest(build_dir / 'draft') != record['files']:
        raise ValueError('build 目录内容被修改')
    runtime.ensure_editor_closed()

    root = runtime.draft_root()
    target = root / plan['name']
    if target.exists():
        raise ValueError('目标草稿已存在: ' + str(target))
    audit = fresh_directory(audit_dir)

    root_meta = root / 'root_meta_info.json'
    if root_meta.is_file():
        original = runtime.read_json(root_meta)
        shutil.copyfile(root_meta, audit / 'root_meta_info.original.json')
    else:
        original = {'root_path': str(root), 'all_draft_store': [], 'draft_ids': 0}
    original.setdefault('root_path', str(root))
    original.setdefault('all_draft_store', [])
    original.setdefault('draft_ids', 0)

    shutil.copytree(build_dir / 'draft', target, copy_function=shutil.copy2)
    if files_manifest(target) != record['files']:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError('草稿复制后校验失败，已回滚')

    metadata = codec.load_json_file(target / 'draft_meta_info.json')
    updated = deepcopy(original)
    updated['root_path'] = str(root)
    updated['all_draft_store'].insert(0, index_entry(metadata, target))
    updated['draft_ids'] = int(original['draft_ids']) + 1
    temporary = root / ('.root_meta_info.jydraft-' + uuid.uuid4().hex + '.tmp')
    temporary.write_bytes(runtime.packed(updated))
    os.chmod(temporary, 0o600)
    os.replace(temporary, root_meta)

    result = {
        'status': 'created',
        'draft': str(target),
        'name': target.name,
        'draft_id': record['draft_id'],
        'root_index': str(root_meta),
        'audit': str(audit),
        'ui_preparation_used': False,
        'native_ui_acceptance': 'pending',
        'video_exported': False,
    }
    runtime.write_json(audit / 'result.json', result)
    return result


def write_protected_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = codec.encrypt_bytes(runtime.packed(value))
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def timeline_mirrors(folder, timeline_id):
    timeline_dir = Path(folder) / 'Timelines' / timeline_id
    timeline_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return [
        Path(folder) / 'draft_info.json',
        Path(folder) / 'template-2.tmp',
        Path(folder) / 'draft_info.json.bak',
        timeline_dir / 'draft_info.json',
        timeline_dir / 'template-2.tmp',
        timeline_dir / 'draft_info.json.bak',
    ]


def backup_paths(target, paths, label):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = ROOT_WORK / 'backups' / (target.name + '-' + label + '-' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        relative = path.relative_to(target) if target in path.parents else Path(path.name)
        destination = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return backup


def update_home_index(draft_id, values):
    root = runtime.draft_root()
    root_meta = root / 'root_meta_info.json'
    index = runtime.read_json(root_meta)
    matched = 0
    for entry in index.get('all_draft_store', []):
        if entry.get('draft_id') == draft_id:
            entry.update(values)
            matched += 1
    if matched == 0:
        raise ValueError('首页索引中没有该草稿，先执行 publish')
    temporary = root / ('.root_meta_info.jydraft-' + uuid.uuid4().hex + '.tmp')
    temporary.write_bytes(runtime.packed(index))
    os.chmod(temporary, 0o600)
    os.replace(temporary, root_meta)
    return matched


def edit_text(draft_name, text, index=None, old=None):
    runtime.ensure_editor_closed()
    target = runtime.draft_root() / draft_name
    if not target.is_dir():
        raise ValueError('草稿不存在: ' + str(target))
    timeline = codec.load_json_file(target / 'draft_info.json')
    metadata = codec.load_json_file(target / 'draft_meta_info.json')
    candidates = []
    for track in timeline.get('tracks', []):
        if track.get('type') != 'text':
            continue
        for segment in track.get('segments', []):
            candidates.append(segment)
    if not candidates:
        raise ValueError('草稿里没有文字片段')
    if index is not None:
        if not 0 <= index < len(candidates):
            raise ValueError('文字片段序号超出范围 0..%d' % (len(candidates) - 1))
        segment = candidates[index]
    elif old:
        segment = None
        for candidate in candidates:
            material = next((m for m in timeline['materials'].get('texts', [])
                             if m['id'] == candidate['material_id']), None)
            if material and json.loads(material['content']).get('text') == old:
                segment = candidate
                break
        if segment is None:
            raise ValueError('没找到包含该文本的片段: ' + old)
    else:
        raise ValueError('需要 --index 或 --old 指定要修改的文字片段')
    material = next((m for m in timeline['materials'].get('texts', [])
                     if m['id'] == segment['material_id']), None)
    if material is None:
        raise ValueError('文字素材缺失')
    content = json.loads(material['content'])
    content['text'] = text
    length = len(text.encode('utf-16-le')) // 2
    for style in content.get('styles', []):
        style['range'] = [0, length]
    material['content'] = json.dumps(content, ensure_ascii=False, separators=(',', ':'))
    now = runtime.now_us()
    metadata['tm_draft_modified'] = now
    mirrors = timeline_mirrors(target, timeline['id'])
    backup = backup_paths(target, mirrors + [target / 'draft_meta_info.json',
                                            runtime.draft_root() / 'root_meta_info.json'], 'edit-text')
    write_protected_json(target / 'draft_info.json', timeline)
    encrypted = (target / 'draft_info.json').read_bytes()
    for path in mirrors[1:]:
        path.write_bytes(encrypted)
        os.chmod(path, 0o600)
    write_protected_json(target / 'draft_meta_info.json', metadata)
    update_home_index(metadata['draft_id'], {'tm_draft_modified': now})
    return {
        'status': 'edited',
        'draft': str(target),
        'segment_id': segment['id'],
        'text': text,
        'backup': str(backup),
        'native_ui_acceptance': 'requires reopen evidence',
    }


def edit_timeline(draft_name, plan_path, force=False):
    runtime.ensure_editor_closed()
    target = runtime.draft_root() / draft_name
    if not target.is_dir():
        raise ValueError('草稿不存在: ' + str(target))
    old_timeline = codec.load_json_file(target / 'draft_info.json')
    old_metadata = codec.load_json_file(target / 'draft_meta_info.json')
    if not force:
        if any(track.get('type') not in {'video', 'audio', 'text'} for track in old_timeline.get('tracks', [])):
            raise ValueError('草稿包含非基础轨道；确认要整体替换请加 --force')
        allowed = {'videos', 'audios', 'texts', 'speeds', 'placeholder_infos', 'canvases',
                   'sound_channel_mappings', 'material_colors', 'vocal_separations',
                   'material_animations', 'beats', 'loudnesses', 'common_mask'}
        extra = sorted(key for key, values in old_timeline.get('materials', {}).items()
                       if values and key not in allowed)
        if extra:
            raise ValueError('草稿包含当前未实现的素材类型 %s；整体替换请加 --force' % extra)

    plan = runtime.read_json(plan_path)
    assets, duration = plan_module.validate(plan)
    now = runtime.now_us()
    for asset in assets.values():
        asset['local_id'] = str(uuid.uuid4())
        asset['relative'] = 'Resources/headless-media/' + asset['sha256'] + Path(asset['source']).suffix.lower()
        destination = target / asset['relative']
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            if runtime.digest(destination) != asset['sha256']:
                raise RuntimeError('草稿内已有同名但内容不同的素材: ' + str(destination))
        else:
            with Path(asset['source']).open('rb') as source, destination.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if runtime.digest(destination) != asset['sha256']:
                raise RuntimeError('素材复制后校验失败: ' + asset['source'])

    resources.prepare(plan, target)
    timeline, registrations = timeline_for(plan, assets, target, old_timeline['id'])
    timeline['id'] = old_timeline['id']
    metadata = old_metadata
    library = [item for group in metadata.get('draft_materials', []) if group.get('type') == 0
               for item in group.get('value', [])]
    known = {item.get('id') for item in library}
    for asset in assets.values():
        if asset['local_id'] not in known:
            library.append(library_record(asset, target, now))
    groups = []
    for group in metadata.get('draft_materials', []):
        if group.get('type') == 0:
            groups.append({'type': 0, 'value': library})
        else:
            groups.append(group)
    if not any(group.get('type') == 0 for group in groups):
        groups.insert(0, {'type': 0, 'value': library})
    metadata.update(draft_materials=groups, tm_draft_modified=now, tm_duration=duration)
    timeline_bytes = codec.encrypt_bytes(runtime.packed(timeline))
    metadata['draft_timeline_materials_size_'] = (sum(asset['size'] for asset in assets.values())
                                                  + len(timeline_bytes))
    mirrors = timeline_mirrors(target, timeline['id'])
    backup = backup_paths(target, mirrors + [target / 'draft_meta_info.json',
                                            target / 'key_value.json',
                                            runtime.draft_root() / 'root_meta_info.json'], 'edit-timeline')
    for path in mirrors:
        path.write_bytes(timeline_bytes)
        os.chmod(path, 0o600)
    runtime.write_json(target / 'key_value.json', registrations)
    video_assets = [asset for asset in assets.values() if asset['media_type'] in {'video', 'photo', 'gif'}]
    if video_assets:
        runtime.make_cover(target / video_assets[0]['relative'], target / 'draft_cover.jpg')
        timeline_dir = target / 'Timelines' / timeline['id']
        shutil.copyfile(target / 'draft_cover.jpg', timeline_dir / 'draft_cover.jpg')
    write_protected_json(target / 'draft_meta_info.json', metadata)
    update_home_index(metadata['draft_id'], {
        'tm_draft_modified': now,
        'draft_timeline_materials_size': metadata['draft_timeline_materials_size_'],
        'draft_cover': str(target / 'draft_cover.jpg'),
        'draft_json_file': str(target / 'draft_info.json'),
    })
    return {
        'status': 'edited',
        'draft': str(target),
        'duration_us': duration,
        'tracks': len(plan['tracks']),
        'media_files': len(assets),
        'backup': str(backup),
        'native_ui_acceptance': 'requires reopen evidence',
    }


def verify_build(build_dir):
    build_dir = Path(build_dir).resolve(strict=True)
    record = runtime.read_json(build_dir / 'build.json')
    plan = runtime.read_json(build_dir / 'plan.json')
    if runtime.digest(build_dir / 'plan.json') != record['plan_sha256']:
        raise ValueError('plan.json 已变化')
    if files_manifest(build_dir / 'draft') != record['files']:
        raise ValueError('draft 文件清单已变化')
    timeline = codec.load_json_file(build_dir / 'draft' / 'draft_info.json')
    metadata = codec.load_json_file(build_dir / 'draft' / 'draft_meta_info.json')
    if timeline['id'] != record['timeline_id'] or metadata['draft_id'] != record['draft_id']:
        raise ValueError('草稿身份不一致')
    if abs(timeline['duration'] - record['duration_us']) > 1:
        raise ValueError('时间线长度不一致')
    if len(timeline.get('tracks', [])) != len(plan['tracks']):
        raise ValueError('轨道数量不一致')
    resources.verify_files(record.get('native_resources', []), build_dir / 'draft', plan)
    return {
        'status': 'verified',
        'name': record['name'],
        'duration_us': record['duration_us'],
        'tracks': [{'type': track['type'], 'segments': len(track['segments'])} for track in timeline['tracks']],
        'media_files': len(record['assets']),
        'encoding': record['encoding'],
        'live_written': False,
    }


def verify_live(build_dir):
    build_dir = Path(build_dir).resolve(strict=True)
    record = runtime.read_json(build_dir / 'build.json')
    target = Path(record['target'])
    if not target.is_dir():
        raise ValueError('草稿目录不存在: ' + str(target))
    root_meta = runtime.draft_root() / 'root_meta_info.json'
    index = runtime.read_json(root_meta)
    entries = [entry for entry in index.get('all_draft_store', [])
               if entry.get('draft_id') == record['draft_id']]
    if len(entries) != 1:
        raise ValueError('首页索引中没有唯一的草稿记录')
    timeline = codec.load_json_file(target / 'draft_info.json')
    metadata = codec.load_json_file(target / 'draft_meta_info.json')
    if timeline.get('id') != record['timeline_id'] or metadata.get('draft_id') != record['draft_id']:
        raise ValueError('live 草稿身份与 build 记录不一致')
    return {
        'status': 'verified-live',
        'draft': str(target),
        'name': record['name'],
        'duration_us': timeline.get('duration', 0),
        'tracks': [{'type': track['type'], 'segments': len(track['segments'])} for track in timeline.get('tracks', [])],
        'registered': True,
        'encoding': 'encrypted',
        'native_ui_acceptance': 'requires open/play/save/cold-reopen evidence',
    }
