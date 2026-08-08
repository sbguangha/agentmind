"""Perception tool: reading and writing files — confined to a workspace.

Path confinement is a deliberate security boundary: every path is resolved
and verified to stay inside the configured workspace, so the model can never
touch files outside its sandbox — unless the session's workspace scope is
``full`` (see ``security/workspace_access.py``), which grants whole-machine
access and is the dangerous-but-explicit opt-in.
"""
from __future__ import annotations

from pathlib import Path

from agentmind.security.workspace_access import current_scope
from agentmind.tools.base import Tool, ToolResult


class Filesystem:
    """Path confinement helpers shared by the filesystem tools."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        """Resolve a user-supplied path, honoring the current workspace scope."""
        scope = current_scope()
        if scope is not None and not scope.restrict:
            # full access: allow absolute paths or paths relative to the CWD
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            return candidate.resolve()
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"路径越界: {path} (仅允许访问 {self.root})")
        return candidate

    @staticmethod
    def read_text(path: Path, *, limit: int = 40_000) -> str:
        """Read text with encoding fallbacks (UTF-8 then GBK) and length cap."""
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="gbk", errors="replace")
        if len(content) > limit:
            content = content[:limit] + "\n...[输出已截断]..."
        return content


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取工作区内文本文件的内容（UTF-8/GBK 自动识别）。"

    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "相对工作区的文件路径"}},
        "required": ["path"],
    }

    def __init__(self, fs: Filesystem) -> None:
        self._fs = fs

    async def run(self, path: str, **kwargs) -> ToolResult:
        target = self._fs.resolve(path)
        if not target.is_file():
            return ToolResult(output=f"文件不存在: {path}", is_error=True)
        return ToolResult(output=self._fs.read_text(target))


class WriteFileTool(Tool):
    name = "write_file"
    description = "在工作区写入或覆盖文件；append=true 时追加内容。"

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
            "append": {"type": "boolean", "description": "是否追加而不是覆盖"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, fs: Filesystem) -> None:
        self._fs = fs

    async def run(self, path: str, content: str, append: bool = False, **kwargs) -> ToolResult:
        target = self._fs.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        return ToolResult(output=f"已{'追加' if append else '写入'} {path}（{len(content)} 字符）")


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "列出工作区目录下的文件与子目录。path 为空表示工作区根目录。"

    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "相对工作区的目录路径，默认根目录"}},
        "required": [],
    }

    def __init__(self, fs: Filesystem) -> None:
        self._fs = fs

    async def run(self, path: str = ".", **kwargs) -> ToolResult:
        target = self._fs.resolve(path)
        if not target.is_dir():
            return ToolResult(output=f"目录不存在: {path}", is_error=True)
        lines = [f"{'.' if p.is_dir() else ''}{p.name}" for p in sorted(target.iterdir())]
        return ToolResult(output=f"{target}\n" + ("\n".join(lines) if lines else "(空目录)"))
