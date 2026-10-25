"""跨平台剪映草稿加解密。

macOS：运行时 dlopen 本机 libvideoeditor，调用 lvve::EncryptUtils。
Windows：ctypes 调用本机 videoeditor.dll 的 MSVC 导出符号。
两边都不分发官方二进制。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import media, platform_support, runtime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = HERE / 'codec_dlopen.cpp'
CODEC = ROOT / 'bin' / 'jycodec'
WORK = ROOT / '.work'
APP_FRAMEWORKS = Path('/Applications/VideoFusion-macOS.app/Contents/Frameworks')


def build_codec(force=False):
    if platform_support.IS_MAC:
        if CODEC.is_file() and not SOURCE.is_file():
            return str(CODEC)
        if not SOURCE.is_file():
            raise RuntimeError('缺少 tools/jycodec_dlopen.cpp')
        if not force and CODEC.is_file() and CODEC.stat().st_mtime >= SOURCE.stat().st_mtime:
            return str(CODEC)
        CODEC.parent.mkdir(parents=True, exist_ok=True)
        install_dir = Path(platform_support.app_info()['install_dir'])
        command = ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-O2',
                   '-arch', 'arm64', '-arch', 'x86_64', str(SOURCE),
                   '-Wl,-rpath,' + str(APP_FRAMEWORKS),
                   '-Wl,-rpath,' + str(install_dir), '-o', str(CODEC)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            command = ['/usr/bin/xcrun', 'clang++', '-std=c++17', '-O2', str(SOURCE),
                       '-Wl,-rpath,' + str(APP_FRAMEWORKS),
                       '-Wl,-rpath,' + str(install_dir), '-o', str(CODEC)]
            result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError('jycodec 编译失败:\n' + result.stderr[-3000:])
        os.chmod(CODEC, 0o700)
        return str(CODEC)
    if platform_support.IS_WIN:
        info = platform_support.app_info()
        if not Path(info['editor_library']).is_file():
            raise RuntimeError('缺少 videoeditor.dll')
        return info['editor_library']
    raise RuntimeError('目前只支持 macOS 与 Windows')


def available():
    try:
        build_codec()
        return True
    except Exception:
        return False


def _ensure_work():
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK


def _run_mac(command, payload):
    build_codec()
    work = Path(tempfile.mkdtemp(prefix='codec-', dir=_ensure_work()))
    try:
        source = work / 'source.bin'
        output = work / 'output.bin'
        source.write_bytes(payload)
        os.chmod(source, 0o600)
        result = subprocess.run([str(CODEC), command, str(source), str(output)],
                                cwd=str(ROOT), capture_output=True, text=True,
                                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
        if result.returncode != 0:
            raise RuntimeError('jycodec %s 失败: %s' % (command, result.stderr[-1500:]))
        if not output.is_file():
            raise RuntimeError('jycodec %s 没有产出文件' % command)
        return output.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _StringData:
    pass


def _windows_crypto():
    import ctypes

    decrypt_symbol = (
        '?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@'
        'V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z')
    encrypt_symbol = (
        '?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@'
        'V?$allocator@D@2@@std@@AEBV34@@Z')
    enable_symbol = '?enable@EncryptUtils@lvve@@QEAAX_N@Z'

    class Data(ctypes.Union):
        _fields_ = [('small', ctypes.c_char * 16), ('pointer', ctypes.c_void_p)]

    class MsvcString(ctypes.Structure):
        _fields_ = [('data', Data), ('size', ctypes.c_uint64), ('capacity', ctypes.c_uint64)]

    class Arg:
        def __init__(self, payload):
            self.buffer = None
            self.value = MsvcString()
            self.value.size = len(payload)
            if len(payload) < 16:
                self.value.capacity = 15
                ctypes.memset(ctypes.addressof(self.value.data), 0, 16)
                if payload:
                    ctypes.memmove(ctypes.addressof(self.value.data), payload, len(payload))
            else:
                self.buffer = ctypes.create_string_buffer(payload)
                self.value.capacity = len(payload)
                self.value.data.pointer = ctypes.cast(self.buffer, ctypes.c_void_p).value

    info = platform_support.app_info()
    install_dir = Path(info['install_dir'])
    dll_path = Path(info['editor_library'])
    cookie = os.add_dll_directory(str(install_dir)) if hasattr(os, 'add_dll_directory') else None
    old_path = os.environ.get('PATH', '')
    os.environ['PATH'] = str(install_dir) + os.pathsep + old_path
    try:
        dll = ctypes.WinDLL(str(dll_path), winmode=0x100 | 0x400 | 0x800, use_last_error=True)
    finally:
        os.environ['PATH'] = old_path
    winfunctype = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)
    decrypt_fn = winfunctype(ctypes.POINTER(MsvcString), ctypes.c_void_p, ctypes.POINTER(MsvcString),
                             ctypes.POINTER(MsvcString), ctypes.POINTER(MsvcString),
                             ctypes.POINTER(ctypes.c_bool))(ctypes.cast(dll[decrypt_symbol], ctypes.c_void_p).value)
    encrypt_fn = winfunctype(ctypes.POINTER(MsvcString), ctypes.c_void_p, ctypes.POINTER(MsvcString),
                             ctypes.POINTER(MsvcString))(ctypes.cast(dll[encrypt_symbol], ctypes.c_void_p).value)
    enable_fn = winfunctype(None, ctypes.c_void_p, ctypes.c_bool)(
        ctypes.cast(dll[enable_symbol], ctypes.c_void_p).value)

    def bytes_of(value):
        size = int(value.size)
        address = ctypes.addressof(value.data) if value.capacity < 16 else value.data.pointer
        if size > 512 * 1024 * 1024 or (size and not address):
            raise RuntimeError('剪映 DLL 返回了异常字符串')
        return ctypes.string_at(address, size)

    def encrypt(payload):
        enable_fn(None, True)
        source, output = Arg(payload), MsvcString()
        encrypt_fn(None, ctypes.byref(output), ctypes.byref(source.value))
        result = bytes_of(output)
        if not result or decrypt(result) != payload:
            raise RuntimeError('草稿加密回环校验失败')
        return result

    def decrypt(payload):
        source, parameters = Arg(payload), Arg(b'{}')
        output, ok = MsvcString(), ctypes.c_bool(False)
        decrypt_fn(None, ctypes.byref(output), ctypes.byref(source.value),
                   ctypes.byref(parameters.value), ctypes.byref(ok))
        result = bytes_of(output)
        if not ok.value or not result:
            raise RuntimeError('草稿解密失败')
        return result

    return encrypt, decrypt


_WINDOWS = None


def encrypt_bytes(payload):
    if not payload:
        raise ValueError('拒绝加密空内容')
    if platform_support.IS_WIN:
        global _WINDOWS
        if _WINDOWS is None:
            _WINDOWS = _windows_crypto()
        return _WINDOWS[0](payload)
    return _run_mac('encrypt', payload)


def decrypt_bytes(payload):
    if not payload:
        raise ValueError('拒绝解密空内容')
    if platform_support.IS_WIN:
        global _WINDOWS
        if _WINDOWS is None:
            _WINDOWS = _windows_crypto()
        return _WINDOWS[1](payload)
    return _run_mac('decrypt', payload)


def encrypt_json(value):
    return encrypt_bytes(runtime.packed(value))


def decrypt_json(payload):
    raw = decrypt_bytes(payload)
    if not raw:
        raise RuntimeError('解密结果为空（输入可能不是剪映加密 JSON）')
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError('解密结果不是 UTF-8 JSON') from error


def load_json_file(path):
    payload = Path(path).read_bytes()
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return decrypt_json(payload)


def self_test():
    probe = {'schema': 'jydraft-codec-selftest/v1', 'text': '中文' * 120, 'n': 1}
    payload = runtime.packed(probe)
    cipher = encrypt_bytes(payload)
    if decrypt_bytes(cipher) != payload:
        raise RuntimeError('加密/解密往返不一致')
    return {'status': 'ok', 'plain_bytes': len(payload), 'cipher_bytes': len(cipher),
            'platform': 'windows' if platform_support.IS_WIN else 'macos'}
