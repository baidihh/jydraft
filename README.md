# jydraft

给 Agent 和终端用的剪映自动剪辑 CLI：**一个素材文件夹 + 一句话 → 可编辑的剪映草稿 + MP4**。

支持 macOS 与 Windows。CLI 全部输出 JSON，任何能执行终端命令的 AI Agent 都可以直接调用。

```bash
jydraft auto --input /素材目录 --brief "20秒快节奏竖屏，配乐，带字幕" \
  --work /tmp/job --name 我的自动剪辑 --publish --export
```

- 自动扫描视频、图片、音频、SRT；
- 用 ffmpeg 场景检测按节奏选段；
- 拼接主轨、插入图片、循环 BGM、上字幕；
- 生成原生可编辑剪映草稿，登记到首页，并可无头导出 MP4。

> 草稿的实际打开、播放、保存和冷重开仍建议在剪映里人工验收。

---

## 工作方式：AI 出计划，两个执行器干活

```text
素材目录
  └─ analyze ──→ evidence.json + 代表帧 + 字幕 + 音量
                    │
                    ├─ Agent 读证据，产出 jy14-headless-plan/v1
                    │
                    ├─ build / publish ──→ 剪映可编辑草稿（人可微调）
                    └─ export ───────────→ FFmpeg MP4（快速预览/成片）
```

- `analyze` 只描述素材事实：镜头切点、代表帧、音量、SRT 分句，不做创作判断。
- `plan` 是唯一的执行契约；任何模型或脚本只要产出合法 JSON，就能被执行。
- `build` 和 `export` 是**两个独立执行器**：同一个计划既能落到剪映草稿，也能直接出 MP4。
- FFmpeg 执行器不重实现剪映的全部效果；遇到蒙版、关键帧、旋转、透明度、转场时，
  `export` 报告的 `degraded` 字段会明确列出降级项，不会静默丢弃。
- 需要像素级还原这些效果时，再走剪映原生导出（目前仅 11.4.2 / 11.5.0 有匹配配置）。

---

## 1. 安装

### 方式 A：下载发布包（推荐）

到 [Releases](https://github.com/baidihh/jydraft/releases) 下载对应平台：

- `jydraft-macos.zip`
- `jydraft-windows.zip`

macOS：

```bash
unzip jydraft-macos.zip -d ~/.local/share/jydraft
ln -sf ~/.local/share/jydraft/jydraft ~/.local/bin/jydraft
jydraft doctor
```

Windows（PowerShell）：

```powershell
Expand-Archive jydraft-windows.zip -DestinationPath $env:USERPROFILE\jydraft
$env:Path += ";$env:USERPROFILE\jydraft"
jydraft doctor
```

发布包内置 ffmpeg/ffprobe，不需要另外安装。首次运行如果被系统拦截：

- macOS：`xattr -dr com.apple.quarantine ~/.local/share/jydraft`
- Windows：SmartScreen →「更多信息」→「仍要运行」

### 方式 B：从源码运行

需要 Python 3.9+、ffmpeg/ffprobe、Python 依赖 Pillow。

```bash
git clone https://github.com/baidihh/jydraft.git
cd jydraft
python3 -m pip install -r requirements.txt
python3 -m jydraft --help
```

macOS 首次运行会尝试编译 `bin/jycodec`（需要 Xcode Command Line Tools）；失败时自动退化为明文草稿（剪映打开后仍会自己加密）。

### 剪映

需要自行安装剪映专业版：

- macOS：已实测 **11.6.0**；草稿目录 `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`
- Windows：按上游验证为 **11.5.0.14471**；安装目录
  `%LOCALAPPDATA%\JianyingPro\Apps\<version>\`，草稿目录
  `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`

Windows 可用环境变量覆盖：

```powershell
$env:JIANYING_INSTALL_DIR = "C:\path\to\JianyingPro"
$env:JIANYING_DRAFT_ROOT = "D:\JianyingDrafts"
```

> Windows 路径与加密桥接来自公开的 Windows 移植证据，CI 会构建和跑单元测试，但当前开发机是 macOS，**Windows 还需要在真机上做一次完整验收**。

---

## 2. 给 Agent 调用

先让 Agent 读能力清单和计划 Schema：

```bash
jydraft capabilities
jydraft schema
```

典型 Agent 流程：

```bash
# 1) 环境
jydraft doctor

# 2) 理解素材：镜头、代表帧、音量、字幕
jydraft analyze --input /素材目录 --work /tmp/job-analysis

# 3) Agent 读 /tmp/job-analysis/evidence.json 和 frames/*.jpg，
#    产出 /tmp/job-analysis/plan.json（格式见 jydraft schema）

# 4) 先看计划，不落草稿
jydraft build --plan /tmp/job-analysis/plan.json --out /tmp/job-build
jydraft verify-build --build /tmp/job-build

# 5) 快速出片检查剪辑节奏（degraded 会列出 FFmpeg 没还原的效果）
jydraft export --build /tmp/job-build --out /tmp/job-preview

# 6) 满意后登记到剪映首页，人工微调
jydraft publish --build /tmp/job-build --audit /tmp/job-publish
```

没有 Agent 或不想自己写计划时，用规则兜底：

```bash
jydraft auto --input /素材目录 --brief "15秒快节奏，配乐，带字幕" \
  --work /tmp/job --name 成片01 --publish --export
```

约定：

- 成功结果输出到 **stdout**，固定 JSON；
- 错误输出到 **stderr**；
- 退出码：`0` 成功，`1` doctor 检查失败，`2` 参数/运行错误；
- `publish` / `edit-*` 前必须完全退出剪映；
- 需要预览计划时用 `--dry-run`，不会落盘。

`--brief` 目前识别这些词：

| 意图 | 示例 | 行为 |
| --- | --- | --- |
| 时长 | `20秒`、`1分钟` | 目标时长 |
| 节奏 | `快节奏`、`卡点` | 2 秒一段 |
| 节奏 | `慢`、`Vlog` | 4.5 秒一段 |
| 画幅 | `竖屏`、`9:16`、`抖音` | 1080×1920 |
| 画幅 | `横屏`、`16:9`、`B站` | 1920×1080 |
| 音频 | `静音`、`无声` | 原声静音 |
| 音频 | `配乐`、`BGM` | 使用素材里的音频循环铺设 |
| 字幕 | `字幕` | 自动读取素材目录里的 `.srt` |

---

## 3. 命令

| 命令 | 作用 |
| --- | --- |
| `doctor` | 检查剪映、草稿目录、ffmpeg、加解密 |
| `analyze --input DIR --work DIR` | 输出 AI 可读证据包：镜头、代表帧、音量、字幕 |
| `scan --input DIR` | 扫描素材和镜头切点 |
| `auto --input DIR --brief TEXT --work DIR` | 一句话自动粗剪（规则兜底） |
| `build --plan FILE\|- --out DIR` | 按计划生成离线 build |
| `publish --build DIR --audit DIR` | 登记到剪映首页 |
| `create --plan FILE\|- --work DIR` | build + publish |
| `verify-build --build DIR` | 校验离线 build |
| `verify-live --build DIR` | 回读首页登记与加密草稿 |
| `inspect --draft NAME` | 汇总已有草稿（支持加密） |
| `decode --file PATH` | 把加密草稿解码成明文 JSON |
| `edit-text --draft NAME --text TEXT` | 修改已有草稿的字幕 |
| `edit-timeline --draft NAME --plan FILE --force` | 整体替换已有草稿时间线 |
| `export --build DIR --out DIR` | FFmpeg 无头导出 MP4 |
| `sample --out FILE` | 生成合成测试素材 |
| `selftest` | 验证原生加解密 |
| `schema` | 输出计划 JSON Schema |
| `capabilities` | 输出机器可读能力清单 |
| `app` | 可选的本地网页界面（人类使用） |

计划格式示例见 [examples/basic.plan.json](examples/basic.plan.json) 和
[examples/mask.plan.json](examples/mask.plan.json)。

---

## 4. 能力与限制

| 能力 | 状态 |
| --- | --- |
| 视频 / PNG / JPEG / GIF 主轨 | 支持 |
| 画中画 / 多轨 | 支持 |
| 字幕 / 标题 / 颜色 / 描边 / 位置 | 支持 |
| BGM / 音效 / 音量 | 支持 |
| 裁剪、变速、缩放、旋转、透明度 | 支持 |
| 线性关键帧 | 支持 |
| 六种静态蒙版 | 支持（使用本机剪映缓存资源） |
| 修改已有草稿 | 支持 `edit-text` / `edit-timeline` |
| 读取剪映加密草稿 | 支持 |
| FFmpeg 无头导出 MP4 | 支持 |
| 素材证据包（镜头/抽帧/音量/字幕） | 支持 `analyze` |
| 自动语音识别字幕 | 未实现；需要先提供 `.srt` |
| 转场 / 滤镜 / 特效 / 花字 | 未实现（缺少本机已采集资源） |
| 剪映原生引擎无头导出 | 11.6 无可用 ABI；上游也只支持 11.5/11.4.2 |
| FFmpeg 导出蒙版/关键帧/旋转/透明度/转场 | 不渲染，`degraded` 字段会逐项列出 |

`auto` 是**规则驱动粗剪**，不是语义级 AI 创作。需要创作意图时：

1. 先跑 `analyze` 拿到 `evidence.json` 和代表帧；
2. Agent 读帧、字幕、音量，决定用哪些镜头、怎么排；
3. 产出 `jy14-headless-plan/v1`，用 `build` / `create` 执行；
4. 先用 `export` 低价预览，再 `publish` 进剪映微调。

---

## 5. 构建发布包

```bash
python3 -m pip install -r requirements-dev.txt
npm install --no-save --no-package-lock ffmpeg-static ffprobe-static
python3 scripts/fetch_tools.py
bash scripts/build_codec.sh        # 仅 macOS
pyinstaller jydraft.spec --noconfirm
```

产物在 `dist/jydraft/`。GitHub Actions 会：

- 在 macOS / Windows 上跑测试并打包；
- 推送 tag `v*` 时自动创建 Release 并上传两个 zip。

---

## 6. 实现说明

- macOS 草稿加解密：运行时 `dlopen` 本机 `libvideoeditor.dylib` 的
  `lvve::EncryptUtils`，编译产物只有自己的桥接代码；
- Windows：`ctypes` 调用本机 `videoeditor.dll` 的 MSVC 导出符号；
- 媒体探测、封面、场景检测、导出：ffmpeg / ffprobe；
- 字幕 PNG：Pillow + 系统中文字体；
- 草稿字段、时间线结构与逆向过程见 [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md)。

## 7. 合规

本项目不分发剪映程序、DLL、字体、素材或账号数据。仅用于本机互操作和个人学习。
使用前请确认你拥有剪映的合法安装与素材授权。详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [LICENSE](LICENSE)。
