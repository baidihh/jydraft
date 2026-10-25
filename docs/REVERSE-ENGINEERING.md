# 剪映 11.6 本地草稿生成（逆向报告 + 工具）

> 结论：不启动剪映 UI、不导入素材、不点任何按钮，直接生成一份**可被剪映专业版 11.6.0 打开、渲染并继续编辑**的原生草稿。
> 已在当前机器实测：草稿出现在「本地草稿」，双击打开后画面、字幕、时间线、素材库均正常（见 [首页截图](docs/01-home-draft-list.png)、[单轨打开截图](docs/02-draft-opened.png)、[四轨打开截图](docs/03-mix-draft-opened.png)、[蒙版打开截图](docs/04-mask-draft-opened.png)）。
> 还实现了加密草稿回读、修改已有草稿、FFmpeg 无头导出，以及 [导出帧截图](docs/05-export-frame.png)。
> 最新加入 `auto`：给一个素材文件夹和一句话，自动粗剪成可编辑草稿（[自动剪辑打开截图](docs/07-auto-draft-opened.png)）。

---

## 1. 与 yichen-skills / jianying-headless 的关系

调查公开提交后的事实链：

- `mcncarl/yichen-skills` 在 `6e0c0ae`（2026-10-23）一次性删除了 `yichen-jianying-edit/` 目录、中英文 README 条目、第三方声明和发布链说明。
- 该 Skill 本身不含引擎，只负责调用另行检出的 `mcncarl/jianying-headless`，并校验核心文件哈希；核心仓库随后匿名访问 404，公开入口既装不起来也过不了校验。
- 公开镜像 `GdLinkLock/jianying-headless` 仍保留了「私有源码预览」的完整结构（engine/bridge/skills），本项目把它作为**格式与流程参考**，但没有直接依赖它的私有授权链。

本项目是独立实现：只保留「从本地素材生成新草稿」这一条主链路，去掉了原项目对 11.4.x 版本、固定哈希、原生资源目录和账号资源的强绑定，改为适配本机 **11.6.0 / build 490**。

---

## 2. 目标程序事实

| 项 | 值 |
| --- | --- |
| App | `/Applications/VideoFusion-macOS.app` |
| Bundle ID | `com.lemon.lvpro` |
| 版本 | 11.6.0（build 490） |
| 签名 Team | `X2JNK7LY8J` |
| 主编辑器库 | `Contents/Frameworks/libvideoeditor.dylib`（183 MB，fat：x86_64 + arm64） |
| 草稿根目录 | `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft` |
| 根目录来源 | `~/Movies/JianyingPro/User Data/Config/globalSetting` 的 `currentCustomDraftPath` |
| 首页索引 | `<草稿根>/root_meta_info.json`（明文 JSON） |
| 草稿时间线版本 | `version = 360000`，`new_version = 189.0.0` |

### 2.1 REA 使用记录

本机 `~/.local/bin/rea`（REA 6.7.0，Node 26 + Ghidra 12.1.4 + JDK 25）实际执行过：

```bash
rea doctor                                     # 153 CLI / 207 MCP tools
lipo -thin arm64 libvideoeditor.dylib ...      # REA 的 Ghidra provider 不接受 fat Mach-O
rea inspect-macho /tmp/ve_thin/libvideoeditor_arm64.dylib --format md
rea decompile /tmp/ve_thin/... \
  --procedure _ZN4lvve12EncryptUtils7encryptE...  # 89MB 单 slice，Ghidra 导入超时，未产出伪代码
```

`inspect-macho` 给出的 arm64 slice SHA-256 为
`8683300150f6f6495c88303375acaef688ad4c49095288fa8e1e18269a83c458`。
Ghidra 对 1.5GB 的 `libVECreator.dylib` 同样不具备可用性，因此加密细节改用符号表 + 字符串 + 运行时黑盒验证。

---

## 3. 关键逆向发现

### 3.1 草稿文件集合

一个可被 11.6 打开的草稿目录至少包含：

```
draft_info.json                     主时间线
draft_info.json.bak                 时间线镜像
template-2.tmp                      时间线镜像
draft_meta_info.json                草稿元信息
key_value.json                      素材登记
timeline_layout.json                时间线布局
draft_virtual_store.json            素材库视图
draft_settings                      草稿设置（INI）
draft_cover.jpg                     封面
Timelines/project.json             多时间线工程
Timelines/<timeline_id>/draft_info.json
Timelines/<timeline_id>/template-2.tmp
Timelines/<timeline_id>/template.tmp
Timelines/<timeline_id>/draft_cover.jpg
Resources/headless-media/<sha256>.<ext>
```

### 3.2 明文可以被读取

`libvideoeditor` 中存在日志：

```
[DRAFT_IO_MANAGER] IOManagerPrivate::tryDecrypt: decrypt fail, may be raw content, path: {}
```

实测确认：把 `draft_info.json` / `draft_meta_info.json` 直接写成 UTF-8 JSON，剪映能够正常加载、渲染、播放。
**剪映在打开/保存后会立刻把它们改写为加密格式**（本次实测文件仍约 6.3KB，但内容已变为 base64 字符流）。

### 3.3 加密链路（已完成）

- `nm` 确认 11.6 仍导出 `lvve::EncryptUtils::{encrypt, decrypt, enable, isFileEncrypted, decryptFile}`。
- 字符串层面能看到 `jianying_draft_encrypt_v2`、`decryptKV2`、`is not crypted by KV2`、`EncryptStringKeyIVMuxer`；但实测发现 **公开的 `EncryptUtils::decrypt` 仍能解开 11.6 保存的草稿**：
  - 密文是 base64 字符流；
  - `decrypt(cipher, "{}")` 对完整草稿（≥131 字节）返回明文 JSON；
  - 小于 131 字节时会静默返回空，这也是最初误判“KV2 解不开”的原因；
  - `decryptFile` / `decryptFileBytes` 在 11.6 是空实现（分别固定返回 1 / -1），不能使用。
- 本工具用 `clang++` 把 `encrypt/decrypt` 编译成本地 `bin/jycodec`，链接本机 `libvideoeditor`：
  - `build` 默认直接写加密草稿；
  - `inspect` / `decode` / `verify-live` 可以回读剪映保存后的加密草稿；
  - 加密/解密在构建时做往返校验。

因此现在不需要“先把明文交给剪映加密”这种折中路径，生成、回读、修改走的是同一套格式。

### 3.4 首页登记

`root_meta_info.json` 是明文，核心结构：

```json
{
  "root_path": "/Users/.../com.lveditor.draft",
  "all_draft_store": [ { "draft_id": "...", "draft_fold_path": "...", "draft_name": "...", "...": "..." } ],
  "draft_ids": 1
}
```

把新草稿目录复制到根目录、插入一条 `all_draft_store`、递增 `draft_ids`，剪映下次启动即可在「本地草稿」看到。

---

## 4. 工程结构

```
剪映逆向/
  jydraft/
    cli.py            命令行入口
    draft.py          草稿目录、时间线、元信息、首页登记、edit/edit-timeline
    plan.py           剪辑计划校验与素材探测
    motion.py         线性关键帧 + 六种静态蒙版
    codec.py          原生加密/解密桥接（bin/jycodec）
    resources.py      本机蒙版资源校验与复制
    auto.py           一句话自动粗剪（扫描/选段/排轨/字幕/BGM）
    export.py         FFmpeg 无头 MP4 导出
    runtime.py        路径、版本、进程检查、媒资探测
    blueprint.json    11.6 实测可用的时间线蓝本
    native-resource-catalog.json  本机采集的蒙版资源清单
    bridge/           jy14_codec.cpp + EncryptUtil.h
  tools/jymedia.swift 媒资探测、封面、合成素材、CoreText 字幕 PNG
  bin/jymedia         jymedia 编译产物
  bin/jycodec         原生加解密桥接编译产物
  tests/test_jydraft.py
  examples/basic.plan.json
  docs/               实测截图
  reference/          公开镜像源码（仅作格式参考）
```

媒资探测、封面、合成测试素材和字幕 PNG 都走 AVFoundation/CoreText 的 `jymedia`：
`probe`、`cover`、`blank`、`sample`、`text` 五个子命令，纯系统框架。
FFmpeg 只用于导出后端，可用 `brew install ffmpeg` 安装。

---

## 5. 使用

### 5.1 一句话自动剪辑（推荐）

```bash
cd "/Users/wh/Documents/workspace/ai_mac/开发/剪映逆向"

# 先看看素材里有什么、镜头切点有多少
python3 -m jydraft.cli scan --input /绝对路径/素材文件夹

# 一句话自动粗剪：选段、拼主轨、插图片、垫 BGM、上 SRT 字幕
python3 -m jydraft.cli auto \
  --input /绝对路径/素材文件夹 \
  --brief "20秒快节奏，配乐，带字幕" \
  --work /tmp/auto-work \
  --name 自动剪辑测试 \
  --publish \
  --export
```

`--brief` 目前能识别：

- 时长：`20秒`、`1分钟`；
- 节奏：`快节奏/卡点` → 2 秒一段，`慢/Vlog` → 4.5 秒一段；
- 画幅：`竖屏/9:16/抖音`、`横屏/16:9/B站`；
- `静音/无声`、`配乐/BGM`、`字幕`。

行为：

- 用 ffmpeg 场景检测找镜头切点，按节奏从每个视频轮换取片段；
- 主轨硬切连续拼接，每 4 段插入一张图片，BGM 自动循环铺满；
- 如果素材目录里有 `.srt`，自动上字幕轨；没有 SRT 时只生成标题；
- 输出仍然是可继续编辑的剪映草稿；`--publish` 登记首页，`--export` 同时出 MP4。

这是**规则驱动的粗剪器**，不是语义级 AI 创作：它不会理解“哪一段最好看”，也不会自动语音识别。
想要更作者的剪法，可以直接把素材目录和你的要求发给我（Trae），我再用 `scan` 的结果逐段挑选并生成计划。

### 5.2 手动计划与其它命令

```bash
cd "/Users/wh/Documents/workspace/ai_mac/开发/剪映逆向"

# 1) 自检（首次编译 jymedia 与 jycodec）
python3 -m jydraft.cli doctor --build-helper
python3 -m jydraft.cli selftest

# 2) 一键生成合成素材 + 计划 + 离线 build
python3 -m jydraft.cli demo --work /tmp/jydraft-demo

# 3) 登记到剪映首页（必须先完整退出剪映）
python3 -m jydraft.cli demo --work /tmp/jydraft-demo2 --publish

# 4) 标准三步
python3 -m jydraft.cli build   --plan examples/basic.plan.json --out /tmp/my-build
python3 -m jydraft.cli publish --build /tmp/my-build --audit /tmp/my-audit
python3 -m jydraft.cli verify-build --build /tmp/my-build
python3 -m jydraft.cli verify-live  --build /tmp/my-build

# 5) 回读剪映保存后的加密草稿
python3 -m jydraft.cli inspect --draft jydraft-demo
python3 -m jydraft.cli decode --file "/Users/wh/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/jydraft-demo/draft_info.json" --out /tmp/timeline.json

# 6) 修改已有草稿（需先退出剪映；自动备份到 .work/backups/）
python3 -m jydraft.cli edit-text --draft jydraft-demo --index 0 --text "新的字幕"
python3 -m jydraft.cli edit-timeline --draft jydraft-demo --plan /tmp/new-plan.json --force

# 7) FFmpeg 无头导出
python3 -m jydraft.cli export --build /tmp/my-build --out /tmp/my-export

# 8) 自动化测试
python3 -m unittest discover -s tests -v
```

计划格式（`jy14-headless-plan/v1`）：

```json
{
  "schema": "jy14-headless-plan/v1",
  "name": "我的第一个自动草稿",
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "tracks": [
    {"type": "video", "name": "主视频", "segments": [
      {"source": "/absolute/source.mp4", "start_us": 0, "duration_us": 2000000,
       "mask": {"shape": "circle", "width": 0.35, "height": 0.35, "feather": 0.08}}
    ]},
    {"type": "text", "name": "字幕", "segments": [
      {"text": "这是一条可编辑字幕", "start_us": 0, "duration_us": 2000000,
       "size": 6, "y": -0.78, "color": "#FFFFFF"}
    ]},
    {"type": "audio", "name": "BGM", "segments": [
      {"source": "/absolute/music.wav", "start_us": 0, "duration_us": 2000000, "volume": 0.12}
    ]}
  ]
}
```

蒙版可选 `circle/rectangle/line/mirror/star/heart`，资源来自本机剪映缓存，
构建时会逐文件校验并复制进草稿。完整字段见 [basic.plan.json](examples/basic.plan.json)
和 [mask.plan.json](examples/mask.plan.json)。

---

## 6. 能力边界

| 能力 | 状态 |
| --- | --- |
| 视频 / PNG / JPEG / GIF 主轨 | 已支持 |
| 画中画（第二条及以后 video 轨） | 已支持 |
| 字幕 / 标题（字号、颜色、描边、位置） | 已支持 |
| 本地 BGM / 音效（独立 audio 轨） | 已支持 |
| 裁剪、变速、音量、缩放、旋转、透明度 | 已支持 |
| 线性关键帧（x/y/scale/rotation/opacity/volume） | 已支持 |
| 六种静态蒙版 | 已支持（使用本机剪映缓存资源，构建时逐文件校验） |
| 一句话自动粗剪 | 已支持：`scan` + `auto`，场景检测选段、BGM 循环、SRT 字幕 |
| 自动语音识别字幕 | 未实现；需要先提供 SRT，或由外部 ASR 生成 |
| 转场、滤镜、画面特效、花字 | 未实现（本机缺少对应的已采集缓存资源） |
| 修改已有草稿 | 已支持：`edit-text` 改文字、`edit-timeline` 整体替换（自动备份） |
| 读取剪映保存后的加密草稿 | 已支持：`inspect` / `decode` / `verify-live` |
| 无头导出 MP4 | 已支持：FFmpeg 后端（非剪映原生引擎） |
| 剪映原生引擎无头导出 | 11.6 无可用导出 ABI；上游也只支持 11.5/11.4.2 |

约束：

- `publish` 前必须完全退出剪映（主页运行也会占用首页索引）。
- `edit-text` / `edit-timeline` 同样要求剪映完全退出，并会先把原文件备份到 `.work/backups/`。
- `edit-timeline` 默认拒绝含非基础素材的草稿；确认要整体替换需加 `--force`。
- 只创建新草稿，不覆盖同名目录（`edit-*` 是显式修改）。
- 素材会复制到草稿自己的 `Resources/headless-media/`，源文件之后可移动。
- 主视频轨必须从 0 开始且连续；空隙请放画中画轨。
- 仅支持 H.264 / HEVC、单视频流 + 至多一条音频流；带旋转元数据的素材需先归一化。
- 蒙版依赖本机剪映缓存里的对应资源目录；文件哈希不匹配时会停止，不自动下载。
- FFmpeg 导出需要 `ffmpeg` / `ffprobe`，且不渲染蒙版、关键帧和剪映特效。

---

## 7. 导出说明

`export` 是独立的 **FFmpeg 渲染后端**，不是剪映原生引擎导出：

- 输入是已校验的 `build`，不会修改 live 草稿；
- 支持主视频/画中画、裁剪、变速、音量、图片/GIF、音频轨和字幕；
- 字幕用 CoreText 渲染成透明 PNG，再用 overlay 合成，不依赖 ffmpeg 的 drawtext；
- 输出固定 H.264 / yuv420p + AAC（有音频时），并自动用 ffprobe 校验
  分辨率、帧率、时长、帧数和音频流数量；
- **不渲染**原生蒙版、关键帧和剪映特效；导出报告会列出 `warnings`；
- 需要 `ffmpeg`/`ffprobe`（本机用 `brew install ffmpeg` 安装）。

剪映 11.6 本身没有公开、可用的原生无头导出 ABI；上游 `jianying-headless`
也只对 11.5 / 11.4.2 做原生导出，对 11.6 只提供编辑器 UI 导出。
因此这里选择 FFmpeg 后端来保证“无头出片”，并在文档和报告里明确它与剪映原生渲染的差异。

---

## 8. 实测记录

2026-10-25，本机 macOS 26.5 / 剪映 11.6.0：

1. `demo` 生成 3 秒 1280×720 H.264 素材（AVAssetWriter）并构建草稿。
2. `publish` 写入 `root_meta_info.json`。
3. 启动剪映，首页「本地草稿」出现 `jydraft-demo`，显示 `53.6K | 00:03`（[截图](docs/01-home-draft-list.png)）。
4. 双击打开：预览区出现蓝紫渐变 + 字幕「jydraft 生成的草稿」，时间线同时出现字幕段与 `media.mp4 00:00:03:00`，右侧工程信息显示 1280×720、Rec.709 SDR（[截图](docs/02-draft-opened.png)）。
5. 第二个草稿 `jydraft-mix-test` 包含 4 条轨道：主视频、画中画（JPEG，0.5s 起 1s，缩放 0.4、透明度 0.9）、字幕（x 轴 0→3s 线性关键帧）、BGM（WAV）。打开后 3 个素材全部进入素材库，时间线四条轨道完整，预览区渲染出字幕（[截图](docs/03-mix-draft-opened.png)）。
6. 加密草稿 `jydraft-enc` 由 `build` 直接写原生密文；剪映正常打开，随后 `inspect` / `verify-live` 能解密回读。
7. `edit-text` 把 `jydraft-demo` 的字幕改成「已通过 edit-text 修改」，重新打开后预览区和时间线都显示新文本（[截图](docs/06-edit-text-opened.png)）。
8. `edit-timeline` 用四轨计划整体替换了 `jydraft-enc` 的时间线；`inspect` 回读为 4 条轨道，原文件已备份到 `.work/backups/`。原生重开验收待补。
9. 蒙版草稿 `jydraft-mask-test` 在剪映中打开后，预览区正常显示圆形蒙版（羽化 0.08），字幕也可编辑（[截图](docs/04-mask-draft-opened.png)）。
10. `export` 对单轨草稿输出 90 帧 / 3.0s，对四轨草稿输出 90 帧 + AAC 音轨；导出帧见 [截图](docs/05-export-frame.png)。`python3 -m unittest discover -s tests -v` 4 项通过。
11. `auto` 用 3 段视频 + 1 张图片 + WAV BGM + SRT，按「20秒快节奏，配乐，带字幕」自动生成 20 秒草稿：10 个主轨片段、图片插入、BGM 循环、3 条 SRT 字幕；`--publish` 后剪映首页出现 `自动剪辑测试`，打开后四类轨道和时间线均正常（[截图](docs/07-auto-draft-opened.png)）。

---

## 9. 合规说明

- 仅用于自己机器上的本地互操作与个人学习，不绕过剪映账号、会员、授权或云服务。
- 不随项目分发剪映二进制、字体、效果资源或账号数据。
- `reference/` 中的公开镜像源码继续适用其原作者的个人学习 / 非商业许可；本项目的 `jydraft/` 为独立实现，参考了其计划格式与时间线字段组织方式。
- 商用或再分发前，请自行确认剪映软件许可、素材版权和上游项目授权。
