---
name: mytv-family-tv
description: Configure and operate MyTV's local collect-edit-deliver workflow to automatically curate a daily memory programme, render and verify it, and safely update a playback directory. Use existing local media and confirmed metadata; no cloud login or device control.
---

# MyTV：我的家庭电视台

一个节目编导Skill，统一“采—编—播”。按需读[三章总手册](README.md)。使用scripts/pipeline.py确定性程序，不依赖永久模型会话。采从本地已有素材开始，不含拍摄/云账号下载；编配置日期、回退和确认标签偏好并解释计划；播只校验获取与切换，不冒称实际播放。

配置本地库及互不包含的状态/工作/发布/播放根、合法外部字体。原件不改不删，未知日期不以mtime补齐；支持[侧车schema](schemas/sidecar.schema.json)、[完整配置](examples/pipeline.json)。嵌入照片EXIF/GPS仅在已有ExifTool时可读，实测JPEG；无偏移时间保持unknown，容器时间默认候选，显式政策才用于选材。严格解码超时的媒体不自动入选，不直接称损坏。只用已有确认人物标签，不识别人脸或上传家庭素材。

先preflight、index、plan，再run；素材不足明确失败保留上一期。普通版把用户有权使用的音乐合入MP4；米家立体声只有进阶说明，不控制设备。每日调度调用同一run命令，由用户部署，不擅自创建服务。

原始EXIF/GPS留在私密索引；pipeline派生画布先校正方向/合成sRGB后剥离元数据，实际发布/获取封面须检查，不把任意独立cover-source当作已脱敏。

中断先核同日journal/产物，按原意图有界恢复，不换ID盲目重放、不删锁或旧片。doctor不是完整就绪。分别报告索引、计划、制片质检、发布、客户端验证、实际播放；确认实际播放后才记played。源码变更不能复用旧验收，mock不能算真实影片。

本地计划/索引/日志含私人路径/标签，不公开。公开证据只用合成素材和通用能力条件，不夹带个人邮箱、设备/IP或家庭路径。安装、云凭据、网络服务、生产排程/电视修改仍需对应授权。
