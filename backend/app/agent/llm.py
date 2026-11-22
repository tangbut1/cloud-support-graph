"""大模型客户端（可插拔 + 可降级）。

【当前阶段的重要约束】
    项目还没有拿到 API Key，因此这里必须做到"没有模型也能跑"：
        LlmClient.enabled == False 时，所有方法返回 None，
        调用方（graph 节点）自动退回确定性路径（规则分类 / 抽取式回答）。

【为什么要这层薄封装】
    1. 隔离 langchain-openai 的构造细节，测试可注入假实现；
    2. 统一超时、重试与 usage 统计入口（Day 8 评测要统计 Token）；
    3. 把"模型可用性"变成一个显式开关，而不是散落在各处的 try/except。

【拿到 Key 后怎么用】
    在 .env 里填 LLM_BASE_URL / LLM_MODEL_NAME / LLM_API_KEY，
    LlmClient.enabled 自动变 True，graph 会优先走模型路径，无需改任何业务代码。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

# 进程级稳定的会话 ID：OpenCode Go 网关用它做路由优化（缺了会 400 MissingSessionID）
_PROCESS_SESSION_ID = uuid.uuid4().hex


class LlmClient:
    """对 langchain_openai.ChatOpenAI 的薄封装。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None
        self._loop = None       # 持有循环对象本身（不能用 id()，对象释放后 id 会复用）
        self.session_id = settings.llm_session_id or _PROCESS_SESSION_ID
        self.last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        # 只做"配置是否齐全"的判断，不在这里建客户端（见 _get_model 的说明）
        self._configured = bool(settings.llm_api_key and settings.llm_base_url)

    @property
    def enabled(self) -> bool:
        """是否配置了可用的大模型。"""
        return self._configured

    def _get_model(self):
        """按事件循环懒加载客户端。

        为什么不能在 __init__ 里建 ChatOpenAI：
            它内部的 AsyncOpenAI 会绑定到调用时的事件循环；测试与脚本里多次
            asyncio.run() 会创建不同的循环，复用同一个客户端就会报
            "Event loop is closed"。按循环缓存后，uvicorn 常驻进程仍只建一次。
        """
        if not self._configured:
            return None
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:              # 没有运行中的循环 -> 退回同步场景
            loop = None

        # 必须比较"循环对象本身"而不是 id()：id 在对象被回收后会被复用，
        # 会导致复用一个绑定在已关闭循环上的旧客户端。
        if self._model is not None and self._loop is loop and not (loop is not None and loop.is_closed()):
            return self._model

        try:
            from langchain_openai import ChatOpenAI

            self._model = ChatOpenAI(
                model=self._settings.llm_model_name,
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url,
                timeout=self._settings.llm_timeout_seconds,
                temperature=0.0,
                # 该头对 OpenAI 官方接口无副作用；OpenCode Go 网关必需
                default_headers={"x-opencode-session": self.session_id},
            )
            self._loop = loop
        except Exception as exc:          # pragma: no cover
            logger.warning("大模型初始化失败，将使用确定性降级路径: %s", exc)
            self._configured = False
            self._model = None
        return self._model

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None) or {}
        self.last_usage = {
            "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
            "completion_tokens": int(usage.get("output_tokens", 0) or 0),
        }

    async def _json_call(self, system: str, user: str) -> dict | None:
        model = self._get_model()
        if model is None:
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await model.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
            self._record_usage(response)
            text = response.content if isinstance(response.content, str) else str(response.content)
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            return json.loads(text[start : end + 1])
        except Exception as exc:          # pragma: no cover - 网络/解析异常兜底
            logger.warning("大模型调用失败，回退确定性路径: %s", exc)
            return None

    async def aclose(self) -> None:
        """关闭底层 HTTP 连接（脚本/测试收尾用；常驻服务不需要调用）。"""
        model = self._model
        client = getattr(model, "async_client", None) if model is not None else None
        try:
            if client is not None:
                await client.close()
        except Exception:                 # pragma: no cover - 关闭失败不影响业务
            pass
        self._model = None
        self._loop = None

    # ---- 具体能力 ----
    async def classify_intent(self, query: str) -> tuple[str, str] | None:
        """返回 (intent, reason)；不可用时返回 None。"""
        from app.agent.prompts import INTENT_SYSTEM

        data = await self._json_call(INTENT_SYSTEM, query)
        if not data:
            return None
        intent = str(data.get("intent", "")).strip()
        from app.agent.intent_guard import INTENTS

        if intent not in INTENTS:
            return None
        return intent, str(data.get("reason", "模型分类"))

    async def answer_with_citations(self, query: str, context: str) -> str | None:
        """基于检索资料生成回答；不可用时返回 None。"""
        from app.agent.prompts import ANSWER_SYSTEM, ANSWER_USER

        model = self._get_model()
        if model is None:
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await model.ainvoke(
                [
                    SystemMessage(content=ANSWER_SYSTEM),
                    HumanMessage(content=ANSWER_USER.format(query=query, context=context)),
                ]
            )
            self._record_usage(response)
            return response.content if isinstance(response.content, str) else str(response.content)
        except Exception as exc:          # pragma: no cover
            logger.warning("回答生成失败，回退抽取式摘要: %s", exc)
            return None

    async def describe_image(self, data: bytes, mime: str) -> str | None:
        """视觉理解：提取报错截图中的报错码、端口、服务名、命令行输出。

        模型未配置或调用失败时返回 None，不阻断主聊天流程。
        """
        model = self._get_model()
        if model is None:
            return None
        try:
            import base64

            from langchain_core.messages import HumanMessage

            b64 = base64.b64encode(data).decode("ascii")
            msg = HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": "提取这张报错截图中的关键信息（报错码、端口、服务名、命令行输出），一句话概括，不要推测。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ]
            )
            response = await model.ainvoke([msg])
            self._record_usage(response)
            text = response.content if isinstance(response.content, str) else str(response.content)
            return text.strip() or None
        except Exception as exc:          # pragma: no cover - 视觉接口异常降级
            logger.warning("视觉识别失败，降级为空摘要: %s", exc)
            return None
