# MyTV：我的家庭电视台

Harry Water · 1.0.0

本版本包含本地素材索引、确定性节目编排、受限资源制片质检及校验后更新。版本说明与实际验收边界见[RELEASE-NOTES.md](RELEASE-NOTES.md)，新完整链证据见evidence/metadata-revision-acceptance.json；历史证据单独保留。

统一“采—编—播”：每日从本地已有内容库自动选材，清晨生成“历史上的今天”。程序确定性执行；单一Skill负责配置、编导策略、调用和解释，不依赖永久模型会话。云相册、数码相机、DJI等内容由用户已有流程下载到本地，本包不拍摄、不登录云账号、不上传家庭素材、不新增人脸识别。

## 一、采：本地素材入库

原件只读。library/state/work/publish四个根必须互不包含；后面三个是当前用户拥有的0700私密目录，程序不替用户修改已有目录权限。路径、预算均可配置，无指定个人硬盘容量、GPU型号或驱动版本要求。索引不扫描工作产物，不删除文件。

接受JPG/JPEG/PNG、MP4/MOV/MKV后缀，实际仍须已有解码器通过尺寸与严格解码门禁。**嵌入EXIF/GPS本轮实际验证的是JPEG，容器时间验证的是MP4**；PNG嵌入EXIF及MOV/MKV各类拍摄时间字段没有同等验证。HEIC/HEIF、RAW、TIFF及厂商专有格式不在本版枚举中，不能宣称全格式入库。

已有ExifTool时只读请求DateTimeOriginal、OffsetTimeOriginal、CreateDate及GPS字段，不读取设备/所有者字段。无ExifTool时照片嵌入EXIF能力明确不可用，仍可使用支持的相邻`MEDIA.ext.mytv.json`侧车；不安装依赖。格式见[侧车schema](schemas/sidecar.schema.json)和[合成示例](examples/photo.jpg.mytv.json)。

| 来源 | 日期/冲突规则 |
|---|---|
| 侧车capture_time | 优先，必须带偏移才能换算节目日期；即使有IANA名称也不猜本地时间的夏令时重叠/空缺。与IANA偏移矛盾时日期unknown。 |
| 侧车capture_date | 没有capture_time时使用明确提供的日历日期；优先于嵌入信息，差异记time_conflicts。 |
| 照片EXIF原始拍摄时间 | DateTimeOriginal+OffsetTimeOriginal换算配置时区；无偏移时保存本地原始时间线索，但选片日期unknown。CreateDate仅是数字化/创建提示，不冒充拍摄日。 |
| 视频creation_time | 默认仅unconfirmed候选，不自动选入。显式metadata.video_creation_time_policy=use-offset才接受带偏移且无相互冲突的容器时间；仍不保证相机时钟或容器语义准确。 |

mtime只用于缓存提示，从不充当确定拍摄日期。未知IANA时区拒绝。现有ExifTool可读GPS经有效南北/东西参考及范围验证后保存私密索引；确认侧车GPS优先，坐标不同记gps_conflicts。GPS不联网地理编码，不推断地名，与已确认地点标签不互相替代。只消费已有confirmed人物标签，不新增人脸识别、人物库解析或关系推断。


state_root/index.json记录相对路径、SHA、大小、媒体参数、时间来源/可信度、地点/人物标签；增量缓存未变文件，移动重新探测，要求正宽高（不超过1亿像素）并实际严格解码：照片首帧、视频完整首视频轨/可选首音轨；失败或每文件30秒完整解码预算不足（长视频/高分辨率可能被暂时排除）都排除并记录异常类型，超时不能直接认定文件损坏。增量schema/策略/ExifTool可用性变化强制重索引。选片按SHA去重，staging及发布前复核源SHA，不能靠保留mtime骗过。history.json分别记录published及调用方确认的played；生成计划或获取文件不算播放。

前置：Linux/POSIX（/proc、flock、进程组）、已有Python、FFmpeg/ffprobe、ImageMagick7 magick、合法外部字体，无pip依赖。FFmpeg需zscale/drawtext/xfade/acrossfade/ssim、AAC及可用h264_nvenc；CPU/libx264是实验分支，未确认全长性能。实际软件版本及验证范围见DEPENDENCIES.json。可选Nix配方仅静态审查，不是干净安装通过；不自动安装或改驱动，不附字体。不是恶意媒体或同UID攻击者沙箱。

普通用户安装源码到全新私密目录：先按交付清单核ZIP的SHA，确认是本项目已审包，再解压；这些命令是部署说明，本任务没有执行系统安装。依赖和字体须已存在，不自动下载。

```sh
umask 077
mkdir -m 700 /absolute/new-install
python3 -m zipfile -e /absolute/verified-source.zip /absolute/new-install
cd /absolute/new-install/mytv-family-tv
```

复制[完整配置示例](examples/pipeline.json)到源码树之外，填写绝对路径与IANA时区；[配置schema](schemas/pipeline.schema.json)加运行时preflight约束共同定义有效配置。字体及工作路径拒绝滤镜特殊字符；中文空格可用。

```sh
python3 scripts/pipeline.py preflight --config /absolute/pipeline.json
python3 scripts/pipeline.py index --config /absolute/pipeline.json
python3 scripts/pipeline.py plan --config /absolute/pipeline.json --date 2030-10-24
```

preflight核配置/时区/空间，plan核节目可行性并解释回退；旧run.py doctor仅探测工具/字体/编码能力，不代表完整就绪。

## 二、编：MyTV节目编导 Skill

历史同日优先，按策略允许同月回退；闰日优先回退2月28日需显式leap_day_fallback=feb28；若同月回退开启，其余2月日期（包括2月28日）仍可按同月规则纳入。同一级内优先匹配配置preferred_locations/preferred_people的确认标签，再按日期接近程度、较近年份、SHA排序。仅标签偏好，不是语义识别；字幕目前只用拍摄日期，地点字幕回执false。近期发布/确认播放的素材按recent_days排除。最多46项有限循环，需至少一张照片并足量不同素材满足180–300秒；不足失败，保留上一期，不重复凑时长。

计划含日期、源SHA、选取理由、回退/排除计数、时长及代码/字体/音乐指纹；自动把所需副本放独立work_root，调用现有渲染与质检，无需每日人工挑片。公开渲染接口的[selection契约](schemas/selection.schema.json)仍可用于互操作。

```sh
python3 scripts/pipeline.py run --config /absolute/pipeline.json --date 2030-10-24
# 每日按配置时区当天：省略 --date
python3 scripts/pipeline.py run --config /absolute/pipeline.json
```

pipeline画布在自动方向校正和sRGB合成之后剥离源EXIF/IPTC等元数据，封面复制此已清理画布。私密索引仍保留所需GPS来源；这是pipeline路径的保护，不是任意图片脱敏器，独立prepare_video_presentation.py的任意外部cover-source仍原样复制。

普通单设备版可加配置`music={"file":"/absolute/licensed-track.wav","license_confirmed":true,"volume":0.18}`：合法音轨混入单个MP4，保留源视频音轨，无music则不加背景音乐。坏音乐阻止发布，音乐SHA入指纹。可解码不等于实体扬声器已验。独立米家立体声仅属进阶部署思路，用户保留原系统自行管理，本包不控制或同步设备。

run持有排他锁。通过前置门禁并进入worker的每次attempt均保存唯一目录intent与不可覆盖resource回执，resource-day.json仅是最新快照；复用不会覆盖首次attempt证据；前置配置/繁忙/缺依赖拒绝由CLI与调用方日志记录，不生成内部attempt。已完成相同日/配置可复用，但旧日重跑不得把较新current回退；变更配置/代码/字体拒绝悄悄续接旧意图。中断后重跑同一命令先对账日journal；首次部分渲染保留为interrupted-render，允许一次重新渲染，第二次部分失败需检查，不无限重试/删锁/清旧片。目录提交先fsync版本父目录再提交current；已rename影片缺receipt时重验后对账。仍不保证所有硬件/文件系统断电行为，不是阶段级断点续编码，SIGKILL/断电只能下次对账，未声称全故障恢复。生成、发布、获取、实际播放分开记录。

默认整个自有进程树900秒超时、TERM后最多20秒KILL；单文件512MiB硬上限。状态/工作/发布聚合字节、RSS、可用内存/负载/PSI/磁盘是0.5秒采样阈值而非硬配额，只停本任务后代，不杀其他工作，不自动跑长CPU降级。预算可配，需按使用环境评估。失败日志/回执留在私密state_root；本包不创建系统服务。

合成验收步骤（新目录，只生成几何图案、测试视频和合成音，不读家庭照片）：

```sh
python3 scripts/make_demo.py --root /absolute/new-demo --font /absolute/existing-licensed-font.otf --backend nvenc --date 2030-10-24 --music
python3 scripts/pipeline.py preflight --config /absolute/new-demo/pipeline.json
python3 scripts/pipeline.py index --config /absolute/new-demo/pipeline.json
python3 scripts/pipeline.py plan --config /absolute/new-demo/pipeline.json --date 2030-10-24
python3 scripts/pipeline.py run --config /absolute/new-demo/pipeline.json --date 2030-10-24
python3 scripts/pipeline.py fetch --source /absolute/new-demo/publish --player-root /absolute/new-player
```

回归：`python3 -m unittest discover -s tests -v`及`python3 -O -m unittest discover -s tests -v`。原24项全部保留，最终PC-1现有工具环境各57项通过；PC-5各54通过、3项因缺少ExifTool/ImageMagick跳过，不能把跳过项标通过。mock不算影片验收。

当前执行代码完成一次新的183.2秒音乐合成完整链：真实JPEG嵌入拍摄时间/GPS、显式MP4容器时间政策驱动自动选片，22素材、19静态照片质检，坏图片和截断视频两项实际排除；无偏移/无时间仍unknown，源SHA不变，严格独立全片解码、发布及获取通过。实际发布/获取的cover.jpg均无源GPS和DateTimeOriginal，首次220.901秒及复用1.086秒各有不可覆盖attempt证据。详情见[evidence/metadata-revision-acceptance.json](evidence/metadata-revision-acceptance.json)。

前期采—编—播验证快照的两条影片及旋转组件仅作历史记录，见evidence/synthetic-acceptance.json；首条元数据链未strip的结果也不能替代最终封面隐私验收。没有把旧片复制为本修订新渲染结果。未验证任意真实家庭库、全部方向/格式、新系统安装、CPU全长、长期运行或实际电视音画。


## 三、播：基础部署与安全更新

本版提供本地/已挂载目录的安全获取工具，不自建网络服务或托管凭据。使用者可沿现有已授权安全传输把发布树送达播放端，网络和播放器的安装配置须单独验收。没有内置云同步、跨机登录、Kodi/电视控制或唤醒。

```sh
python3 scripts/pipeline.py fetch --source /absolute/received-publication --player-root /absolute/private-player
```

先核pointer、manifest SHA、固定文件白名单及逐文件大小/SHA，完整校验后原子切换current.json。坏SHA、缺文件、中断、不合法路径保留旧current和上一期。播放器读取programme_id后打开`editions/ID/programme.mp4`，一轮结束/安全边界才重读pointer；本工具不启动、重启或修改播放器。manifest没有原始路径、人物地点标签或私人元数据；本地索引/计划/日志不公开。

外部播放器确实完成后，可由可信确认接口记录（不是设备探测）：

```sh
python3 scripts/pipeline.py played --config /absolute/pipeline.json --programme-id ACTUAL_64_HEX_ID --confirmed-played
```

已有cron且其触发时区已与目标清晨对齐时，可由用户自行用`crontab -e`加入以下一行；须替换所有绝对路径，先手工执行同一命令成功。cron的触发时区属于调度器配置，pipeline的timezone只决定节目日期，两者必须核对，不能假定通用cron支持CRON_TZ。以下不会由Skill自动注册：

```cron
15 5 * * * /absolute/python3 /absolute/mytv-family-tv/scripts/pipeline.py run --config /absolute/pipeline.json >> /absolute/private-state/scheduler.log 2>&1
```

每日调度只调用上述run的绝对Python/脚本/配置路径，不需要常驻模型。使用者自行审阅并验证时区、权限、工作目录、失败通知、留足制片时间后，才把命令注册到现有调度器。本任务未在任何电脑注册timer/service或改家庭排程。通知外部渠道、长期日志/保留清理、播放器适配、实体音画仍待部署验收；工具不自动清理文件。

原创由Harry Water以MIT授权，详见LICENSE-SCOPE.md与PROVENANCE.json，第三方条款保留。本文件说明源版本1.0.0的功能和验收范围，远端提交、tag及Release以GitHub实际记录为准。历史隐私清理由用户另行决定，不以新提交冒称历史资料已撤回。
