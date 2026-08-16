# minimax-video — MiniMax 视频生成插件

基于 MiniMax video-01 的视频生成 backend（文生视频/图生视频），接入 hermes 内置 `video_generate` 工具。

## 功能

- **VideoGenProvider backend**（name=minimax）：内置 `video_generate` 工具分发到本插件
  - 文生视频：`prompt` + `duration`（6/10 秒）
  - 图生视频：`image_url`（参考图，支持本地路径自动转 base64）
  - 同步轮询最长 150s；超时返回 `task_id`
- `video_check` 工具：查询历史任务（`status` + 完成后 `video_url`）
- 视频结果获取：查询返回 `file_id` → `GET /v1/files/retrieve?file_id=` 换 `download_url`（1 小时有效）
- `pre_gateway_dispatch` rewrite：检测"动起来/视频/动画"等触发词，把消息改写为明确指令引导 agent 用 `video_generate`（防 agent 误用 generate_image）

## 安装

```bash
hermes plugins enable minimax-video
hermes gateway restart
```

必须配置（config.yaml）：

```yaml
platform_toolsets:
  qqbot: [hermes-qqbot, video_gen]   # 启用内置 video_gen 工具集（默认关闭！）
video_gen:
  provider: minimax
```

> 注意：`video_gen` 在 hermes 的 `_DEFAULT_OFF_TOOLSETS`（默认关闭）——不启用则内置 `video_generate` 对 agent 不可见。

## 配置

`.env`（`~/.hermes/.env`）：

```
MINIMAX_API_KEY=你的 MiniMax 官方 key（token plan，每日 3 条视频限额，用尽即停）
```

## 使用

配合 `~/.hermes/skills/video-generation/SKILL.md`：

```
video_generate(prompt="橘猫在草地上打滚", duration=6)
video_generate(prompt="画面动起来", image_url="图片路径或URL")
video_check(task_id="...")   # 轮询超时后查询
```

## 注意事项

- token plan 每日限额 3 条视频，用尽后 API 报错（不自动 fallback 付费 key）
- 视频生成需 1-3 分钟（排队 + 渲染），150s 内未完成返回 task_id
- 完成后返回 `video_url`（1 小时有效），agent 发链接给用户

## 配套 Skill

`skill/SKILL.md` 是配套的 hermes skill（安装到 `~/.hermes/skills/<name>/SKILL.md`），
教 agent 何时使用本插件工具及正确用法（含角色特征检索、触发词等）。
