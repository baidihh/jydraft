"""jydraft 命令行入口。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from . import auto as auto_module, analyze as analyze_module, codec, draft, export as export_module, media, platform_support, runtime

PROJECT = Path(__file__).resolve().parent.parent


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def doctor(args):
    info = runtime.app_info()
    errors = []
    if info['bundle_id'] != runtime.BUNDLE_ID:
        errors.append('未找到剪映专业版（bundle id 不符）')
    if args.build_helper:
        try:
            codec.build_codec(force=True)
        except Exception as error:
            errors.append(str(error))
    codec_path = None
    try:
        codec_path = codec.build_codec()
    except Exception as error:
        errors.append('加解密不可用: %s' % error)
    ffmpeg = ffprobe = None
    try:
        ffmpeg = media.ffmpeg()
        ffprobe = media.ffprobe()
    except Exception as error:
        errors.append(str(error))
    result = {
        'status': 'ok' if not errors else 'error',
        'app': info,
        'draft_root': str(runtime.draft_root()),
        'draft_root_exists': runtime.draft_root().is_dir(),
        'editor_running': runtime.editor_running(),
        'ffmpeg': str(ffmpeg) if ffmpeg else None,
        'ffprobe': str(ffprobe) if ffprobe else None,
        'codec': codec_path,
        'encoding': 'encrypted',
        'platform': 'windows' if platform_support.IS_WIN else 'macos',
        'errors': errors,
    }
    print_json(result)
    return 1 if errors else 0


def build(args):
    plan = json.load(sys.stdin) if args.plan == '-' else args.plan
    print_json(draft.build(plan, args.out, plaintext=args.plaintext))
    return 0


def publish(args):
    print_json(draft.publish(args.build, args.audit))
    return 0


def create(args):
    plan = json.load(sys.stdin) if args.plan == '-' else args.plan
    work = draft.fresh_directory(args.work)
    built = draft.build(plan, work / 'build', plaintext=args.plaintext)
    published = draft.publish(work / 'build', work / 'audit')
    print_json({'build': built, 'publish': published})
    return 0


def verify_build(args):
    print_json(draft.verify_build(args.build))
    return 0


def verify_live(args):
    print_json(draft.verify_live(args.build))
    return 0


def sample(args):
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    media.sample(output, args.seconds)
    print_json({'status': 'created', 'path': str(output)})
    return 0


def demo(args):
    work = draft.fresh_directory(args.work)
    clip = work / 'media.mp4'
    media.sample(clip, 3)
    plan = {
        'schema': draft.SCHEMA,
        'name': args.name,
        'canvas': {'width': 1280, 'height': 720, 'fps': 30},
        'tracks': [
            {'type': 'video', 'name': '主视频', 'segments': [
                {'source': str(clip), 'start_us': 0, 'duration_us': 3_000_000},
            ]},
            {'type': 'text', 'name': '字幕', 'segments': [
                {'text': 'jydraft 生成的草稿', 'start_us': 0, 'duration_us': 3_000_000,
                 'size': 8, 'y': -0.78, 'color': '#FFFFFF', 'border_color': '#000000', 'border_width': 0.05},
            ]},
        ],
    }
    plan_path = work / 'plan.json'
    runtime.write_json(plan_path, plan)
    built = draft.build(plan_path, work / 'build', plaintext=args.plaintext)
    result = {'build': built}
    if args.publish:
        result['publish'] = draft.publish(work / 'build', work / 'audit')
    print_json(result)
    return 0


def selftest(args):
    print_json(codec.self_test())
    return 0


def decode(args):
    value = codec.load_json_file(args.file)
    if args.out:
        runtime.write_json(args.out, value)
        print_json({'status': 'decoded', 'file': str(Path(args.file).resolve()), 'out': str(Path(args.out).resolve())})
    else:
        print_json(value)
    return 0


def inspect_draft(args):
    folder = runtime.draft_root() / args.draft
    if not folder.is_dir():
        raise ValueError('草稿不存在: ' + str(folder))
    timeline = codec.load_json_file(folder / 'draft_info.json')
    metadata = codec.load_json_file(folder / 'draft_meta_info.json')
    tracks = []
    for track in timeline.get('tracks', []):
        segments = []
        for segment in track.get('segments', []):
            item = {
                'start_us': segment.get('target_timerange', {}).get('start', 0),
                'duration_us': segment.get('target_timerange', {}).get('duration', 0),
                'material_id': segment.get('material_id'),
            }
            material = None
            for bucket, values in timeline.get('materials', {}).items():
                for candidate in values:
                    if candidate.get('id') == item['material_id']:
                        material = (bucket, candidate)
            if material:
                bucket, candidate = material
                item['bucket'] = bucket
                if bucket == 'texts':
                    item['text'] = json.loads(candidate['content']).get('text')
                elif bucket in {'videos', 'audios'}:
                    item['source'] = candidate.get('path')
                    item['media_type'] = candidate.get('type')
            segments.append(item)
        tracks.append({'type': track['type'], 'name': track.get('name'), 'segments': segments})
    print_json({
        'draft': str(folder),
        'name': metadata.get('draft_name'),
        'draft_id': metadata.get('draft_id'),
        'timeline_id': timeline.get('id'),
        'duration_us': timeline.get('duration'),
        'canvas': timeline.get('canvas_config'),
        'fps': timeline.get('fps'),
        'new_version': timeline.get('new_version'),
        'platform': timeline.get('platform'),
        'tracks': tracks,
        'material_counts': {key: len(value) for key, value in timeline.get('materials', {}).items() if value},
    })
    return 0


def edit_text(args):
    print_json(draft.edit_text(args.draft, args.text, index=args.index, old=args.old))
    return 0


def edit_timeline(args):
    print_json(draft.edit_timeline(args.draft, args.plan, force=args.force))
    return 0


def export_cmd(args):
    print_json(export_module.export(args.build, args.out, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                                     font=args.font, crf=args.crf, preset=args.preset,
                                     timeout=args.timeout))
    return 0


def scan_cmd(args):
    media = auto_module.scan(args.input)
    print_json({
        'root': media['root'],
        'videos': [{'path': item['source'], 'duration_us': item['duration_us'],
                    'width': item['width'], 'height': item['height'],
                    'cuts': len(item.get('cuts', []))} for item in media['videos']],
        'images': [{'path': item['source'], 'width': item['width'], 'height': item['height']}
                   for item in media['images']],
        'audios': [{'path': item['source'], 'duration_us': item['duration_us']}
                   for item in media['audios']],
        'subtitles': [str(path) for path in media['subtitles']],
    })
    return 0


def analyze_cmd(args):
    print_json(analyze_module.report(analyze_module.analyze(
        args.input, args.work, frames=args.frames, frame_width=args.frame_width,
        audio_sample=args.audio_sample, with_audio=not args.no_audio)))
    return 0


def auto_cmd(args):
    print_json(auto_module.auto(args.input, args.brief, args.work, name=args.name,
                                title=args.title, publish=args.publish, do_export=args.export,
                                dry_run=args.dry_run))
    return 0


def app_cmd(args):
    from .web import main as web_main
    web_main()
    return 0


def capabilities(args):
    print_json({
        'name': 'jydraft',
        'version': __version__,
        'platform': 'windows' if platform_support.IS_WIN else 'macos',
        'json_output': True,
        'requires': {
            'jianying': '已安装（macOS 11.4+ / Windows 11.5+）',
            'ffmpeg': '发布包内置，或 PATH 中的 ffmpeg/ffprobe',
            'python': '仅源码运行时需要 3.9+',
        },
        'commands': {
            'doctor': {'usage': 'jydraft doctor [--build-helper]', 'returns': '环境检查'},
            'analyze': {
                'usage': 'jydraft analyze --input DIR --work NEW_DIR [--frames N] [--frame-width N] [--no-audio]',
                'returns': 'evidence.json：镜头、代表帧、音量、字幕',
            },
            'scan': {'usage': 'jydraft scan --input DIR', 'returns': '视频/图片/音频/字幕与镜头切点'},
            'auto': {
                'usage': 'jydraft auto --input DIR --brief TEXT --work NEW_DIR [--name NAME] [--publish] [--export] [--dry-run]',
                'returns': '计划、草稿、发布、导出结果',
                'brief_keywords': ['20秒', '1分钟', '快节奏', '卡点', '慢', 'Vlog', '竖屏', '9:16',
                                   '横屏', '16:9', '静音', '配乐', 'BGM', '字幕'],
            },
            'build': {'usage': 'jydraft build --plan FILE|- --out NEW_DIR [--plaintext]', 'returns': '离线 build'},
            'publish': {'usage': 'jydraft publish --build DIR --audit NEW_DIR', 'returns': '首页登记（需退出剪映）'},
            'create': {'usage': 'jydraft create --plan FILE|- --work NEW_DIR [--plaintext]', 'returns': 'build + publish'},
            'verify-build': {'usage': 'jydraft verify-build --build DIR'},
            'verify-live': {'usage': 'jydraft verify-live --build DIR'},
            'inspect': {'usage': 'jydraft inspect --draft NAME', 'returns': '加密草稿摘要'},
            'decode': {'usage': 'jydraft decode --file PATH [--out PATH]'},
            'edit-text': {'usage': 'jydraft edit-text --draft NAME --text TEXT (--index N|--old TEXT)'},
            'edit-timeline': {'usage': 'jydraft edit-timeline --draft NAME --plan FILE [--force]'},
            'export': {'usage': 'jydraft export --build DIR --out NEW_DIR [--crf N] [--preset NAME]'},
            'sample': {'usage': 'jydraft sample --out FILE [--seconds N]'},
            'selftest': {'usage': 'jydraft selftest'},
            'schema': {'usage': 'jydraft schema', 'returns': '计划 JSON Schema'},
            'app': {'usage': 'jydraft app', 'returns': '可选的本地网页界面'},
        },
        'plan_schema': 'jy14-headless-plan/v1',
        'evidence_schema': 'jy14-evidence/v1',
        'executors': {
            'draft': {
                'command': 'jydraft build / publish',
                'output': '剪映可编辑草稿',
                'supports': ['video', 'audio', 'text', 'speed', 'volume', 'pip', 'keyframes',
                             'mask', 'opacity', 'rotation'],
            },
            'ffmpeg': {
                'command': 'jydraft export',
                'output': 'MP4',
                'supports': ['video', 'audio', 'text', 'speed', 'volume', 'pip'],
                'degraded': {
                    'mask': '原生蒙版不参与导出',
                    'keyframes': '使用片段首帧静态值',
                    'rotation': '不参与导出',
                    'opacity': '不参与导出',
                    'transition': '不参与导出',
                },
            },
            'native': {
                'status': 'optional',
                'note': '剪映原生导出需要 11.4.2 / 11.5.0 的匹配配置；11.6 暂无无界面导出 ABI。',
            },
        },
        'agent_flow': [
            'jydraft analyze --input DIR --work W',
            '读取 W/evidence.json 与代表帧，产出 jy14-headless-plan/v1',
            'jydraft build --plan PLAN.json --out W/build',
            'jydraft export --build W/build --out W/export',
        ],
        'exit_codes': {'0': '成功', '1': 'doctor 检查失败', '2': '参数/运行错误'},
        'notes': [
            '所有命令把结果 JSON 输出到 stdout，错误输出到 stderr。',
            'publish / edit-* 前必须完全退出剪映。',
            'auto 是规则驱动的兜底粗剪；需要创作意图时，让 Agent 读 analyze 证据包后自己生成 plan。',
            'export 报告的 degraded 字段列出 FFmpeg 无法还原、需要剪映原生引擎的能力。',
        ],
    })
    return 0


def schema(args):
    segment = {
        'type': 'object',
        'properties': {
            'start_us': {'type': 'integer', 'minimum': 0},
            'duration_us': {'type': 'integer', 'minimum': 1},
            'source': {'type': 'string', 'description': '素材绝对路径（video/audio 必填）'},
            'source_start_us': {'type': 'integer', 'minimum': 0},
            'source_duration_us': {'type': 'integer', 'minimum': 1},
            'speed': {'type': 'number', 'minimum': 0.1, 'maximum': 8},
            'volume': {'type': 'number', 'minimum': 0, 'maximum': 4},
            'text': {'type': 'string'},
            'size': {'type': 'number', 'minimum': 1, 'maximum': 100},
            'x': {'type': 'number', 'minimum': -5, 'maximum': 5},
            'y': {'type': 'number', 'minimum': -5, 'maximum': 5},
            'color': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
            'border_color': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
            'border_width': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'scale': {'type': 'number', 'minimum': 0.01, 'maximum': 10},
            'rotation': {'type': 'number', 'minimum': -360, 'maximum': 360},
            'opacity': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'mask': {
                'type': 'object',
                'required': ['shape'],
                'properties': {
                    'shape': {'enum': ['circle', 'rectangle', 'line', 'mirror', 'star', 'heart']},
                    'width': {'type': 'number'}, 'height': {'type': 'number'},
                    'x': {'type': 'number'}, 'y': {'type': 'number'},
                    'rotation': {'type': 'number'}, 'feather': {'type': 'number'},
                    'invert': {'type': 'boolean'}, 'round_corner': {'type': 'number'},
                },
            },
            'keyframes': {
                'type': 'object',
                'additionalProperties': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'required': ['at_us', 'value'],
                        'properties': {'at_us': {'type': 'integer'}, 'value': {'type': 'number'}},
                    },
                },
            },
        },
    }
    print_json({
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'jy14-headless-plan/v1',
        'type': 'object',
        'additionalProperties': False,
        'required': ['schema', 'name', 'canvas', 'tracks'],
        'properties': {
            'schema': {'const': 'jy14-headless-plan/v1'},
            'name': {'type': 'string'},
            'canvas': {
                'type': 'object',
                'required': ['width', 'height', 'fps'],
                'properties': {
                    'width': {'type': 'integer', 'minimum': 16, 'maximum': 8192},
                    'height': {'type': 'integer', 'minimum': 16, 'maximum': 8192},
                    'fps': {'enum': [24, 25, 30, 50, 60]},
                },
            },
            'tracks': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'required': ['type', 'segments'],
                    'properties': {
                        'type': {'enum': ['video', 'audio', 'text']},
                        'name': {'type': 'string'},
                        'segments': {'type': 'array', 'items': segment},
                    },
                },
            },
        },
    })
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='jydraft', description='剪映本地自动剪辑 CLI')
    parser.add_argument('--version', action='version', version='jydraft ' + __version__)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('doctor', help='检查本机剪映、草稿目录与工具链')
    p.add_argument('--build-helper', action='store_true', help='重新编译 bin/jymedia')
    p.set_defaults(func=doctor)

    p = sub.add_parser('build', help='按计划生成草稿（不写首页索引）')
    p.add_argument('--plan', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--plaintext', action='store_true', help='写明文 JSON（调试用，默认写原生加密格式）')
    p.set_defaults(func=build)

    p = sub.add_parser('publish', help='把 build 登记到剪映首页（需先退出剪映）')
    p.add_argument('--build', required=True)
    p.add_argument('--audit', required=True)
    p.set_defaults(func=publish)

    p = sub.add_parser('create', help='build + publish')
    p.add_argument('--plan', required=True)
    p.add_argument('--work', required=True)
    p.add_argument('--plaintext', action='store_true')
    p.set_defaults(func=create)

    p = sub.add_parser('verify-build', help='校验离线 build')
    p.add_argument('--build', required=True)
    p.set_defaults(func=verify_build)

    p = sub.add_parser('verify-live', help='校验首页登记与加密草稿')
    p.add_argument('--build', required=True)
    p.set_defaults(func=verify_live)

    p = sub.add_parser('sample', help='生成一段合成测试视频')
    p.add_argument('--out', required=True)
    p.add_argument('--seconds', type=float, default=3.0)
    p.set_defaults(func=sample)

    p = sub.add_parser('demo', help='一键生成合成素材、计划与 build')
    p.add_argument('--work', required=True)
    p.add_argument('--name', default='jydraft-demo')
    p.add_argument('--publish', action='store_true', help='同时登记到剪映首页（需先退出剪映）')
    p.add_argument('--plaintext', action='store_true')
    p.set_defaults(func=demo)

    p = sub.add_parser('selftest', help='验证原生加密/解密桥接')
    p.set_defaults(func=selftest)

    p = sub.add_parser('decode', help='把明文/加密草稿 JSON 解码为明文')
    p.add_argument('--file', required=True)
    p.add_argument('--out')
    p.set_defaults(func=decode)

    p = sub.add_parser('inspect', help='读取并汇总一个已存在草稿（支持加密）')
    p.add_argument('--draft', required=True, help='草稿目录名')
    p.set_defaults(func=inspect_draft)

    p = sub.add_parser('edit-text', help='修改已有草稿里的文字片段（需先退出剪映）')
    p.add_argument('--draft', required=True)
    p.add_argument('--text', required=True)
    p.add_argument('--index', type=int, help='文字片段序号，从 0 开始')
    p.add_argument('--old', help='按旧文本匹配要修改的片段')
    p.set_defaults(func=edit_text)

    p = sub.add_parser('edit-timeline', help='用新计划整体替换已有草稿的时间线（需先退出剪映）')
    p.add_argument('--draft', required=True)
    p.add_argument('--plan', required=True)
    p.add_argument('--force', action='store_true', help='允许丢弃蒙版/转场/特效等未实现素材')
    p.set_defaults(func=edit_timeline)

    p = sub.add_parser('export', help='用 FFmpeg 从 build 无头导出 MP4')
    p.add_argument('--build', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--ffmpeg')
    p.add_argument('--ffprobe')
    p.add_argument('--font', help='文字字体 TTF/OTF/TTC；默认系统字体')
    p.add_argument('--crf', type=int, default=18)
    p.add_argument('--preset', default='medium')
    p.add_argument('--timeout', type=int, default=900)
    p.set_defaults(func=export_cmd)

    p = sub.add_parser('analyze', help='把素材变成 AI 可读的证据包（镜头、抽帧、音量、字幕）')
    p.add_argument('--input', required=True, help='素材文件夹')
    p.add_argument('--work', required=True, help='新的输出目录')
    p.add_argument('--frames', type=int, default=4, help='每个视频最多抽取几个镜头代表帧')
    p.add_argument('--frame-width', type=int, default=480, help='代表帧宽度')
    p.add_argument('--audio-sample', type=float, default=120.0, help='音量统计抽样秒数，0 表示不抽样')
    p.add_argument('--no-audio', action='store_true', help='跳过音量统计')
    p.set_defaults(func=analyze_cmd)

    p = sub.add_parser('scan', help='扫描素材目录，输出可用素材与镜头切点')
    p.add_argument('--input', required=True)
    p.set_defaults(func=scan_cmd)

    p = sub.add_parser('auto', help='一句话 + 素材目录，自动粗剪并生成草稿')
    p.add_argument('--input', required=True, help='素材文件夹')
    p.add_argument('--brief', default='', help='例如“30秒快节奏竖屏，配乐，带字幕”')
    p.add_argument('--work', required=True)
    p.add_argument('--name')
    p.add_argument('--title')
    p.add_argument('--publish', action='store_true', help='登记到剪映首页（需先退出剪映）')
    p.add_argument('--export', action='store_true', help='同时导出 MP4')
    p.add_argument('--dry-run', action='store_true', help='只输出计划，不写草稿')
    p.set_defaults(func=auto_cmd)

    p = sub.add_parser('capabilities', help='输出机器可读的命令与能力清单')
    p.set_defaults(func=capabilities)

    p = sub.add_parser('schema', help='输出剪辑计划 JSON Schema')
    p.set_defaults(func=schema)

    p = sub.add_parser('app', help='打开可选的本地网页界面')
    p.set_defaults(func=app_cmd)

    p = sub.add_parser('gui', help=argparse.SUPPRESS)
    p.set_defaults(func=app_cmd)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as error:
        print('错误: %s' % error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
