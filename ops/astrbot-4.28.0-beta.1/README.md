# AstrBot 4.28.0-beta.1 部署资料

本目录保存当前 qlsg 定制镜像的可复现构建、升级切换、数据库备份和验收脚本。

- `Dockerfile.astrbot-4.28-beta1`：基于固定官方摘要加入 Playwright、Chromium 和 SenseVoice 依赖。
- `prepare_astrbot_428.sh`：升级前保存配置、插件和数据库回退点，并拉取官方镜像。
- `switch_astrbot_428.sh`：校验 Compose 后只重建 AstrBot 服务。
- `validate_astrbot_428_image.py`：验证镜像内版本、浏览器和语音依赖。
- `validate_astrbot_428_live.py`：验证容器、管理端、数据库、插件配置和近期日志。
- `backup_astrbot_db.py`：使用 SQLite backup API生成升级前数据库副本。

脚本按 qlsg 当前 `/root/astrbot` 部署路径编写。再次使用前必须核对服务器路径、镜像版本和回退目录，不能直接用于其他机器。
