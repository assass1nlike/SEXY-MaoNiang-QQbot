# QQ 群自动回复机器人

这是一个基于 NoneBot2 + OneBot v11 的 QQ 群机器人。当配置的 QQ 成员在群里发言时，机器人会把消息发送到兼容 OpenAI Responses 的 API，并将回答回复到原消息。

## 配置

复制 `.env.example` 为 `.env`，至少填写：

- `TARGET_USER_IDS`：触发回复的 QQ 号，多个值用逗号分隔。
- `TARGET_GROUP_IDS`：可选，限制群号；留空表示所有群。
- `ALL_USERS_GROUP_IDS`：可选，在这些群中允许所有成员通过 `@机器人` 触发回复。
- `STATUS_GROUP_IDS`：接收上线和下线提醒的群号，多个群号使用逗号分隔；留空时关闭状态提醒。
- `ONLINE_MESSAGE`：机器人账号连接成功后发送的上线消息。
- `OFFLINE_MESSAGE`：程序正常关闭前发送的下线消息。
- `API_BASE_URL`：API 的 base URL，例如 `https://api.openai.com/v1`，程序会请求其 `/responses` 端点。
- `API_KEY`：API 密钥。不要提交到 Git。
- `API_MODEL`：模型名称。
- `SYSTEM_PROMPT`：顶层系统提示词，对应 Responses 的 `instructions`。
- `PUBLIC_SYSTEM_PROMPT`：仅供 `公共` 对话使用的系统提示词；留空时沿用 `SYSTEM_PROMPT`。
- `RATING_SYSTEM_PROMPT`：仅在个人对话中发送精确命令 `评分` 时使用的系统提示词。
- `PREFILL_USER` / `PREFILL_ASSISTANT`：可选的固定示例对话；两项都填写时启用。
- `PUBLIC_PREFILL_USER` / `PUBLIC_PREFILL_ASSISTANT`：仅供 `公共` 对话使用的固定示例对话；两项都填写时启用。字段值会作为完整消息原样传入，示例发言者的 `[群昵称]` 需要自行写入 `PUBLIC_PREFILL_USER`。
- `MAX_CONVERSATION_TURNS`：每个会话保留的最大已完成问答轮数，默认 `20`。

启用 prefill 后，每次请求的上下文顺序为：系统提示词、固定 user、固定 assistant、最近的真实对话历史、当前群消息。

个人对话使用 `PREFILL_USER` / `PREFILL_ASSISTANT`；公共对话改用 `PUBLIC_PREFILL_USER` / `PUBLIC_PREFILL_ASSISTANT`，两组不会混用。公共 prefill 不会自动添加群昵称，例如可将完整 user 消息写为 `"[示例成员]\n你好"`。

真实对话历史按“群号 + 用户 QQ”隔离并保存在内存中，最多保留最近 20 个已完成问答轮次；机器人进程重启后历史清空。目标用户或 `ALL_USERS_GROUP_IDS` 中的任意成员明确 `@机器人` 时才会触发回复，普通群消息不会调用 API。

发送下面这种格式时会进入该群的公共对话，`公共` 后必须立即换行，只有换行后的正文会发送给模型：

```text
@机器人 公共
这里是实际内容
```

公共对话按群号隔离，同一个群内的所有有权触发机器人的成员共享上下文，最多保留 500 条 `user` / `assistant` 消息。公共消息会自动以 `[群名片]` 开头；群名片为空时使用 QQ 昵称，不会传递 QQ 号或管理员身份。`ALL_USERS_GROUP_IDS` 中的群允许所有成员参与；其他群仍使用 `TARGET_USER_IDS` 和 `TARGET_GROUP_IDS` 的权限规则。公共历史与个人历史互不混用，进程重启后都会清空。

个人对话还支持两个精确命令：`@机器人 清空` 会直接删除该用户在该群的个人上下文，不调用 API；`@机器人 评分` 会使用当前个人上下文和 `RATING_SYSTEM_PROMPT` 生成一次回复，随后删除该个人上下文。命令后不能包含其他文字。公共格式中的 `清空` 和 `评分` 不作为命令处理。

如果服务商把完整的 `/responses` 写进了 base URL，程序也能直接使用。

## 安装和启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python bot.py
```

启动前需要运行一个支持 OneBot v11 的 QQ 实现（例如 NapCatQQ），并将反向 WebSocket 地址配置为 `ws://127.0.0.1:8080/onebot/v11/ws`。NoneBot 默认监听 `127.0.0.1:8080`；也可以按 OneBot 实现的配置修改。

机器人只处理群消息，不响应私聊；未配置 `TARGET_USER_IDS` 时机器人保持静默。

状态提醒在机器人账号成功连接 OneBot 后发送。使用 `Ctrl+C` 或其他正常关闭方式时，程序会在连接断开前尝试发送一次下线提醒；强制杀死进程、程序崩溃、断电或网络已经断开时无法保证发送。
