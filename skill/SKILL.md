---
name: video-generation
description: "生成视频/动画/让图片动起来/图生视频/短视频：用 video_generate 工具（MiniMax video-01）。用户发图说动起来、做动画、生成视频时使用。"
version: 1.0.0
author: xudley
metadata:
  hermes:
    tags: [视频, 动画, 生成, 短视频, 图生视频, MiniMax]
---

# 视频生成

当用户要求**生成视频**（动画、短视频、会动的图、图生视频等）时，使用 `video_generate` 工具。

## 正确用法

调用 `video_generate`，只需要传 prompt：
- `prompt`：视频内容描述（描述**动作**、场景、镜头——比如"猫咪在草地上打滚"）
- `duration`（可选）：6 或 10 秒，默认 6
- `image_url`（可选）：参考图 URL（图生视频——用户给了一张图让它动起来）

## 关键规则

1. **不要用 execute_code 生成视频**——用 `video_generate`。
2. 生成需要 **1-3 分钟**：工具内部轮询 90 秒。如果返回 `task_id` + "仍在生成中"，就调用 `video_check` 查询（可以告诉用户"视频还在制作，稍等我看看"再查）。
3. 完成后返回 `video_url`——把 URL 发给用户（视频无法直接发到 QQ 图片消息，发链接即可）。
4. 图生视频：用户提供了图片 URL 时传 `image_url`，prompt 描述"让画面动起来"的动作。

## 示例

用户："做一个猫咪在草地上打滚的视频"
→ `video_generate(prompt="橘猫在阳光明媚的草地上打滚，镜头跟随，自然光线")`
→ 等待/查询 → 发 video_url

用户：（发一张图）"让它动起来"
→ `video_generate(prompt="画面中的角色动起来，自然流畅", image_url="<图片URL>")`
