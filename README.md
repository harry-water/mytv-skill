# MyTV：我的家庭电视台

Harry Water · 1.0.0

从人工确认的本地素材生成三至五分钟家庭回忆影片和独立JPEG封面。此版本只包含本地制片，不包含电视控制、屏保、音乐、云服务、排程或生产集成。Local memory-film and cover generator for Linux.

## 前置条件与验证范围

需要Linux/POSIX、Python标准库、FFmpeg/ffprobe、ImageMagick 7的`magick`命令，以及使用者合法提供的字体。Python 3.11+是根据代码接口提出的理论要求，未逐版本验证；实际回归环境为3.13.5，完整制片环境为3.13.15。

FFmpeg需支持zscale、drawtext、xfade、acrossfade、ssim，以及h264_nvenc（默认）或libx264（可选CPU后端）。仅列出编码器不代表GPU可用，doctor会做一次320×240单帧探测。封面验证使用FFmpeg和Python，不依赖ImageMagick；照片与标题画布仍需要ImageMagick。

已发布基线706ba2a的完整合成验收使用已有Python3.13.15、FFmpeg/ffprobe9.0.1、ImageMagick7.1.2-29 Q16-HDRI、RTX2060及驱动595.71.05。没有验证全新操作系统安装或提供已锁定的一键安装配方；本包不自动安装任何依赖，不附带字体、二进制、照片或音乐。

NVENC完整合成测试、普通与优化模式回归的最终结果见本文件下方“测试状态”。CPU后端仅通过2秒、4帧、1280×720编码及封面小样，未验证完整制片质量、性能或与NVENC等价。未验证实际播放器、电视、音箱、排程和通知。

可选锁定Nix依赖配方及官方字体来源见[docs/R1-INTEGRATION.md](docs/R1-INTEGRATION.md)和environment/sources.json。本次只静态审查配方，没有在合并入口下执行Nix shell或安装依赖；R1原环境的pure-shell实测不是本合并版/新OS/新机驱动验收。

## 从新目录快速上手

将发布源码解压至一个新的私人目录，进入含SKILL.md和scripts/的根目录；不要覆盖生产或已有家庭项目。无需pip安装。先准备符合前置条件的现有工具与外部字体，不要让Agent为了此包自动安装或借用生产资源。

把`config/local.template.json`复制到源码树以外，填写：

- `font_file`：外部字体绝对路径。
- `timezone`：IANA时区。
- `input_root`：已审素材目录。
- `output_root`：全新的私密输出目录，与输入目录互不包含。
- `encoding_backend`：`nvenc`或实验性的`cpu`。

输出根须由当前用户拥有，权限0700；路径及已有子树不能有符号链接。字体和输出路径中的冒号、单引号、反斜线、换行、回车、逗号、分号、方括号会在任何外部工具执行前明确拒绝。中文与空格路径已测试。

```sh
python3 scripts/run.py doctor --config /absolute/local.json
python3 scripts/run.py render --config /absolute/local.json --selection /absolute/selection.json --date 2030-10-24
```

选片文件是JSON数组。每项包含相对输入根的`file`、`kind`（photo/video）、可信`capture_date`（YYYY-MM-DD）、`chapter`（same-day/month-memory/season/trip），视频还可指定`video_start_seconds`和`video_speed`。至少一张照片、最多46个不同素材；选取足量真实素材以满足180–300秒，不重复填时长。

same-day必须是往年的相同月日，其他章节限定同月；trip须同一年、有同日锚点且满足限定范围，season最多20%。不要从mtime猜拍摄日期、虚构行程或人物关系。照片段不嵌入背景音乐，源视频原声可以保留。视频速度、非有限数值和选段越界会在编码/输出创建前拒绝。

## 输出、重复与停止

产物位于`output_root/YYYY-MM-DD/`，包括`daily-render.mp4`、`daily-render-thumb.jpg`及本地验证回执、中间文件和日志。不要把个人运行输出或日志提交到仓库。

新回执采用receipt_schema_version=2。无版本、其他版本或类型不符时拒绝复用，不自动迁移或改写旧回执；保留旧输出并选择新输出根。重复运行只有在schema、代码闭包、输入、配置、字体、工具版本指纹和影片/封面哈希全部一致时才复用。部分输出或不匹配输出保留并拒绝覆盖；改用新输出根，不自动删除旧任务。Ctrl+C只停止适配器自己的渲染进程组；SIGKILL、断电恢复及自动续渲染未验证。完整解码使用`-xerror`，可恢复损坏视频也必须被拒绝。

## 离线测试

```sh
python3 -m unittest discover -s tests -v
python3 -O -m unittest discover -s tests -v
python3 tests/smoke_cpu.py --output /absolute/new-private-smoke --font /absolute/existing-font
```

单元测试中标明的外部制片部分使用mock，不能当作真实影片验收。损坏解码回归实际生成极小H.264并验证普通解码可恢复、生产严格门禁拒绝；不会使用私人素材。CPU小样是独立低负担测试。

在已有合格依赖环境中新建演示目录，完整合成流程为：

```sh
python3 scripts/make_synthetic.py --root /absolute/new-private-demo --font /absolute/existing-font --backend nvenc --date 2030-10-24
python3 scripts/run.py doctor --config /absolute/new-private-demo/config.json
python3 scripts/run.py render --config /absolute/new-private-demo/config.json --selection /absolute/new-private-demo/selection.json --date 2030-10-24
```

它生成28张几何JPEG、4段12秒测试视频及校准音。一次一个任务，至少预留5GiB独立临时空间作为规划余量；这不是硬配额。完整CPU运行时间与内存尚未测量。生产适配器本身没有全树900秒限制；验收使用了独立的一次性watchdog及资源监测，不能把验收包装器的限制误认为所有用户运行的默认能力。本工具不是恶意媒体或同UID恶意并发文件系统操作的安全沙箱。

## 测试状态

已发布基线706ba2a（不是本次选择性整合后的新入口）曾在全新的隔离目录完成一次真实NVENC完整链：28张合成照片＋4段合成视频，成片240.233333秒、47,965,129字节；1920×1080、H.264/yuv420p/limited BT.709，AAC音轨。生产门禁与独立完整解码均使用-xerror并通过，独立解码错误日志为空。28张静态检查通过，代表全分辨率SSIM=1.0，1920×1080封面通过，全部合成输入SHA不变，第二次同输入运行复用且影片/封面mtime不变。

基线普通与-O模式各18项回归通过。本次选择性整合后的新入口普通与-O模式各22项回归通过，新增视频提前边界、回执schema及旧回执不改写检查；没有重复全长制片。实际可恢复损坏H.264负例证明保留的严格门禁会拒绝；单元测试中mock范围不计作真实媒体验收。

完整worker耗时281.872秒，含独立监测总283.572秒；两秒采样任务树RSS峰值约431.0MiB、测试目录约270.5MiB。这是一次指定环境观测，不是瞬时峰值保证。验收没有触发900秒、内存、磁盘或压力停止阈值。

实测Python版本为3.13.5（本地回归）和3.13.15（完整验收），未测试3.11兼容性。CPU全长、全新OS安装、实际播放器/电视/音箱、持续稳定性、排程及通知均未验。

## 许可与外部播放器

用户拥有的项目原创采用MIT，署名Harry Water，详见LICENSE-MIT.txt、LICENSE-SCOPE.md和PROVENANCE.json。交付声明项目/AI协作编写；未声称完成全库逐行版权审计，也不把缺Git历史本身当法律障碍。保留实际第三方条款，不授予作者没有的权利。

屏保/player/launcher/XML及生产集成不在本版本中；通用runtime来自项目制片源码，来源基线有哈希记录。可用独立安装的兼容播放器打开MP4，但实际播放、设备显示及声音不在本次已测范围。
