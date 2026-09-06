# Smart 微信提示词 v1

参考：qlsg `/opt/tgaa/prompt/` 的 `decision.md`、`moderation.md`、`casual.md`、`manage_intent.md`，2026-09-07 实际读取。上游为 [Smart_Group_Bot](https://github.com/Hamster-Prime/Smart_Group_Bot)。这是中文微信适配版，不是上游逐字副本。

插件初始化时通过 AstrBot PersonaManager 添加四个人格，已有同名人格保持原样，避免覆盖管理页修改：

| 人格 ID | 用途 |
| --- | --- |
| Smart-WeChat-Casual-v1 | 可选择的日常聊天人格 |
| Smart-WeChat-Decision-v1 | 内部回复分类，只输出 skip/casual |
| Smart-WeChat-Moderation-v1 | 内部审核分类，只输出 JSON |
| Smart-WeChat-ManageIntent-v1 | 内部管理意图分类，只输出 JSON |

后三项禁止配置成面向群成员的聊天人格。它们禁用工具和技能，仅作为后续群决策调用的提示词资源；添加人格不等于分类处理器已接通。

微信版不保留 Telegram 身份标记和处罚动作，不使用 [[SPLIT]] 私有协议，不推断所有者身份，不把普通群文字当作群公告。知识库、记忆、工具和权限以 AstrBot 实际能力为准。

初始化不更改默认人格或其他会话绑定。真实验收需先给指定微信测试会话选择 Casual，再取得真实微信群结果及服务端证据。本版本添加和加载必须分别验收，不能用静态检查代替真实消息验收。
