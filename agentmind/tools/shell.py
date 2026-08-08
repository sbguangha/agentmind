"""Perception tool: execute shell commands.

Deliberately gated behind ``allow_shell=True`` in config — running arbitrary
commands is powerful but dangerous, and a mature agent should make that
trade-off explicit instead of silently enabling it.
"""
from __future__ import annotations

import asyncio
import os
import sys

from agentmind.tools.base import Tool, ToolResult

if sys.platform == "win32":
    _SHELL = "powershell.exe"
else:
    _SHELL = "/bin/bash"


class ShellTool(Tool):
    name = "run_shell"
    description = (
        "在工作区目录下执行一条 shell 命令并返回标准输出/错误。"
        "用于查看进程、运行脚本、安装工具等。命令带超时保护。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 15"},
        },
        "required": ["command"],
    }

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd

    async def run(self, command: str, timeout: int = 15, **kwargs) -> ToolResult:
        if any(dangerous in command for dangerous in ("rm -rf", "format ", "shutdown")):
            return ToolResult(output="已拦截可能具有破坏性的命令。", is_error=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self._cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                shell=True,
                env={**os.environ},
            )
            output = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return ToolResult(output=f"命令执行超时（{timeout}s）。", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"命令启动失败: {exc}", is_error=True)

        stdout, _ = output
        text = stdout.decode(errors="replace").strip() or "(无输出)"
        return ToolResult(output=f"exit={proc.returncode}\n{text}")
