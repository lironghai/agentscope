# -*- coding: utf-8 -*-
"""Focused tests for the long-term memory browser API."""
from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "tree_sitter_bash" not in sys.modules:
    tsbash = types.ModuleType("tree_sitter_bash")
    tsbash.language = lambda: object()
    sys.modules["tree_sitter_bash"] = tsbash

if "tree_sitter" not in sys.modules:
    tree_sitter = types.ModuleType("tree_sitter")

    class _FakeLanguage:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeParser:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeNode:
        type = ""
        children: list = []
        start_byte = 0
        end_byte = 0

    tree_sitter.Language = _FakeLanguage
    tree_sitter.Parser = _FakeParser
    tree_sitter.Node = _FakeNode
    sys.modules["tree_sitter"] = tree_sitter

if "frontmatter" not in sys.modules:
    frontmatter = types.ModuleType("frontmatter")

    class _FakeFrontmatterPost(dict):
        content = ""

    frontmatter.loads = lambda _text: _FakeFrontmatterPost()
    sys.modules["frontmatter"] = frontmatter

if "shortuuid" not in sys.modules:
    shortuuid = types.ModuleType("shortuuid")
    shortuuid.uuid = lambda: uuid.uuid4().hex
    sys.modules["shortuuid"] = shortuuid

from agentscope.app._router import session_router
from agentscope.app.deps import get_storage
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    SessionConfig,
)


class _FakeRedis:
    """Small async Redis stub for session ownership tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                count += 1
            if key in self.sets:
                del self.sets[key]
                count += 1
        return count

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *values: str) -> None:
        current = self.sets.setdefault(key, set())
        for value in values:
            current.discard(value)

    async def expire(self, key: str, _ttl: int) -> None:
        return None


def make_storage() -> RedisStorage:
    """Create RedisStorage backed by an in-memory stub."""
    storage = RedisStorage.__new__(RedisStorage)
    # pylint: disable=protected-access
    storage._client = _FakeRedis()
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_session_config() -> SessionConfig:
    """Create a minimal session config."""
    return SessionConfig(
        workspace_id="ws-1",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


class TestLongTermMemoryBrowserDisabled(IsolatedAsyncioTestCase):
    """Disabled browser dependency returns a controlled error."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            session_id="session-1",
        )

        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_storage] = lambda: self.storage
        self.client = TestClient(app)

    async def test_tree_returns_503_when_browser_is_not_configured(self) -> None:
        """Missing browser configuration is a controlled service error."""
        response = self.client.get(
            f"/sessions/{self.session.id}/memory/tree",
            params={"agent_id": self.agent_id, "backend": "agentic"},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 503)


class TestLongTermMemoryBrowserRouter(IsolatedAsyncioTestCase):
    """Session-scoped long-term memory browser endpoint behavior."""

    async def asyncSetUp(self) -> None:
        from agentscope.app._service._long_term_memory_browser import (
            LongTermMemoryBrowser,
        )

        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp)
        self.base_dir = Path(self.tmp.name)
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            session_id="session-1",
        )
        self.memory_root = (
            self.base_dir
            / self.user_id
            / self.agent_id
            / self.session.id
            / "long_term_memory"
        )

        def resolve_root(user_id: str, agent_id: str, session_id: str) -> Path:
            return (
                self.base_dir
                / user_id
                / agent_id
                / session_id
                / "long_term_memory"
            )

        app = FastAPI()
        app.include_router(session_router)
        self.browser = LongTermMemoryBrowser(
            resolve_root,
        )
        app.state.long_term_memory_browser = self.browser
        app.dependency_overrides[get_storage] = lambda: self.storage
        self.client = TestClient(app)

    async def _cleanup_tmp(self) -> None:
        self.tmp.cleanup()

    def _assert_browser_status(
        self,
        expected_status: int,
        func,
        *args,
    ) -> None:
        with self.assertRaises(HTTPException) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.status_code, expected_status)

    def _create_symlink(
        self,
        target: Path,
        link: Path,
        *,
        target_is_directory: bool,
    ) -> None:
        try:
            os.symlink(
                target,
                link,
                target_is_directory=target_is_directory,
            )
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")

    async def test_tree_lists_agentic_memory_files(self) -> None:
        """Tree lists files below the selected backend root."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (agentic_root / "profile.md").write_text("hello", encoding="utf-8")

        response = self.client.get(
            f"/sessions/{self.session.id}/memory/tree",
            params={"agent_id": self.agent_id, "backend": "agentic"},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["backend"], "agentic")
        self.assertTrue(body["exists"])
        self.assertEqual(body["entries"][0]["path"], "profile.md")
        self.assertTrue(body["entries"][0]["editable"])

    async def test_tree_lists_nested_agentic_memory_files(self) -> None:
        """Tree surfaces AgenticMemoryMiddleware's nested Memory files."""
        agentic_root = self.memory_root / "agentic"
        memory_dir = agentic_root / "Memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text(
            "Always reply in Chinese.",
            encoding="utf-8",
        )

        response = self.client.get(
            f"/sessions/{self.session.id}/memory/tree",
            params={"agent_id": self.agent_id, "backend": "agentic"},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        paths = [entry["path"] for entry in body["entries"]]
        self.assertIn("Memory/MEMORY.md", paths)
        nested_file = next(
            entry
            for entry in body["entries"]
            if entry["path"] == "Memory/MEMORY.md"
        )
        self.assertEqual(nested_file["type"], "file")
        self.assertTrue(nested_file["editable"])

    async def test_missing_backend_directory_returns_404(self) -> None:
        """Configured browser still returns 404 for absent backend storage."""
        response = self.client.get(
            f"/sessions/{self.session.id}/memory/tree",
            params={"agent_id": self.agent_id, "backend": "reme"},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 404)

    async def test_file_rejects_path_traversal(self) -> None:
        """Relative paths cannot escape the configured backend root."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (self.memory_root / "secret.md").write_text(
            "secret",
            encoding="utf-8",
        )

        response = self.client.get(
            f"/sessions/{self.session.id}/memory/file",
            params={
                "agent_id": self.agent_id,
                "backend": "agentic",
                "path": "../secret.md",
            },
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 400)

    async def test_file_rejects_drive_qualified_and_rooted_paths(self) -> None:
        """Public file paths must be plain relative paths."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (agentic_root / "profile.md").write_text("safe", encoding="utf-8")
        old_cwd = Path.cwd()
        os.chdir(agentic_root)
        try:
            bad_paths = [
                f"{agentic_root.drive}profile.md"
                if agentic_root.drive
                else "C:profile.md",
                r"C:\profile.md",
                r"\\server\share\profile.md",
                r"\profile.md",
                "/profile.md",
            ]
            for path in bad_paths:
                with self.subTest(path=path):
                    self._assert_browser_status(
                        status.HTTP_400_BAD_REQUEST,
                        self.browser.read_file,
                        self.user_id,
                        self.agent_id,
                        self.session.id,
                        "agentic",
                        path,
                    )
        finally:
            os.chdir(old_cwd)

    async def test_backend_root_symlink_is_rejected(self) -> None:
        """Backend roots must not be symlinks to another directory."""
        external = self.base_dir / "external_agentic"
        external.mkdir()
        (external / "profile.md").write_text("outside", encoding="utf-8")
        self.memory_root.mkdir(parents=True)
        self._create_symlink(
            external,
            self.memory_root / "agentic",
            target_is_directory=True,
        )

        self._assert_browser_status(
            status.HTTP_404_NOT_FOUND,
            self.browser.list_tree,
            self.user_id,
            self.agent_id,
            self.session.id,
            "agentic",
        )

    async def test_long_term_memory_root_symlink_is_rejected(self) -> None:
        """The configured memory root must not resolve through a symlink."""
        external = self.base_dir / "external_memory"
        external_agentic = external / "agentic"
        external_agentic.mkdir(parents=True)
        (external_agentic / "profile.md").write_text(
            "outside",
            encoding="utf-8",
        )
        self.memory_root.parent.mkdir(parents=True)
        self._create_symlink(
            external,
            self.memory_root,
            target_is_directory=True,
        )

        self._assert_browser_status(
            status.HTTP_400_BAD_REQUEST,
            self.browser.read_file,
            self.user_id,
            self.agent_id,
            self.session.id,
            "agentic",
            "profile.md",
        )

    async def test_session_directory_symlink_is_rejected(self) -> None:
        """Session ancestors must not resolve through a symlink."""
        external_session = self.base_dir / "external_session"
        external_agentic = external_session / "long_term_memory" / "agentic"
        external_agentic.mkdir(parents=True)
        (external_agentic / "profile.md").write_text(
            "outside",
            encoding="utf-8",
        )
        session_parent = self.base_dir / self.user_id / self.agent_id
        session_parent.mkdir(parents=True)
        self._create_symlink(
            external_session,
            session_parent / self.session.id,
            target_is_directory=True,
        )

        self._assert_browser_status(
            status.HTTP_400_BAD_REQUEST,
            self.browser.read_file,
            self.user_id,
            self.agent_id,
            self.session.id,
            "agentic",
            "profile.md",
        )

    async def test_file_rejects_symlink_child_component(self) -> None:
        """A symlink anywhere below the backend root is not browsable."""
        agentic_root = self.memory_root / "agentic"
        real_dir = agentic_root / "real"
        real_dir.mkdir(parents=True)
        (real_dir / "profile.md").write_text("behind symlink", encoding="utf-8")
        self._create_symlink(
            real_dir,
            agentic_root / "linked",
            target_is_directory=True,
        )

        self._assert_browser_status(
            status.HTTP_400_BAD_REQUEST,
            self.browser.read_file,
            self.user_id,
            self.agent_id,
            self.session.id,
            "agentic",
            "linked/profile.md",
        )

    async def test_file_rejects_nul_public_path(self) -> None:
        """NUL in public paths is a controlled bad request."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (agentic_root / "profile.md").write_text("safe", encoding="utf-8")

        for operation, args in (
            (
                self.browser.read_file,
                (
                    self.user_id,
                    self.agent_id,
                    self.session.id,
                    "agentic",
                    "profile.md\x00outside",
                ),
            ),
            (
                self.browser.write_file,
                (
                    self.user_id,
                    self.agent_id,
                    self.session.id,
                    "agentic",
                    "profile.md\x00outside",
                    "new",
                ),
            ),
        ):
            with self.subTest(operation=operation.__name__):
                self._assert_browser_status(
                    status.HTTP_400_BAD_REQUEST,
                    operation,
                    *args,
                )

    async def test_io_errors_are_mapped_to_http_errors(self) -> None:
        """Filesystem failures are surfaced as controlled HTTP errors."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        target = agentic_root / "profile.md"
        target.write_text("old", encoding="utf-8")

        with patch.object(Path, "iterdir", side_effect=PermissionError):
            self._assert_browser_status(
                status.HTTP_403_FORBIDDEN,
                self.browser.list_tree,
                self.user_id,
                self.agent_id,
                self.session.id,
                "agentic",
            )

        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            self._assert_browser_status(
                status.HTTP_404_NOT_FOUND,
                self.browser.read_file,
                self.user_id,
                self.agent_id,
                self.session.id,
                "agentic",
                "profile.md",
            )

        real_stat = Path.stat
        stat_calls = 0

        def failing_stat(path: Path, *args, **kwargs):
            nonlocal stat_calls
            if path == target:
                stat_calls += 1
                if stat_calls > 1:
                    raise OSError("stat failed")
            return real_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=failing_stat):
            self._assert_browser_status(
                status.HTTP_409_CONFLICT,
                self.browser.read_file,
                self.user_id,
                self.agent_id,
                self.session.id,
                "agentic",
                "profile.md",
            )

        with patch.object(Path, "write_text", side_effect=OSError):
            self._assert_browser_status(
                status.HTTP_409_CONFLICT,
                self.browser.write_file,
                self.user_id,
                self.agent_id,
                self.session.id,
                "agentic",
                "profile.md",
                "new",
            )

    async def test_readonly_backends_reject_writes(self) -> None:
        """ReMe and Mem0 are read-only in the v1 browser API."""
        reme_root = self.memory_root / "reme"
        reme_root.mkdir(parents=True)
        (reme_root / "memory.md").write_text("read only", encoding="utf-8")

        response = self.client.put(
            f"/sessions/{self.session.id}/memory/file",
            params={
                "agent_id": self.agent_id,
                "backend": "reme",
                "path": "memory.md",
            },
            headers={"X-User-ID": self.user_id},
            json={"content": "updated"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            (reme_root / "memory.md").read_text(encoding="utf-8"),
            "read only",
        )

    async def test_agentic_text_file_can_be_edited(self) -> None:
        """Agentic memory allows updates to supported text files."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (agentic_root / "profile.md").write_text("old", encoding="utf-8")

        response = self.client.put(
            f"/sessions/{self.session.id}/memory/file",
            params={
                "agent_id": self.agent_id,
                "backend": "agentic",
                "path": "profile.md",
            },
            headers={"X-User-ID": self.user_id},
            json={"content": "new memory"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["content"], "new memory")
        self.assertTrue(body["editable"])
        self.assertEqual(
            (agentic_root / "profile.md").read_text(encoding="utf-8"),
            "new memory",
        )

    async def test_file_checks_session_ownership_before_reading(self) -> None:
        """Wrong owner cannot read files from an existing memory directory."""
        agentic_root = self.memory_root / "agentic"
        agentic_root.mkdir(parents=True)
        (agentic_root / "profile.md").write_text("private", encoding="utf-8")

        response = self.client.get(
            f"/sessions/{self.session.id}/memory/file",
            params={
                "agent_id": "wrong-agent",
                "backend": "agentic",
                "path": "profile.md",
            },
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 404)
