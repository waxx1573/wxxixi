# AstrBot 4.28.0 / Mem0 固定镜像

目录和脚本保留历史文件名，实际基础版本为 **4.28.0 正式版**。原运行容器的镜像标签及安装元数据仍写 beta，但逐文件核对证明核心已更新为正式版；不能只按标签选择构建基线。

- 基础镜像固定为 `soulter/astrbot@sha256:cf271483d2f03d2220b2c1ec5574747f4ebbdba27396421ef2e785474255856e`。
- 目标镜像为 `astrbot-goofish:4.28.0-playwright-sensevoice-mem0`。
- `runtime-python-requirements.txt` 固定 202 个包版本；除 AstrBot 元数据对应已有正式版核心外，其余版本保留原运行环境，包括 Mem0 1.0.11、Qdrant 1.19.0、protobuf 6.33.6、OpenAI 3.6.0 和 ONNX Runtime 1.29.0。
- `build_astrbot_428_mem0.sh` 只将 Dockerfile 和依赖清单打包送入构建器，不发送数据、配置或备份目录；构建后验证浏览器、依赖、源文件和隔离 Mem0 读写。
- `verify_astrbot_image_packages.py` 同时比较原运行容器与候选镜像的包版本、核心 Python 文件及主入口；源码有差异就停止。只允许已核验的 beta 主包元数据更正，仍必须通过源码一致性检查。
- `create_astrbot_recovery.py` 创建唯一 `pre-mem0-runtime` 本机回退镜像；还原或清空运行时额外注入的环境变量，不将 Compose 注入的凭据固化。数据挂载不进入镜像，该镜像不上传。
- `switch_astrbot_428_mem0.sh` 只重建 AstrBot；配置、启动、就绪或依赖检查失败均回退，并检查 NapCat ID 和启动时间不变。若当前已运行该回退镜像，验证后直接复用，不重复创建。
- `validate_astrbot_428_live.py` 检查 self-learning 的 Mem0 配置、管理端、数据库、插件补丁和日志计数，不依赖已经退出当前环境的微信群管理配置文件，不输出聊天或完整日志。
- `test_mem0_switch.py` 覆盖成功、复用回退镜像、四种失败回退及环境变量脱敏。

## 部署与复用

1. 核对实际源码、当前镜像、插件补丁、依赖、磁盘和有效恢复点。后续框架升级须重新生成并审核清单，不能用旧清单压回较新的环境。
2. 将 Dockerfile 放到 `/root/astrbot/Dockerfile.astrbot-goofish`，其余运行脚本和 `runtime-python-requirements.txt` 放到 `/root/astrbot`，另复制 `../test_mem0_takeover.py`。所有文件使用 UTF-8 与 LF。
3. 运行 `sh /root/astrbot/build_astrbot_428_mem0.sh`。原服务继续运行；隔离 Mem0 验证禁用网络，只读挂载插件源码，测试数据写入临时容器。
4. 所有检查通过后，在既定项目授权范围内执行 `sh /root/astrbot/switch_astrbot_428_mem0.sh`。预计中断超过 5 分钟或影响其他项目时，先确认。就绪截止 130 秒，依赖检查各 15 秒；失败回退有独立超时，不保证恢复一定成功。
5. 运行 `python3 /root/astrbot/validate_astrbot_428_live.py`，回读配置校验值、原有 Mem0 记录、挂载及插件状态。保留日志轮转、模型缓存和真实业务限制。

self-learning 完整补丁见 `../patches/0001-fix-serialize-self-learning-memory-ingestion.patch`，上游基线与应用方法见 `../self-memory-takeover.md`。镜像不会自动覆盖数据挂载中的插件源码。健康检查和隔离测试不能替代微信真实业务验收。

`prepare_astrbot_428.sh`、`switch_astrbot_428.sh` 与 `backup_astrbot_db.py` 是历史升级资料，本次固化不重复执行旧备份流程。

本机测试：设置 `TEST_SH` 为实际安装的 sh 路径，然后运行 `python ops/astrbot-4.28.0-beta.1/test_mem0_switch.py`。测试不连接服务器或 Docker。
