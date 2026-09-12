# wxxixi

wxxixi 当前版本使用 WeFlow 和 Akasha-WeChat 接入微信，通过 OneBot v11 / `aiocqhttp` 连接 AstrBot。AstrBot 负责模型、会话、人格、知识库、工具和标准消息结构，SpectreCore 负责群级语义参与决策，桥接层负责微信收发和消息转换。

## 目录

- `wechat-weflow-bridge-ob11`：WeFlow 到 AstrBot 的微信桥接器。
- `astrbot_plugin_self_learning`：群风格学习、记忆和相关学习链路。
- `astrbot_plugin_stealer`：图片/表情采集、审核、检索和发送（线上插件，源码不在本仓库）。
- `astrbot_plugin_smart_core`：历史群策略与管理页面源码，线上已停用，当前仅保留迁移/回退证据。
- `astrbot_plugin_wechat_group_manager`：历史自研群管理源码，线上已停用；本地改动和测试作为迁移证据暂保留，不代表线上启用。

## 当前状态

- AstrBot 适配版本：`4.28.0`；当前镜像为 `astrbot-goofish:4.28.0-playwright-sensevoice-mem0`。
- 管理员白名单与 `/wx` 命令路由已通过真实微信群和插件审计验收。
- 公告、置顶、TTS、微信原生语音出站、视频出站和 AstrBot `At` / `Reply` 微信出站均已取消，不作为当前目标。
- 图片/表情能力仍为部分通过，必须继续做真实图片查看与送达验收；普通文字和语音入站转文字回复已有真实通过证据。

## 验证

```powershell
python -m unittest discover -s wechat-weflow-bridge-ob11/tests -v
node --test astrbot_plugin_smart_core/tests/dashboard.test.cjs
```

本仓库不提交本机配置、模型令牌、微信会话内容、数据库、日志或媒体缓存。

`astrbot_plugin_self_learning` 在本地作为独立 Git 仓库维护，不嵌套提交到本仓库；其本地提交和上游同步应在独立仓库内处理。

桥接器基于 [alingalingling/Akasha-WeChat](https://github.com/alingalingling/Akasha-WeChat)，其许可证保留在 `wechat-weflow-bridge-ob11/LICENSE`。
