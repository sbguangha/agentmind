"""Tests for the security layer: SSRF protection and workspace access scope."""
from __future__ import annotations

import pytest

from agentmind.security.network import contains_internal_url, resolve_url_target, validate_url
from agentmind.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_scope,
    build_scope,
    current_scope,
    reset_scope,
)
from agentmind.tools.filesystem import Filesystem


# ---- SSRF --------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "http://192.168.1.10/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://172.16.0.1/",
        "ftp://example.com/x",
        "file:///etc/passwd",
    ],
)
def test_ssrf_blocks_private_and_bad_scheme(url):
    ok, error, _ = resolve_url_target(url)
    assert ok is False, f"should block {url}: {error}"


@pytest.mark.asyncio
async def test_validate_url_async_blocks_loopback():
    ok, _ = await validate_url("http://127.0.0.1/x")
    assert ok is False
    ok, _ = await validate_url("http://127.0.0.1/x", allow_loopback=True)
    assert ok is True


def test_ssrf_allows_public_domain():
    ok, _, _ = resolve_url_target("https://www.python.org/")
    assert ok is True


def test_contains_internal_url():
    assert contains_internal_url("去 http://192.168.1.1 看看")
    assert not contains_internal_url("没有 URL 的一句话")
    assert not contains_internal_url("参考 https://docs.python.org/3/")


# ---- workspace scope ---------------------------------------------------
def test_build_scope_modes():
    assert build_scope("/tmp/x", "restricted").restrict is True
    assert build_scope("/tmp/x", "full").restrict is False
    with pytest.raises(ValueError):
        build_scope("/tmp/x", "bogus")


def test_scope_contextvar_binding():
    scope = build_scope("/tmp/x", "full")
    assert current_scope() is None
    token = bind_scope(scope)
    try:
        assert current_scope() is scope
    finally:
        reset_scope(token)
    assert current_scope() is None


def test_resolver_defaults(tmp_path):
    resolver = WorkspaceScopeResolver(tmp_path, default_restrict=True)
    assert resolver.resolve().restrict is True
    assert resolver.resolve("full").restrict is False


def test_filesystem_restricted_blocks_escape(tmp_path):
    fs = Filesystem(tmp_path / "ws")
    with pytest.raises(ValueError):
        fs.resolve("../../outside")


def test_filesystem_full_allows_absolute(tmp_path):
    fs = Filesystem(tmp_path / "ws")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    scope = build_scope(tmp_path / "ws", "full")
    token = bind_scope(scope)
    try:
        assert fs.resolve(str(outside)) == outside.resolve()
    finally:
        reset_scope(token)
