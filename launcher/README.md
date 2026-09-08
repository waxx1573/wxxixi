# 一键启动

双击 `WxPiPi.cmd`，WxPiPi 一键启动器会自动发现并启动：

1. 微信
2. WeFlow，并等待本地 API `5031`
3. WeFlow 微信桥接，并等待控制面板 `8766`
4. 在启动窗口输出桥接连接状态，不自动打开桥接网页

启动器不固定微信、WeFlow 或桥接项目的安装路径。发现结果缓存到 `%LOCALAPPDATA%\Akasha-WeChat\launcher-state.json`，只保存可执行文件路径和桥接目录，不保存桥接配置、Token、聊天内容或日志。

首次排查环境时可双击 `diagnose.cmd`。它只发现依赖，不启动程序。日常管理使用 AstrBot 插件页面；桥接网页仅作为独立排障入口。
