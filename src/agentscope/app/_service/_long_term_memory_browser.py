# -*- coding: utf-8 -*-
"""Safe browser for long-term memory files behind session ownership checks."""
from __future__ import annotations

import stat as stat_module
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import HTTPException, status


RootResolver = Callable[[str, str, str], str | Path]


class LongTermMemoryBrowser:
    """Browse and edit files under a configured long-term memory root."""

    SUPPORTED_BACKENDS = {"agentic", "reme", "mem0"}
    EDITABLE_BACKENDS = {"agentic"}
    EDITABLE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
    DEFAULT_MAX_FILE_BYTES = 1024 * 1024

    def __init__(
        self,
        root_resolver: RootResolver,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        """Initialize the browser.

        Args:
            root_resolver: Callable returning the memory root for
                ``(user_id, agent_id, session_id)``.
            max_file_bytes: Maximum readable/editable file size in bytes.
        """
        self._root_resolver = root_resolver
        self._max_file_bytes = max_file_bytes

    def list_tree(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        backend: str,
    ) -> dict:
        """List files and directories under a memory backend directory."""
        backend_root = self._require_backend_root(
            user_id,
            agent_id,
            session_id,
            backend,
        )
        entries = [
            self._entry_payload(backend_root, backend, child)
            for child in self._iter_descendants(backend_root)
        ]
        entries.sort(
            key=lambda item: (
                item["type"] != "directory",
                item["path"].lower(),
            ),
        )
        return {
            "backend": backend,
            "exists": True,
            "entries": entries,
        }

    def read_file(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        backend: str,
        path: str,
    ) -> dict:
        """Read one file from a memory backend."""
        backend_root = self._require_backend_root(
            user_id,
            agent_id,
            session_id,
            backend,
        )
        target = self._resolve_child(backend_root, path)
        self._require_regular_file(target)
        self._require_size(target)
        content = self._read_text(target)
        return self._file_payload(backend_root, backend, target, content)

    def write_file(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        backend: str,
        path: str,
        content: str,
    ) -> dict:
        """Edit one supported Agentic memory text file."""
        backend_root = self._require_backend_root(
            user_id,
            agent_id,
            session_id,
            backend,
        )
        target = self._resolve_child(backend_root, path)
        self._require_regular_file(target)
        if backend not in self.EDITABLE_BACKENDS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Memory backend '{backend}' is read-only.",
            )
        if target.suffix.lower() not in self.EDITABLE_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only text memory files can be edited.",
            )
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Memory file content is too large.",
            )
        try:
            target.write_text(content, encoding="utf-8")
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, target)
        return self._file_payload(backend_root, backend, target, content)

    def _iter_descendants(self, root: Path) -> list[Path]:
        try:
            children = list(root.iterdir())
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, root)

        descendants: list[Path] = []
        for child in children:
            self._reject_symlink(
                child,
                status.HTTP_400_BAD_REQUEST,
                "Memory file path contains a symlink.",
            )
            descendants.append(child)
            stat_result = self._stat_path(child)
            if stat_module.S_ISDIR(stat_result.st_mode):
                descendants.extend(self._iter_descendants(child))
        return descendants

    def _session_root(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> Path:
        root = Path(self._root_resolver(user_id, agent_id, session_id))
        self._reject_symlink_existing_components(
            root,
            status.HTTP_400_BAD_REQUEST,
            "Memory root path contains a symlink.",
        )
        try:
            return root.resolve()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, root)

    def _require_backend_root(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        backend: str,
    ) -> Path:
        if backend not in self.SUPPORTED_BACKENDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported memory backend '{backend}'.",
            )
        root = (self._session_root(user_id, agent_id, session_id) / backend)
        self._reject_symlink(
            root,
            status.HTTP_404_NOT_FOUND,
            f"Memory backend '{backend}' not found.",
        )
        try:
            is_dir = root.is_dir()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, root)
        if not is_dir:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory backend '{backend}' not found.",
            )
        try:
            backend_root = root.resolve()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, root)
        return backend_root

    def _resolve_child(self, backend_root: Path, path: str) -> Path:
        self._validate_public_path(path)
        requested = Path(path)
        self._reject_symlink_child_components(backend_root, requested)
        try:
            target = (backend_root / requested).resolve()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, backend_root / requested)
        try:
            target.relative_to(backend_root)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Memory file path escapes the configured root.",
            ) from e
        return target

    def _require_regular_file(self, target: Path) -> None:
        stat_result = self._stat_path(target)
        if not stat_module.S_ISREG(stat_result.st_mode):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory file '{target.name}' not found.",
            )

    def _require_size(self, target: Path) -> None:
        if self._stat_path(target).st_size > self._max_file_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Memory file is too large.",
            )

    def _read_text(self, target: Path) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Memory file is not UTF-8 text.",
            ) from e
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, target)

    def _entry_payload(
        self,
        backend_root: Path,
        backend: str,
        target: Path,
    ) -> dict:
        self._reject_symlink(
            target,
            status.HTTP_400_BAD_REQUEST,
            "Memory file path contains a symlink.",
        )
        stat_result = self._stat_path(target)
        is_file = stat_module.S_ISREG(stat_result.st_mode)
        editable = (
            backend in self.EDITABLE_BACKENDS
            and is_file
            and target.suffix.lower() in self.EDITABLE_SUFFIXES
            and stat_result.st_size <= self._max_file_bytes
        )
        return {
            "path": target.relative_to(backend_root).as_posix(),
            "name": target.name,
            "type": "file" if is_file else "directory",
            "size": stat_result.st_size if is_file else None,
            "editable": editable,
            "readonly": not editable,
        }

    def _file_payload(
        self,
        backend_root: Path,
        backend: str,
        target: Path,
        content: str,
    ) -> dict:
        payload = self._entry_payload(backend_root, backend, target)
        return {
            "backend": backend,
            "path": payload["path"],
            "content": content,
            "size": payload["size"],
            "editable": payload["editable"],
            "readonly": payload["readonly"],
        }

    def _validate_public_path(self, path: str) -> None:
        if not path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Memory file path is required.",
            )
        if "\x00" in path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Memory file path contains an invalid character.",
            )
        windows_path = PureWindowsPath(path)
        posix_path = PurePosixPath(path)
        if (
            windows_path.drive
            or windows_path.root
            or windows_path.is_absolute()
            or posix_path.is_absolute()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Memory file path must be relative.",
            )

    def _reject_symlink_child_components(
        self,
        backend_root: Path,
        requested: Path,
    ) -> None:
        current = backend_root
        for part in requested.parts:
            if part in {"", "."}:
                continue
            current = current / part
            self._reject_symlink(
                current,
                status.HTTP_400_BAD_REQUEST,
                "Memory file path contains a symlink.",
            )

    def _reject_symlink_existing_components(
        self,
        target: Path,
        status_code: int,
        detail: str,
    ) -> None:
        current = Path(target.anchor) if target.anchor else Path()
        parts = target.parts[1:] if target.anchor else target.parts
        for part in parts:
            current = current / part
            self._reject_symlink(current, status_code, detail)
            try:
                exists = current.exists()
            except (OSError, ValueError) as e:
                self._raise_filesystem_error(e, current)
            if not exists:
                return

    def _reject_symlink(
        self,
        target: Path,
        status_code: int,
        detail: str,
    ) -> None:
        try:
            is_symlink = target.is_symlink()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, target)
        if is_symlink:
            raise HTTPException(status_code=status_code, detail=detail)

    def _stat_path(self, target: Path):
        try:
            return target.stat()
        except (OSError, ValueError) as e:
            self._raise_filesystem_error(e, target)

    def _raise_filesystem_error(
        self,
        error: OSError | ValueError,
        target: Path,
    ) -> None:
        if isinstance(error, FileNotFoundError):
            status_code = status.HTTP_404_NOT_FOUND
        elif isinstance(error, PermissionError):
            status_code = status.HTTP_403_FORBIDDEN
        elif isinstance(error, ValueError):
            status_code = status.HTTP_400_BAD_REQUEST
        else:
            status_code = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=status_code,
            detail=f"Memory filesystem error for '{target.name}'.",
        ) from error
