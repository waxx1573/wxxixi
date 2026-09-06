# wxxixi

wxxixi 当前版本使用 WeFlow 和 Akasha-WeChat 接入微信，通过 OneBot v11 / `aiocqhttp` 连接 AstrBot。AstrBot 负责模型、会话、人格、知识库、工具和标准消息结构，Smart Core 负责群级回复决策，桥接层负责微信收发和消息转换。

## 目录

- `wechat-weflow-bridge-ob11`：WeFlow 到 AstrBot 的微信桥接器。
- `astrbot_plugin_wechat_group_manager`：微信群权限、状态、软审核和审计。
- `astrbot_plugin_smart_core`：每群启停、回复策略和群级配置页面。
- `astrbot_plugin_qiuse_model_roles`：模型角色与辅助模型降级。
- `astrbot_plugin_wechat_bridge`：AstrBot 内的微信桥接配置入口。

## 当前状态

- AstrBot 适配版本：`4.28.0-beta.1`。
- 管理员白名单与 `/wx` 命令路由已通过真实微信群和插件审计验收。
- 公告功能尚未实现。
- AstrBot `At` / `Reply` 到微信的完整转换尚未验收。
- “引用消息后由机器人置顶”保持待定，不能用会话置顶代替。

## 验证

```powershell
python -m unittest discover -s wechat-weflow-bridge-ob11/tests -v
node --test astrbot_plugin_smart_core/tests/dashboard.test.cjs
```

本仓库不提交本机配置、模型令牌、微信会话内容、数据库、日志或媒体缓存。

桥接器基于 [alingalingling/Akasha-WeChat](https://github.com/alingalingling/Akasha-WeChat)，其许可证保留在 `wechat-weflow-bridge-ob11/LICENSE`。
