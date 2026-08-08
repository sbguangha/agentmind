"""Tests for tool registry and built-in tools."""
from __future__ import annotations

import pytest

from agentmind.tools.base import ToolResult
from agentmind.tools.filesystem import (
    Filesystem,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool


def test_register_and_schema():
    registry = ToolRegistry()
    registry.register(EchoTool())
    schemas = registry.schemas()
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"


async def test_execute_valid_arguments():
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.execute("echo", '{"text": "hello"}')
    assert result.output == "echo:hello"


async def test_execute_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute("nope", "{}")
    assert result.is_error


async def test_execute_bad_json():
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.execute("echo", "not-json")
    assert result.is_error


async def test_execute_runtime_exception_surfaced():
    class BoomTool(EchoTool):
        name = "boom"

        async def run(self, **kwargs) -> ToolResult:
            raise ValueError("kaboom")

    registry = ToolRegistry()
    registry.register(BoomTool())
    result = await registry.execute("boom", "{}")
    assert result.is_error
    assert "kaboom" in result.output


@pytest.fixture
def fs(tmp_path):
    return Filesystem(tmp_path / "workspace")


async def test_filesystem_roundtrip(fs):
    await WriteFileTool(fs).run(path="a/b.txt", content="你好")
    result = await ReadFileTool(fs).run(path="a/b.txt")
    assert result.output == "你好"


async def test_filesystem_path_escape_blocked(fs):
    with pytest.raises(ValueError):
        fs.resolve("../../outside")


async def test_filesystem_write_append(fs):
    await WriteFileTool(fs).run(path="log.txt", content="line1\n")
    await WriteFileTool(fs).run(path="log.txt", content="line2\n", append=True)
    result = await ReadFileTool(fs).run(path="log.txt")
    assert result.output == "line1\nline2\n"


async def test_list_directory(fs, tmp_path):
    fs.root.mkdir(exist_ok=True)
    (fs.root / "x.txt").write_text("x", encoding="utf-8")
    (fs.root / "sub").mkdir()
    result = await ListDirectoryTool(fs).run(path=".")
    assert "x.txt" in result.output
    assert "sub" in result.output
