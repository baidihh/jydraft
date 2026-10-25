from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from jydraft import analyze, auto, codec, draft, export, media, resources, runtime

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / '.work' / 'tests'


def has_app():
    try:
        runtime.app_info()
        return True
    except Exception:
        return False


def make_video(path):
    media.sample(path, 2)


@unittest.skipUnless(has_app(), '需要本机安装剪映')
class CodecTest(unittest.TestCase):
    def test_round_trip(self):
        result = codec.self_test()
        self.assertEqual(result['status'], 'ok')
        self.assertGreater(result['cipher_bytes'], result['plain_bytes'])


@unittest.skipUnless(has_app(), '需要本机安装剪映')
class BuildExportTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.base = Path(tempfile.mkdtemp(prefix='case-', dir=WORK))
        self.video = self.base / 'input.mp4'
        make_video(self.video)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def plan(self, name, mask=None):
        segment = {'source': str(self.video), 'start_us': 0, 'duration_us': 2_000_000}
        if mask:
            segment['mask'] = mask
        return {
            'schema': 'jy14-headless-plan/v1',
            'name': name,
            'canvas': {'width': 640, 'height': 360, 'fps': 30},
            'tracks': [
                {'type': 'video', 'name': '主视频', 'segments': [segment]},
                {'type': 'text', 'name': '字幕', 'segments': [
                    {'text': '自动化测试字幕', 'start_us': 0, 'duration_us': 2_000_000, 'size': 8},
                ]},
            ],
        }

    def build(self, plan, suffix):
        plan_path = self.base / ('plan-' + suffix + '.json')
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        out = self.base / ('build-' + suffix)
        return plan_path, out, draft.build(plan_path, out)

    def test_encrypted_build_and_verify(self):
        plan_path, out, result = self.build(self.plan('jydraft-test-enc'), 'enc')
        self.assertEqual(result['status'], 'built')
        verified = draft.verify_build(out)
        self.assertEqual(verified['encoding'], 'encrypted-v1')
        timeline = codec.load_json_file(out / 'draft' / 'draft_info.json')
        self.assertEqual(timeline['new_version'], '189.0.0')
        self.assertEqual(len(timeline['tracks']), 2)

    def test_mask_build(self):
        try:
            source = Path(resources.definition('mask/circle')['source'])
            if not source.is_dir():
                self.skipTest('本机没有圆形蒙版缓存')
        except Exception:
            self.skipTest('本机没有圆形蒙版缓存')
        plan_path, out, result = self.build(
            self.plan('jydraft-test-mask', {'shape': 'circle', 'width': .35, 'height': .35, 'feather': .08}), 'mask')
        self.assertEqual(result['status'], 'built')
        timeline = codec.load_json_file(out / 'draft' / 'draft_info.json')
        masks = timeline['materials'].get('common_mask', [])
        self.assertEqual(len(masks), 1)
        self.assertTrue(timeline['tracks'][0]['segments'][0]['enable_video_mask'])
        native = out / 'draft' / 'Resources' / 'headless-native'
        self.assertTrue(native.is_dir())
        self.assertTrue(any(path.is_file() for path in native.rglob('*')))

    def test_headless_export(self):
        plan_path, out, result = self.build(self.plan('jydraft-test-export'), 'export')
        report = export.export(out, self.base / 'export-out', crf=28, preset='ultrafast')
        self.assertEqual(report['status'], 'exported')
        self.assertEqual(report['stats']['video_codec'], 'h264')
        self.assertEqual(report['stats']['frames'], 60)
        self.assertTrue(Path(report['output']).is_file())


def has_ffmpeg():
    try:
        media.ffmpeg()
        return True
    except Exception:
        return False


@unittest.skipUnless(has_ffmpeg(), '需要 ffmpeg')
class AnalyzeTest(unittest.TestCase):
    def test_evidence_pack(self):
        WORK.mkdir(parents=True, exist_ok=True)
        base = Path(tempfile.mkdtemp(prefix='analyze-', dir=WORK))
        try:
            media.sample(base / 'clip.mp4', 2)
            result = analyze.analyze(base, base / 'work', frames=2, audio_sample=2)
            self.assertEqual(result['status'], 'analyzed')
            self.assertEqual(result['summary']['videos'], 1)
            self.assertGreaterEqual(result['summary']['frames'], 1)
            evidence = json.loads(Path(result['evidence']).read_text(encoding='utf-8'))
            self.assertEqual(evidence['schema'], 'jy14-evidence/v1')
            video = evidence['videos'][0]
            self.assertGreaterEqual(len(video['shots']), 1)
            self.assertTrue(Path(video['shots'][0]['frame']).is_file())
            self.assertIn('mean_volume_db', video.get('audio', {}))
        finally:
            shutil.rmtree(base, ignore_errors=True)


class DegradeTest(unittest.TestCase):
    def test_report_lists_unsupported_features(self):
        timeline = {'tracks': [{'type': 'video', 'segments': [
            {'enable_video_mask': True, 'common_keyframes': [{}],
             'clip': {'rotation': 12, 'alpha': 0.5}},
        ]}]}
        found = {item['feature']: item['segments'] for item in export.degradations(timeline)}
        self.assertEqual(found, {'mask': 1, 'keyframes': 1, 'rotation': 1, 'opacity': 1})


class AutoTest(unittest.TestCase):
    def test_brief_and_plan(self):
        options = auto.parse_brief('10秒快节奏竖屏，静音')
        self.assertEqual(options['duration_us'], 10_000_000)
        self.assertEqual(options['pace_us'], 2_000_000)
        self.assertTrue(options['vertical'])
        self.assertTrue(options['mute'])
        media = {
            'root': '/tmp/materials',
            'videos': [{'source': '/tmp/a.mp4', 'duration_us': 8_000_000, 'cuts': [0.0, 4.0],
                        'media_type': 'video', 'has_audio': True, 'width': 1280, 'height': 720}],
            'images': [],
            'audios': [],
            'subtitles': [],
        }
        plan, summary = auto.build_plan(media, options, 'auto-test', '标题')
        self.assertEqual(summary['duration_us'], 10_000_000)
        self.assertEqual(plan['canvas'], {'width': 1080, 'height': 1920, 'fps': 30})
        self.assertEqual(plan['tracks'][0]['segments'][0]['start_us'], 0)
        self.assertEqual(plan['tracks'][0]['segments'][0]['volume'], 0.0)
        self.assertEqual(plan['tracks'][-1]['type'], 'text')


if __name__ == '__main__':
    unittest.main()
