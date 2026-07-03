# SPDX-License-Identifier: GPL-3.0-or-later
#
# 大儒帮我辩经 - AstrBot 插件
# Copyright (C) 2026 Zam
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# ============================================================
# 大儒帮我辩经 - AstrBot 插件
# 用春秋笔法讲现代道理，支持四种儒家辩经模式。
# ============================================================
import sys
import os
import json
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

# 确保插件目录在 sys.path 中，使 from prompt import ... 能正常加载
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from prompt import MODE_INFO, MODE_TRIGGER_MAP, LENGTH_PROMPTS, ANALYSIS_PROMPT, COMMON_RULES, WENYAN_RULES


class ConfuciusSaidPlugin(Star):
    """大儒帮我辩经 - 用春秋笔法讲现代道理"""

    def __init__(self, context: Context):
        super().__init__(context)

    # ----------------------------------------------------------
    # 内部工具方法
    # ----------------------------------------------------------
    async def _get_provider_id(self, event: AstrMessageEvent) -> str | None:
        """获取当前会话的 LLM 提供商 ID"""
        umo = event.unified_msg_origin
        try:
            return await self.context.get_current_chat_provider_id(umo=umo)
        except Exception as e:
            logger.error(f"获取 LLM 提供商失败: {e}")
            return None

    def _extract_question(self, event: AstrMessageEvent) -> str:
        """从消息中提取问题文本（去掉命令部分）"""
        text = event.message_str.strip()
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def _analyze(self, event: AstrMessageEvent, question: str) -> dict:
        """调用 LLM 分析问题，返回 {mode, length, style}"""
        provider_id = await self._get_provider_id(event)
        if not provider_id:
            logger.warning("无可用 LLM 提供商，使用默认分析结果")
            return {"mode": 2, "length": "成礼", "style": "白话"}

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=f"用户的问题是：{question}",
                system_prompt=ANALYSIS_PROMPT,
            )
        except Exception as e:
            logger.error(f"分析 LLM 调用失败: {e}")
            return {"mode": 2, "length": "成礼", "style": "白话"}

        text = resp.completion_text.strip()
        logger.info(f"分析 LLM 返回: {text}")

        try:
            # 尝试从返回文本中提取 JSON
            m = re.search(r"\{[^}]+\}", text)
            data = json.loads(m.group()) if m else json.loads(text)
            mode = int(data.get("mode", 2))
            length = str(data.get("length", "成礼"))
            style = str(data.get("style", "白话"))
            if mode not in (1, 2, 3, 4):
                mode = 2
            if length not in ("小礼", "成礼", "大礼"):
                length = "成礼"
            if style not in ("白话", "文言"):
                style = "白话"
            return {"mode": mode, "length": length, "style": style}
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as e:
            logger.warning(f"解析分析结果失败: {e}，原始文本={text}")
            return {"mode": 2, "length": "成礼", "style": "白话"}

    async def _generate(
        self,
        event: AstrMessageEvent,
        question: str,
        mode: int,
        length: str,
        style: str = "白话",
    ) -> str:
        """根据模式、长度和语体生成最终回答"""
        provider_id = await self._get_provider_id(event)
        if not provider_id:
            return "抱歉，无法获取 LLM 服务提供商，请检查配置。"

        info = MODE_INFO.get(mode, MODE_INFO[2])
        length_prompt = LENGTH_PROMPTS.get(length, LENGTH_PROMPTS["成礼"])

        if style == "文言":
            rules = WENYAN_RULES
            mode_prompt = info.get("wenyan_prompt", "")
        else:
            rules = COMMON_RULES
            mode_prompt = info["prompt"]

        system_prompt = (
            f"{rules}\n\n"
            f"{info['name']}（{info['desc']}）\n"
            f"{mode_prompt}\n"
            f"{length_prompt}"
        )

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=question,
                system_prompt=system_prompt,
            )
            return resp.completion_text.strip()
        except Exception as e:
            logger.error(f"生成 LLM 调用失败: {e}")
            return f"抱歉，本次{info['name']}出了点差错：{str(e)}"

    async def _handle_auto(
        self, event: AstrMessageEvent, question: str
    ):
        """自动模式：先分析再生成"""
        if not question:
            yield event.plain_result(
                "请告知需要辩经的问题，例如：大儒帮我辩经 华强买瓜如何才合乎周礼"
            )
            return

        yield event.plain_result("正在翻阅经典，请稍候……")

        analysis = await self._analyze(event, question)
        mode = analysis["mode"]
        length = analysis["length"]
        style = analysis.get("style", "白话")
        info = MODE_INFO[mode]
        logger.info(f"分析结果：模式={info['name']}({mode})，长度={length}，语体={style}")

        answer = await self._generate(event, question, mode, length, style)
        yield event.plain_result(answer)

    async def _handle_manual(
        self, event: AstrMessageEvent, question: str, mode: int, style: str = "白话"
    ):
        """手动指定模式：先分析长度再生成"""
        if not question:
            triggers = {1: "相劝", 2: "大儒", 3: "圆场", 4: "察小"}
            t = triggers[mode]
            yield event.plain_result(
                f"请告知需要讨论的问题，例如：{t} AI远超人类怎么办"
            )
            return

        info = MODE_INFO[mode]
        yield event.plain_result(f"正在以「{info['name']}」之道思索，请稍候……")

        # 只用分析获取长度和语体，模式固定为用户选择的
        analysis = await self._analyze(event, question)
        length = analysis["length"]
        # 手动触发时，如果未指定 style 则优先使用分析结果中的语体
        if style == "白话" and analysis.get("style") == "文言":
            style = "文言"

        answer = await self._generate(event, question, mode, length, style)
        yield event.plain_result(answer)

    # ----------------------------------------------------------
    # 命令处理器
    # ----------------------------------------------------------

    @filter.command("大儒帮我辩经")
    async def auto_bianjing(self, event: AstrMessageEvent):
        """大儒帮我辩经 <问题> — 自动判断模式和长度"""
        async for r in self._handle_auto(event, self._extract_question(event)):
            yield r

    @filter.command("大儒用文言帮我辩经")
    async def cmd_wenyan(self, event: AstrMessageEvent):
        """大儒用文言帮我辩经 <问题> — 文言输出版"""
        question = self._extract_question(event)
        if not question:
            yield event.plain_result("请告知需要辩经的问题，例如：大儒用文言帮我辩经 华强买瓜如何才合乎周礼")
            return

        yield event.plain_result("正在研墨秉简，以古文辞，请稍候……")

        analysis = await self._analyze(event, question)
        mode = analysis["mode"]
        length = analysis["length"]
        info = MODE_INFO[mode]
        logger.info(f"文言模式：模式={info['name']}({mode})，长度={length}")

        answer = await self._generate(event, question, mode, length, style="文言")
        yield event.plain_result(answer)

    @filter.command("相劝")
    async def cmd_xiangquan(self, event: AstrMessageEvent):
        """相劝 <问题> — 温和相劝模式"""
        async for r in self._handle_manual(event, self._extract_question(event), 1):
            yield r

    @filter.command("大儒")
    async def cmd_daru(self, event: AstrMessageEvent):
        """大儒 <问题> — 大儒辩经模式"""
        async for r in self._handle_manual(event, self._extract_question(event), 2):
            yield r

    @filter.command("圆场")
    async def cmd_yuanchang(self, event: AstrMessageEvent):
        """圆场 <问题> — 强行圆场模式"""
        async for r in self._handle_manual(event, self._extract_question(event), 3):
            yield r

    @filter.command("察小")
    async def cmd_chaxiao(self, event: AstrMessageEvent):
        """察小 <问题> — 痛心疾首模式"""
        async for r in self._handle_manual(event, self._extract_question(event), 4):
            yield r

    async def terminate(self):
        """插件被卸载/停用时调用"""
        logger.info("大儒帮我辩经插件已卸载，礼失求诸野。")
