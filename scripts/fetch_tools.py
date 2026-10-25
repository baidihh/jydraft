#!/usr/bin/env python3
"""把 npm 的静态 ffmpeg/ffprobe 复制到 bin/，供打包和本地运行使用。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin'


def node(expression):
    result = subprocess.run(['node', '-p', expression], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit('无法解析 %s:\n%s' % (expression, result.stderr))
    return Path(result.stdout.strip())


def main():
    BIN.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if sys.platform == 'win32' else ''
    for name, expression in (
        ('ffmpeg', "require('ffmpeg-static')"),
        ('ffprobe', "require('ffprobe-static').path"),
    ):
        source = node(expression)
        if not source.is_file():
            raise SystemExit('找不到 %s: %s' % (name, source))
        target = BIN / (name + suffix)
        shutil.copyfile(source, target)
        os.chmod(target, 0o755)
        print('%s -> %s' % (source, target))


if __name__ == '__main__':
    main()
