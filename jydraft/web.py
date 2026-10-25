"""本地 Web 桌面应用：启动 127.0.0.1 服务并用应用窗口打开。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auto, codec, draft, export as export_module, media, platform_support, runtime

HERE = Path(__file__).resolve().parent
INDEX = HERE / 'web' / 'index.html'
JOBS = {}
TOKEN = secrets.token_urlsafe(24)
SERVER = None


def unique_dir(path):
    path = Path(path)
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name('%s-%d' % (path.name, index))
        if not candidate.exists():
            return candidate
    raise RuntimeError('无法生成新的工作目录')


def default_name():
    return '自动剪辑-' + time.strftime('%Y%m%d-%H%M')


def default_work():
    return Path.home() / 'Movies' / 'JianyingAutoWork' / default_name()


def environment():
    result = {'platform': 'windows' if platform_support.IS_WIN else 'macos',
              'editor_running': False, 'errors': []}
    try:
        info = runtime.app_info()
        result.update(app_version=info.get('version'), app_path=info.get('path'),
                      draft_root=str(runtime.draft_root()))
    except Exception as error:
        result['errors'].append(str(error))
    try:
        result['editor_running'] = runtime.editor_running()
    except Exception:
        pass
    try:
        result['ffmpeg'] = str(media.ffmpeg())
    except Exception as error:
        result['errors'].append(str(error))
    try:
        codec.build_codec()
        result['codec'] = True
    except Exception as error:
        result['codec'] = False
        result['errors'].append('加解密不可用: %s' % error)
    result['default_name'] = default_name()
    result['default_work'] = str(default_work())
    return result


def choose_folder(kind):
    prompt = '选择素材文件夹' if kind == 'input' else '选择工作目录'
    if platform_support.IS_MAC:
        script = 'POSIX path of (choose folder with prompt "%s")' % prompt
        result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        value = result.stdout.strip().rstrip('/')
        return value or None
    if platform_support.IS_WIN:
        script = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
            '$d.Description = "%s";'
            'if ($d.ShowDialog() -eq "OK") { $d.SelectedPath }' % prompt)
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                                capture_output=True, text=True, creationflags=0x08000000)
        value = result.stdout.strip()
        return value or None
    return None


def append(job, message):
    job['logs'].append('[%s] %s' % (time.strftime('%H:%M:%S'), message))


def run_job(job, input_dir, brief, name, work, publish, do_export):
    try:
        append(job, '扫描素材...')
        scanned = auto.scan(input_dir)
        append(job, '视频 %d，图片 %d，音频 %d，字幕 %d'
               % (len(scanned['videos']), len(scanned['images']),
                  len(scanned['audios']), len(scanned['subtitles'])))
        options = auto.parse_brief(brief)
        append(job, '目标 %.1f 秒，单段 %.1f 秒'
               % (options['duration_us'] / 1_000_000, options['pace_us'] / 1_000_000))
        plan, summary = auto.build_plan(scanned, options, name, name)
        work = unique_dir(work).resolve()
        work.mkdir(parents=True, exist_ok=True)
        plan_path = work / 'plan.json'
        runtime.write_json(plan_path, plan)
        append(job, '计划已生成: %s' % plan_path)
        append(job, '构建草稿...')
        built = draft.build(plan_path, work / 'build')
        append(job, '草稿构建完成: %s' % built['target'])
        published = None
        if publish:
            append(job, '登记到剪映首页...')
            published = draft.publish(work / 'build', work / 'audit')
            append(job, '已登记: %s' % published['draft'])
        exported = None
        if do_export:
            append(job, 'FFmpeg 导出 MP4...')
            exported = export_module.export(work / 'build', work / 'export')
            append(job, '导出完成: %s' % exported['output'])
        job['result'] = {
            'work': str(work),
            'draft': built['target'],
            'name': name,
            'published': bool(published),
            'export': exported['output'] if exported else None,
            'duration_us': summary.get('duration_us', 0),
            'segments': summary.get('segments', 0),
        }
        job['status'] = 'done'
    except Exception:
        job['error'] = traceback.format_exc()
        job['status'] = 'error'
        append(job, '失败: ' + job['error'].strip().splitlines()[-1])


class Handler(BaseHTTPRequestHandler):
    server_version = 'jydraft/1.0'

    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type='application/json; charset=utf-8'):
        payload = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code, value):
        self._send(code, json.dumps(value, ensure_ascii=False))

    def _authorized(self):
        return self.headers.get('X-Jydraft-Token') == TOKEN

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/':
            html = INDEX.read_text(encoding='utf-8')
            self._send(200, html.replace('__JYDRAFT_TOKEN__', TOKEN), 'text/html; charset=utf-8')
            return
        if path == '/api/env':
            self._json(200, environment())
            return
        if path.startswith('/api/job/'):
            job = JOBS.get(path.rsplit('/', 1)[-1])
            if not job:
                self._json(404, {'error': '任务不存在'})
                return
            self._json(200, {'status': job['status'], 'logs': job['logs'][-400:],
                             'result': job['result'], 'error': job['error']})
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        if not self._authorized():
            self._json(403, {'error': 'invalid token'})
            return
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            self._json(400, {'error': 'invalid json'})
            return
        path = self.path.split('?', 1)[0]
        if path == '/api/browse':
            value = choose_folder(body.get('kind', 'input'))
            self._json(200, {'path': value})
            return
        if path == '/api/open':
            target = body.get('path')
            if not target or not Path(target).exists():
                self._json(400, {'error': '路径不存在'})
                return
            platform_support.open_path(target)
            self._json(200, {'ok': True})
            return
        if path == '/api/launch':
            platform_support.launch_editor()
            self._json(200, {'ok': True})
            return
        if path == '/api/quit-editor':
            self._json(200, {'closed': platform_support.quit_editor()})
            return
        if path == '/api/start':
            self._start(body)
            return
        if path == '/api/shutdown':
            self._json(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._json(404, {'error': 'not found'})

    def _start(self, body):
        input_dir = Path(str(body.get('input', ''))).expanduser()
        if not input_dir.is_dir():
            self._json(400, {'error': '素材文件夹不存在'})
            return
        name = str(body.get('name') or default_name()).strip()
        if not name:
            self._json(400, {'error': '草稿名为空'})
            return
        work = Path(str(body.get('work') or default_work())).expanduser()
        publish = bool(body.get('publish'))
        do_export = bool(body.get('export'))
        if publish and runtime.editor_running():
            self._json(409, {'error': 'editor-running', 'message': '发布前需要先退出剪映'})
            return
        job_id = secrets.token_hex(8)
        job = {'status': 'running', 'logs': [], 'result': None, 'error': None}
        JOBS[job_id] = job
        threading.Thread(target=run_job, args=(job, input_dir, str(body.get('brief', '')),
                                               name, work, publish, do_export), daemon=True).start()
        self._json(200, {'job_id': job_id})


def open_window(url):
    if platform_support.IS_MAC:
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if chrome.is_file():
            subprocess.Popen([str(chrome), '--app=' + url])
            return
    elif platform_support.IS_WIN:
        edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / \
               'Microsoft/Edge/Application/msedge.exe'
        if edge.is_file():
            subprocess.Popen([str(edge), '--app=' + url])
            return
    webbrowser.open(url)


def main():
    global SERVER
    INDEX.is_file() or (_ for _ in ()).throw(RuntimeError('缺少 web/index.html'))
    SERVER = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = SERVER.server_address[1]
    url = 'http://127.0.0.1:%d/?token=%s' % (port, TOKEN)
    print('jydraft 已启动:', url)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    open_window(url)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        SERVER.shutdown()


if __name__ == '__main__':
    main()
