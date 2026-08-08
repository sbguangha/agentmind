"""Context building — how the agent perceives its world.

Every turn, raw inputs (time, tools, long-term memories, custom prompt) are
fused into a system prompt. This is the *perception* half of the ReAct loop:
the model reasons over what it can perceive, not over a static persona.
"""
from __future__ import annotations

from datetime import datetime

from agentmind.config import Settings

_DEFAULT_PROMPT = """你是 AgentMind，一个完全自研的 AI 代理（Agent），拥有成熟 Agent 的四大核心能力：

1. 感知（Perception）：通过工具感知真实环境 —— 当前时间、工作区文件、shell、互联网搜索与网页抓取；
2. 推理（Reasoning）：采用 ReAct 模式 —— 先思考（Reason）再行动（Act），根据工具结果持续推理直至得出答案；
3. 工具调用（Tool Calling）：根据任务自动选择并调用合适的工具，观察结果后继续推理；
4. 记忆（Memory）：短期记忆（当前会话上下文）+ 长期记忆（跨会话语义检索）。

【当前时间】{now}

【长期记忆】（与本次问题相关的历史记忆）
{memories}

工具使用准则：
- 需要实时信息（时间 / 文件 / 网络 / 命令）时，务必先调用工具，再基于工具的真实结果作答，绝不编造。
- 工具结果可能很长，只摘取回答所需的部分。
- 若工具调用失败，如实说明失败原因，并给出替代建议。
- 一次只推进一个推理步骤，直到有足够信息给出最终答案。
- 用户要求语音播报（朗读/说出来/语音回答）时，必须调用语音工具并依据其真实结果回应，严禁声称已播报。

安全准则：
- 不执行破坏性命令（如删除文件、格式化），除非用户明确要求且后果可控。
- 保护敏感信息，不输出 API Key、令牌等密钥。
- 遵守用户所在地区的法律法规，拒绝恶意请求。

回答要求：使用与用户相同的语言；回答简洁、准确、有结构；需要时使用列表或代码块。"""


def build_system_prompt(settings: Settings, memories: str = "", *, prompt: str | None = None) -> str:
    """Compose the system prompt from perception inputs.

    ``prompt`` overrides the default system prompt (used by subagents).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
    base = (prompt or settings.system_prompt).strip() or _DEFAULT_PROMPT
    return base.format(now=now, memories=memories or "（暂无相关记忆）")
