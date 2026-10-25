"""跨平台环境探测：剪映安装、草稿根目录、进程状态。"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform == 'win32'
MAC_APP = Path('/Applications/VideoFusion-macOS.app')
BUNDLE_ID = 'com.lemon.lvpro'


def require(value, message):
    if not value:
        raise ValueError(message)


def mac_app_info():
    plist = MAC_APP / 'Contents' / 'Info.plist'
    require(plist.is_file(), '未找到剪映专业版: ' + str(MAC_APP))
    data = plistlib.loads(plist.read_bytes())
    return {
        'platform': 'macos',
        'path': str(MAC_APP),
        'install_dir': str(MAC_APP / 'Contents' / 'Frameworks'),
        'executable': str(MAC_APP / 'Contents' / 'MacOS' / 'VideoFusion-macOS'),
        'bundle_id': data.get('CFBundleIdentifier'),
        'version': data.get('CFBundleShortVersionString'),
        'build': data.get('CFBundleVersion'),
        'editor_library': str(MAC_APP / 'Contents' / 'Frameworks' / 'libvideoeditor.dylib'),
    }


def windows_candidates():
    result = []
    explicit = os.environ.get('JIANYING_INSTALL_DIR')
    if explicit:
        result.append(Path(explicit).expanduser())
    local = os.environ.get('LOCALAPPDATA')
    if local:
        apps = Path(local) / 'JianyingPro' / 'Apps'
        if apps.is_dir():
            for child in apps.iterdir():
                if child.is_dir():
                    result.append(child)
    for base in ('C:/Program Files/JianyingPro', 'C:/Program Files (x86)/JianyingPro'):
        path = Path(base)
        if path.is_dir():
            result.append(path)
    return result


def windows_app_info():
    candidates = windows_candidates()
    valid = []
    for directory in candidates:
        dll = directory / 'videoeditor.dll'
        exe = directory / 'JianyingPro.exe'
        if dll.is_file() and exe.is_file():
            valid.append((directory, dll, exe))
    require(valid, '未找到剪映专业版（需要 videoeditor.dll 和 JianyingPro.exe）')
    directory, dll, exe = sorted(valid, key=lambda item: str(item[0]), reverse=True)[0]
    version = directory.name if re.fullmatch(r'\d+(\.\d+)+', directory.name) else 'unknown'
    return {
        'platform': 'windows',
        'path': str(directory),
        'install_dir': str(directory),
        'executable': str(exe),
        'bundle_id': 'com.lemon.lvpro',
        'version': version,
        'build': version,
        'editor_library': str(dll),
    }


def app_info():
    if IS_MAC:
        return mac_app_info()
    if IS_WIN:
        return windows_app_info()
    raise RuntimeError('目前只支持 macOS 与 64 位 Windows')


def mac_draft_root(info):
    setting = Path.home() / 'Movies/JianyingPro/User Data/Config/globalSetting'
    if setting.is_file():
        for line in setting.read_text(errors='ignore').splitlines():
            if line.startswith('currentCustomDraftPath='):
                value = line.split('=', 1)[1].strip()
                if value:
                    return Path(value)
    return Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'


def windows_draft_root():
    explicit = os.environ.get('JIANYING_DRAFT_ROOT')
    if explicit:
        return Path(explicit).expanduser()
    local = os.environ.get('LOCALAPPDATA')
    require(local, '缺少 LOCALAPPDATA')
    return Path(local) / 'JianyingPro' / 'User Data' / 'Projects' / 'com.lveditor.draft'


def draft_root(info=None):
    if IS_MAC:
        return mac_draft_root(info or app_info())
    if IS_WIN:
        return windows_draft_root()
    raise RuntimeError('目前只支持 macOS 与 64 位 Windows')


def editor_running(info=None):
    info = info or app_info()
    executable = Path(info['executable'])
    if IS_MAC:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=15)
        for line in result.stdout.splitlines():
            _, _, command = line.strip().partition(' ')
            if command == str(executable) or command.startswith(str(executable) + ' '):
                return True
        return False
    if IS_WIN:
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq JianyingPro.exe', '/NH'],
                                capture_output=True, text=True, timeout=15)
        return 'JianyingPro.exe' in result.stdout
    return False


def ensure_editor_closed(info=None):
    if editor_running(info):
        raise RuntimeError('剪映仍在运行。publish / edit 前请完整退出剪映。')


def quit_editor(info=None):
    """尝试优雅退出剪映；返回是否已经退出。"""
    import time
    info = info or app_info()
    if IS_MAC:
        subprocess.run(['/usr/bin/osascript', '-e', 'tell application id "com.lemon.lvpro" to quit'],
                       capture_output=True, timeout=30)
    elif IS_WIN:
        subprocess.run(['taskkill', '/IM', 'JianyingPro.exe'], capture_output=True, timeout=30)
    for _ in range(20):
        if not editor_running(info):
            return True
        time.sleep(0.5)
    return not editor_running(info)


def open_path(path):
    if IS_MAC:
        subprocess.Popen(['/usr/bin/open', str(path)])
    elif IS_WIN:
        os.startfile(str(path))
    else:
        raise RuntimeError('不支持的系统')


def launch_editor(info=None):
    info = info or app_info()
    if IS_MAC:
        subprocess.Popen(['/usr/bin/open', '-a', info['path']])
    elif IS_WIN:
        subprocess.Popen([info['executable']])
    else:
        raise RuntimeError('不支持的系统')


def system_version():
    if IS_MAC:
        import platform
        return platform.mac_ver()[0] or '26.0'
    if IS_WIN:
        import platform
        return platform.version() or '10.0'
    return 'unknown'
