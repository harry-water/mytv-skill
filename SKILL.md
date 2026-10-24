---
name: mytv-family-tv
description: Generate a local three-to-five-minute memory film and JPEG cover from a reviewed local selection; test with synthetic inputs. Does not control televisions or schedule playback.
---

# MyTV：我的家庭电视台

本版本仅本地制片与封面。先读README.md的依赖、配置、测试状态及限制。用scripts/run.py doctor/render处理人工确认的选片表，输出到输入树之外的新私密目录，至少包含一张照片。

保留原件；不用mtime猜拍摄日期，不虚构人物或行程关系。字体和输出路径须通过滤镜特殊字符校验。部分输出不自动清理或覆盖。

普通与-O模式各18项回归通过，最终严格解码完整NVENC合成验收通过；CPU仅短小样，不代表完整CPU制片通过。分别报告制片、封面、实际播放、屏幕/声音、排程与通知，不把未测项目标通过。

不处理未审恶意媒体或共享/敌对输出目录。不安装依赖、控制设备、上传云端、创建服务或改排程。许可范围见LICENSE-SCOPE.md与PROVENANCE.json；原创MIT授权不扩张第三方权利。
