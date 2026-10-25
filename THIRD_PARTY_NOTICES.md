# 第三方声明

本项目不分发剪映（JianyingPro）官方程序、库、字体、素材或账号数据。
运行时会调用用户本机安装的剪映组件：

- macOS：`/Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib`
- Windows：`%LOCALAPPDATA%\JianyingPro\Apps\<version>\videoeditor.dll`

这些组件仍受字节跳动/剪映的许可约束，使用前请确认你拥有合法安装和本地互操作权限。

## 参考项目

草稿字段与时间线组织方式参考了公开的 `jianying-headless` 源码预览
（原作者 mcncarl，个人学习 / 非商业许可）以及 `pyJianYingDraft`（Apache-2.0）。
`jydraft/blueprint.json` 和 `jydraft/native-resource-catalog.json` 是基于这些公开资料
和本机采集整理的格式/资源元数据，不包含官方二进制或素材文件。
如果你再分发本项目，请同时遵守上述上游项目的许可和剪映的软件许可。

## 运行时依赖

- **FFmpeg / FFprobe**：发布包中的静态二进制来自 npm `ffmpeg-static` / `ffprobe-static`，
  通常为 GPL 构建。FFmpeg 版权归其作者所有，源码与许可见 <https://ffmpeg.org/>。
- **Pillow**：HPND 许可，用于字幕 PNG 渲染。
- **Python**：PSF 许可。
- **剪映**：由用户自行安装，本项目不提供下载。

## 商标

“剪映”“JianyingPro”等名称和商标归其权利人所有。本项目与剪映官方无关联，
不是官方 SDK，也不代表官方认可。
