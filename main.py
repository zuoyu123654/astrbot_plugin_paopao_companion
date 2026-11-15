"""泡泡陪伴（桌宠控制台）。

设计原则：**采集和生成不在这个进程里**。
配套的本地「陪伴核心」（一个常驻 HTTP 服务，负责截屏、帧拼图、字幕 OCR、
音频转写、TTS、模式状态机）跑在自己进程里；这个插件只是遥控器：查状态、
切模式、体检、试听、必要时把它拉起来。

好处：聊天机器人重启不影响采集；核心崩了也不拖累机器人；插件随时可升级。

命令（私聊机器人）：
    陪伴                 看当前状态
    陪伴 医生            逐项体检
    陪伴 模式 anime      切模式（auto/normal/video/anime/live/course/off）
    陪伴 说 我在看呢      让她说一句
    陪伴 摘要            立刻整理一次识屏摘要
    陪伴 启动            拉起核心（如果没跑）
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_paopao_companion"
PLUGIN_VERSION = "0.2.0"

DEFAULTS = {
    "gateway_url": "http://127.0.0.1:9888",
    "start_script": "",
    "auto_start": False,
    "timeout_seconds": 12,
}

MODES = {
    "auto": "自动（按前台推断）",
    "normal": "平时（普通识屏）",
    "video": "视频（普通投稿/直播页）",
    "anime": "番剧",
    "live": "直播",
    "course": "网课",
    "off": "关闭（不念识屏）",
}

HELP = """陪伴控制台（这个插件只是遥控器）

  陪伴                看当前状态
  陪伴 医生           逐项体检（Key/TTS/转写/OCR/字幕/帧缓冲）
  陪伴 模式 <名字>     auto normal video anime live course off
  陪伴 说 <一句话>     让她立刻说（走本地 TTS）
  陪伴 摘要            立刻整理一次识屏摘要
  陪伴 启动            拉起核心服务（需先配 start_script）
"""


@register(PLUGIN_NAME, "paopao", "桌宠陪伴控制台：识屏/看番/陪看/直播网课的开关与体检", PLUGIN_VERSION)
class PaopaoCompanionPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.context = context
        cfg = config if isinstance(config, dict) else {}
        self.gateway = str(cfg.get("gateway_url") or DEFAULTS["gateway_url"]).rstrip("/")
        self.start_script = str(cfg.get("start_script") or "").strip()
        self.auto_start = bool(cfg.get("auto_start", DEFAULTS["auto_start"]))
        self.timeout = float(cfg.get("timeout_seconds") or DEFAULTS["timeout_seconds"])
        logger.info("[陪伴] 核心地址 %s（自动启动=%s）", self.gateway, self.auto_start)
        if self.auto_start:
            try:
                asyncio.get_event_loop().create_task(self._ensure_core_quiet())
            except RuntimeError:
                pass

    # ── HTTP ───────────────────────────────────────────────────
    async def _request(self, path: str, payload: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if payload is None:
                resp = await client.get(self.gateway + path)
            else:
                resp = await client.post(self.gateway + path, json=payload)
            resp.raise_for_status()
            return resp.json()

    # ── 拉起核心（可选）─────────────────────────────────────────
    def _spawn_core_sync(self) -> str:
        if not self.start_script:
            return "没有配置 start_script，跳过自动启动"
        if not os.path.exists(self.start_script):
            return "找不到启动脚本：%s" % self.start_script
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        if sys.platform == "win32":
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.start_script]
        else:
            cmd = ["/bin/sh", self.start_script]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags)
            return ""
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    async def _ensure_core_quiet(self) -> None:
        try:
            await self._request("/health")
            return
        except Exception:  # noqa: BLE001
            pass
        err = await asyncio.to_thread(self._spawn_core_sync)
        if err:
            logger.info("[陪伴] 未自动启动核心：%s", err)
            return
        for _ in range(20):
            await asyncio.sleep(1.0)
            try:
                await self._request("/health")
                logger.info("[陪伴] 核心已就绪")
                return
            except Exception:  # noqa: BLE001
                continue
        logger.warning("[陪伴] 核心 20 秒内没起来")

    # ── 命令 ───────────────────────────────────────────────────
    @filter.command("陪伴")
    async def cmd_companion(self, event: AstrMessageEvent):
        raw = (event.message_str or "").strip()
        args = raw.split()
        if args and args[0].lstrip("/") == "陪伴":
            args = args[1:]
        if not args:
            yield event.plain_result(await self._render_status())
            return

        head = args[0]
        rest = " ".join(args[1:]).strip()

        if head in ("帮助", "help", "?"):
            yield event.plain_result(HELP)
            return
        if head in ("医生", "体检", "doctor", "check"):
            yield event.plain_result(await self._render_doctor())
            return
        if head in ("模式", "mode"):
            yield event.plain_result(await self._switch_mode(rest))
            return
        if head in ("说", "say"):
            yield event.plain_result(await self._say(rest))
            return
        if head in ("摘要", "digest"):
            yield event.plain_result(await self._digest())
            return
        if head in ("启动", "start"):
            yield event.plain_result(await self._start_core())
            return
        if head in ("开", "on"):
            yield event.plain_result(await self._switch_mode("auto"))
            return
        if head in ("关", "off", "停"):
            yield event.plain_result(await self._switch_mode("off"))
            return
        yield event.plain_result(HELP)

    async def _render_status(self) -> str:
        try:
            st = await self._request("/status")
        except Exception as exc:  # noqa: BLE001
            return "❌ 核心没响应（%s）\n发「陪伴 启动」或者手动起核心服务。" % exc
        mode = str(st.get("mode") or "?")
        return "\n".join([
            "🐟 陪伴状态",
            "模式：%s（%s）" % (mode, MODES.get(mode, "?")),
            "今日识屏：%s 次 | 帧缓冲：%s | 提示音缓存：%s" % (
                st.get("today_vision"), st.get("frames"), st.get("precue")),
            "摘要：%s 字 | 已念：%s 句" % (st.get("digest_chars"), st.get("spoken")),
        ])

    async def _render_doctor(self) -> str:
        try:
            d = await self._request("/doctor")
        except Exception as exc:  # noqa: BLE001
            return "❌ 核心没响应（%s）" % exc
        marks = {True: "✅", False: "❌"}
        lines = ["🩺 体检"]
        for name, item in (d.get("checks") or {}).items():
            lines.append("%s %s — %s" % (marks.get(bool(item.get("ok"))), name, item.get("detail")))
        lines.append("总体：%s" % ("全部就绪" if d.get("ok") else "有缺项，见上面 ❌"))
        return "\n".join(lines)

    async def _switch_mode(self, name: str) -> str:
        name = (name or "").strip().lower()
        if name not in MODES:
            return "模式只能是：%s" % " / ".join(MODES.keys())
        try:
            r = await self._request("/mode", {"mode": name, "source": "chat"})
        except Exception as exc:  # noqa: BLE001
            return "❌ 切换失败（%s）" % exc
        return "✅ 模式切到 %s（%s）" % (r.get("mode"), MODES.get(str(r.get("mode")), ""))

    async def _say(self, text: str) -> str:
        if not text:
            return "要说什么？例：陪伴 说 我在看呢"
        try:
            r = await self._request("/say", {"text": text})
        except Exception as exc:  # noqa: BLE001
            return "❌ 没说出来（%s）" % exc
        return "🔊 已送播 %s 句" % r.get("sentences")

    async def _digest(self) -> str:
        try:
            r = await self._request("/digest", {})
        except Exception as exc:  # noqa: BLE001
            return "❌ 摘要失败（%s）" % exc
        if not r.get("ok"):
            return "这次没摘要：%s" % (r.get("reason") or "未知")
        return "📝 %s（%s 条记录）" % (r.get("text"), r.get("count"))

    async def _start_core(self) -> str:
        try:
            await self._request("/health")
            return "核心已经在跑了。"
        except Exception:  # noqa: BLE001
            pass
        err = await asyncio.to_thread(self._spawn_core_sync)
        if err:
            return "❌ 启动失败：%s" % err
        for _ in range(20):
            await asyncio.sleep(1.0)
            try:
                await self._request("/health")
                return "✅ 核心已启动。"
            except Exception:  # noqa: BLE001
                continue
        return "❌ 20 秒内没起来，看核心服务的日志。"
