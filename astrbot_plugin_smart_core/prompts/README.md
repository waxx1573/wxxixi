# Smart 微信提示词 v1

参考：qlsg `/opt/tgaa/prompt/` 的 `decision.md`、`moderation.md`、`casual.md`、`manage_intent.md`，2026-09-07 实际读取。上游为 [Smart_Group_Bot](https://github.com/Hamster-Prime/Smart_Group_Bot)。这是中文微信适配版，不是上游逐字副本。

这些文件是 Smart 的四类提示词资源，但 AstrBot 原生只注册一份面向群聊的 Bot 人格：

| 类型 | 文件 | 用途 |
| --- | --- |
| AstrBot Persona | `casual.md` | `pipi` 唯一的聊天人格 |
| Smart 内部提示词 | `decision.md` | 回复分类，只输出 `skip/casual` |
| Smart 内部提示词 | `moderation.md` | 审核分类，只输出 JSON |
| Smart 内部提示词 | `manage_intent.md` | 管理意图分类，只输出 JSON |

后三项禁止配置成面向群成员的聊天人格。它们由 Smart Core 在需要时作为分类器的 `system_prompt` 使用；不能绑定到会话，也不应出现在 AstrBot 的人格选择列表中。

微信版不保留 Telegram 身份标记和处罚动作，不使用 [[SPLIT]] 私有协议，不推断所有者身份，不把普通群文字当作群公告。知识库、记忆、工具和权限以 AstrBot 实际能力为准。

初始化只维护 `pipi` 这一份原生人格：若发现旧版 `Smart-WeChat-Casual-v1`，先迁移其已编辑内容；随后删除旧的四个 Smart 人格记录，并把默认人格指向 `pipi`。不会删除其他用户人格，也不会覆盖已经存在的 `pipi`。微信群请求读取 `pipi` 的当前内容；内部分类提示词仍来自插件资源文件。
