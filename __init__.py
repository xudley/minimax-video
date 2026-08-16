"""MiniMax 视频生成插件（VideoGenProvider backend 模式）。

内置 ``video_generate`` 工具分发到本 provider（name=minimax），
注册后内置工具的 check_fn 通过 → 工具对 agent 可见。

能力：
1. ``video_generate``（内置）→ MiniMax video-01 文生视频/图生视频（同步轮询）
2. ``video_check``（本插件）→ 查询历史 task_id（轮询超时时用）
3. ``pre_gateway_dispatch`` rewrite：检测"动起来/视频"请求，强制引导 video_generate

依赖：MINIMAX_API_KEY（token plan，每日 3 条视频限额，用尽即停）；config.yaml 需 `video_gen.provider: minimax`
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

try:
    from agent.video_gen_provider import VideoGenProvider as _VideoGenProviderABC
except Exception:  # noqa: BLE001
    _VideoGenProviderABC = object  # 非 gateway 环境退化

PLUGIN_NAME = "minimax-video"

VIDEO_API_URL = "https://api.minimaxi.com/v1/video_generation"
VIDEO_QUERY_URL = "https://api.minimaxi.com/v1/query/video_generation"
MAX_POLL_SECONDS = 150  # 视频生成一般 1-3 分钟，轮询上限 150s

# 聊天类型自动识别（与 minimax-image 相同机制）
_CHAT_TYPE_CACHE: dict = {}
_LAST_CHAT: dict = {}


def _read_env() -> dict:
    home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    env_path = os.path.join(home, ".env")
    vals: dict = {}
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _retrieve_url(key: str, file_id: str) -> str:
    """用 file_id 换取视频下载链接（/v1/files/retrieve，URL 有效期 1 小时）。"""
    if not file_id:
        return ""
    url = f"https://api.minimaxi.com/v1/files/retrieve?file_id={file_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read())
        return (d.get("file") or {}).get("download_url", "")
    except Exception:  # noqa: BLE001
        return ""


def _normalize_image_ref(image_ref: str) -> str:
    """把图片引用规范为 MiniMax 可用形式：公网 URL / data URI / 本地路径→base64。"""
    if not image_ref:
        return ""
    ref = image_ref.strip()
    if ref.startswith("http") or ref.startswith("data:"):
        return ref
    if os.path.exists(ref):
        ext = os.path.splitext(ref)[1].lower()
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
        }.get(ext, "image/jpeg")
        try:
            b64 = base64.b64encode(open(ref, "rb").read()).decode()
            return f"data:{mime};base64,{b64}"
        except Exception:
            return ref
    return ref


def _learn_chat_type(**kwargs):
    """pre_gateway_dispatch hook：学习 chat_id → chat_type + 记录最近对话。"""
    try:
        event = kwargs.get("event")
        if event is None:
            return None
        src = getattr(event, "source", None)
        if src is None or getattr(src, "platform", None) is None:
            return None
        if getattr(src.platform, "value", "") != "qqbot":
            return None
        chat_id = getattr(src, "chat_id", None)
        chat_type = getattr(src, "chat_type", None)
        if chat_id:
            if chat_type:
                _CHAT_TYPE_CACHE[chat_id] = chat_type
            _LAST_CHAT.update({"chat_id": chat_id, "chat_type": chat_type or "group"})
    except Exception:
        pass
    return None  # 不干预消息分发


_VIDEO_TRIGGERS = ("动起来", "让她动", "让他动", "让它动", "动一动", "会动", "生成视频", "做视频", "视频生成", "做个动画")


def _video_dispatch_rewrite(**kwargs):
    """检测视频生成请求，rewrite 消息强制引导 agent 用 video_generate。"""
    try:
        event = kwargs.get("event")
        if event is None or getattr(event, "internal", False):
            return None
        text = (event.text or "").strip()
        if not any(k in text for k in _VIDEO_TRIGGERS):
            return None
        media = getattr(event, "media_urls", None) or []
        img_path = media[0] if media else ""
        img_hint = (
            f"\n图片本地路径: {img_path}\n"
            "（video_generate 的 image_url 直接传这个路径，插件会自动转成 MiniMax 可用的格式）"
            if img_path
            else ""
        )
        new_text = (
            f"[视频生成请求] 用户说: {text[:100]}{img_hint}\n"
            "请使用 video_generate 工具生成视频（不要用 generate_image）。\n"
            "- prompt：描述动作/场景\n"
            "- 图生视频时 image_url 传上面的图片路径\n"
            "- 如返回 task_id 表示仍在生成，稍后用 video_check 查询"
        )
        return {"action": "rewrite", "text": new_text}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# VideoGenProvider
# ---------------------------------------------------------------------------


class MiniMaxVideoProvider(_VideoGenProviderABC):
    """MiniMax video-01 backend（name=minimax）。"""

    name = "minimax"
    display_name = "MiniMax Video"

    def is_available(self) -> bool:
        return bool(_read_env().get("MINIMAX_API_KEY"))

    def list_models(self):
        return [{"id": "video-01", "name": "video-01"}]

    def default_model(self):
        return "video-01"

    def generate(self, prompt, *, model=None, image_url=None,
                 reference_image_urls=None, duration=None,
                 aspect_ratio="16:9", resolution="720p",
                 negative_prompt=None, audio=None, seed=None, **kwargs):
        """提交 MiniMax 视频任务并同步轮询。"""
        try:
            from agent.video_gen_provider import success_response, error_response
        except Exception:
            success_response = lambda **kw: kw
            error_response = lambda **kw: {"success": False, **kw}

        env = _read_env()
        key = env.get("MINIMAX_API_KEY", "")
        if not key:
            return error_response(error="MINIMAX_API_KEY 未配置", provider=self.name, model=model or "video-01", prompt=prompt)

        # 图生视频：image_url 或 reference_image_urls 第一张
        ref = ""
        if image_url:
            ref = _normalize_image_ref(image_url)
        elif reference_image_urls:
            ref = _normalize_image_ref(reference_image_urls[0])

        dur = int(duration or 6)
        if dur not in (6, 10):
            dur = 6

        payload = {"model": model or "video-01", "prompt": prompt[:2000], "duration": dur}
        if ref:
            payload["image_url"] = ref

        # 提交
        try:
            req = urllib.request.Request(
                VIDEO_API_URL,
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                sub = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return error_response(error=f"提交失败 HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}",
                                  provider=self.name, model=model or "video-01", prompt=prompt)
        except Exception as e:  # noqa: BLE001
            return error_response(error=f"提交失败: {e}", provider=self.name, model=model or "video-01", prompt=prompt)

        task_id = sub.get("task_id", "")
        if not task_id:
            return error_response(error=f"无 task_id: {str(sub)[:200]}", provider=self.name, model=model or "video-01", prompt=prompt)

        # 同步轮询
        deadline = time.time() + MAX_POLL_SECONDS
        while time.time() < deadline:
            time.sleep(8)
            try:
                qreq = urllib.request.Request(
                    f"{VIDEO_QUERY_URL}?task_id={task_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                with urllib.request.urlopen(qreq, timeout=30) as resp:
                    q = json.loads(resp.read())
            except Exception:  # noqa: BLE001
                continue
            status = q.get("status", "")
            if status == "Success":
                video_url = (q.get("data") or {}).get("video_url") or ""
                if not video_url:
                    # 查询只返回 file_id，需用 /v1/files/retrieve 换取下载链接
                    video_url = _retrieve_url(key, q.get("file_id", ""))
                if video_url:
                    return success_response(
                        video=video_url,
                        model=model or "video-01",
                        prompt=prompt,
                        modality="image" if ref else "text",
                        duration=dur,
                        provider=self.name,
                        extra={"task_id": task_id, "file_id": q.get("file_id", "")},
                    )
                return error_response(error=f"任务完成但无法获取下载链接: {str(q)[:200]}", provider=self.name,
                                      model=model or "video-01", prompt=prompt)
            if status in ("Failed", "Cancelled", "Error"):
                return error_response(error=f"任务失败 ({status}): {str(q)[:200]}", provider=self.name,
                                      model=model or "video-01", prompt=prompt)

        return error_response(
            error=f"仍在生成中（task_id={task_id}，生成需 1-3 分钟）。稍后用 video_check 查询该 task_id。",
            provider=self.name, model=model or "video-01", prompt=prompt,
            extra={"task_id": task_id},
        )


# ---------------------------------------------------------------------------
# video_check 工具（查询历史任务）
# ---------------------------------------------------------------------------


def _api_get(url: str) -> dict:
    env = _read_env()
    key = env.get("MINIMAX_API_KEY", "")
    if not key:
        return {"success": False, "error": "MINIMAX_API_KEY 未配置"}
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"success": True, **json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:250]}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def _tool_video_check(args: dict, **kw) -> str:
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return json.dumps({"success": False, "error": "task_id 必填"}, ensure_ascii=False)
    env = _read_env()
    q = _api_get(f"{VIDEO_QUERY_URL}?task_id={task_id}")
    if not q.get("success"):
        return json.dumps(q, ensure_ascii=False)
    status = q.get("status", "")
    out = {"success": True, "task_id": task_id, "status": status}
    if status == "Success":
        out["video_url"] = (q.get("data") or {}).get("video_url") or ""
        if not out["video_url"]:
            key = env.get("MINIMAX_API_KEY", "")
            out["video_url"] = _retrieve_url(key, q.get("file_id", ""))
    return json.dumps(out, ensure_ascii=False)


TOOL_SCHEMA_CHECK = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "video_generate 返回的任务 ID"},
    },
    "required": ["task_id"],
}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _learn_chat_type)
    ctx.register_hook("pre_gateway_dispatch", _video_dispatch_rewrite)
    try:
        ctx.register_video_gen_provider(MiniMaxVideoProvider())
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("minimax-video provider 注册失败: %s", e)
    ctx.register_tool(
        name="video_check",
        toolset="video",
        schema=TOOL_SCHEMA_CHECK,
        handler=_tool_video_check,
        is_async=False,
        description="查询 video_generate 提交的视频任务状态；完成后返回 video_url",
        emoji="⏳",
    )
