"""运行环境、路径与通用 JSON 工具。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import media, platform_support

ROOT = Path(__file__).resolve().parent.parent
APP = platform_support.MAC_APP
BUNDLE_ID = platform_support.BUNDLE_ID
MICROS = 1_000_000


def packed(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def app_info():
    return platform_support.app_info()


def draft_root():
    return platform_support.draft_root()


def editor_running():
    return platform_support.editor_running()


def ensure_editor_closed():
    platform_support.ensure_editor_closed()


def macos_version():
    return platform_support.system_version()


def probe(path):
    return media.probe(path)


def make_cover(asset_path, output):
    return media.make_cover(asset_path, output)


def make_blank_cover(output):
    return media.make_blank_cover(output)


def make_text_image(text, output, size, color, border_color, border_width, font=None):
    return media.text_image(text, output, size, color, border_color, border_width, font)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packed(value))


def read_json(path):
    return json.loads(Path(path).read_bytes())


def now_us():
    return time.time_ns() // 1000
