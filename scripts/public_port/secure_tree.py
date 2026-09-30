from __future__ import annotations

import hashlib
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable


class SecureTreeError(ValueError):
    """A filesystem tree violated the descriptor-relative no-follow contract."""


def _parts(relative_path: str) -> tuple[str, ...]:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or relative_path != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SecureTreeError(f"path must be canonical and relative: {relative_path}")
    return path.parts


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def open_absolute_directory_no_follow(path: Path) -> int:
    directory = Path(path)
    if not directory.is_absolute() or str(directory) != os.path.abspath(directory):
        raise SecureTreeError("absolute directory path must be canonical")
    descriptor = os.open("/", _directory_flags())
    try:
        for part in directory.parts[1:]:
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            observed = os.fstat(child)
            if not stat.S_ISDIR(observed.st_mode):
                os.close(child)
                raise SecureTreeError("absolute path parent is not a directory")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise SecureTreeError(
            "absolute directory cannot be opened with the no-follow contract"
        ) from error
    except Exception:
        os.close(descriptor)
        raise


def atomic_write_file_at(
    directory_fd: int,
    name: str,
    body: bytes,
    *,
    mode: int = 0o600,
) -> None:
    if not name or name in {".", ".."} or "/" in name:
        raise SecureTreeError("destination name must be a single path component")
    temporary = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor = -1
    try:
        try:
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            observed = None
        if observed is not None and not stat.S_ISREG(observed.st_mode):
            raise SecureTreeError("destination must be absent or a regular file")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(temporary, flags, mode, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SecureTreeError("temporary destination is not a regular file")
        view = memoryview(body)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except OSError as error:
        raise SecureTreeError(
            "destination cannot be atomically written with the no-follow contract"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


class SecureTree:
    """A root-fd anchored tree reader/writer for POSIX public-port staging roots."""

    def __init__(self, root: Path):
        self.path = Path(root)
        try:
            self._fd = os.open(self.path, _directory_flags())
        except OSError as error:
            raise SecureTreeError(f"secure tree root is unavailable: {self.path}") from error
        root_stat = os.fstat(self._fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            os.close(self._fd)
            raise SecureTreeError(f"secure tree root is not a directory: {self.path}")
        self.identity = (root_stat.st_dev, root_stat.st_ino)

    def __enter__(self) -> SecureTree:
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def _open_parent(self, relative_path: str, *, create: bool = False) -> tuple[int, str]:
        parts = _parts(relative_path)
        descriptor = os.dup(self._fd)
        try:
            for part in parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
                child_stat = os.fstat(child)
                if not stat.S_ISDIR(child_stat.st_mode):
                    os.close(child)
                    raise SecureTreeError(f"path parent is not a directory: {relative_path}")
                os.close(descriptor)
                descriptor = child
            return descriptor, parts[-1]
        except OSError as error:
            os.close(descriptor)
            raise SecureTreeError(f"path parent is not a no-follow directory: {relative_path}") from error
        except Exception:
            os.close(descriptor)
            raise

    def read_file(self, relative_path: str, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must not be negative")
        parent, name = self._open_parent(relative_path)
        descriptor = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise SecureTreeError(f"path is not a regular file: {relative_path}")
            if max_bytes is not None and observed.st_size > max_bytes:
                raise SecureTreeError(f"regular file exceeds the bounded read: {relative_path}")
            chunks: list[bytes] = []
            total = 0
            while True:
                read_size = 1024 * 1024
                if max_bytes is not None:
                    read_size = min(read_size, max_bytes - total + 1)
                chunk = os.read(descriptor, read_size)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise SecureTreeError(
                        f"regular file exceeds the bounded read: {relative_path}"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
        except FileNotFoundError as error:
            raise SecureTreeError(f"regular file is missing: {relative_path}") from error
        except OSError as error:
            raise SecureTreeError(f"regular file cannot be opened without following links: {relative_path}") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def write_file(self, relative_path: str, body: bytes, mode: str) -> None:
        if mode not in {"100644", "100755"}:
            raise SecureTreeError(f"unsupported regular-file mode: {mode}")
        parent, name = self._open_parent(relative_path, create=True)
        descriptor = -1
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_TRUNC
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            descriptor = os.open(name, flags, 0o600, dir_fd=parent)
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise SecureTreeError(f"destination is not a regular file: {relative_path}")
            view = memoryview(body)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fchmod(descriptor, 0o755 if mode == "100755" else 0o644)
            os.fsync(descriptor)
        except OSError as error:
            raise SecureTreeError(
                f"destination cannot be written without following links: {relative_path}"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def _remove_entry(self, parent_fd: int, name: str) -> None:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISDIR(observed.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=parent_fd)
            try:
                for child_name in os.listdir(child):
                    self._remove_entry(child, child_name)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=parent_fd)
            return
        os.unlink(name, dir_fd=parent_fd)

    def clear(self, *, keep_root_names: Iterable[str] = ()) -> None:
        keep = set(keep_root_names)
        for name in os.listdir(self._fd):
            if name not in keep:
                self._remove_entry(self._fd, name)

    def inventory(
        self,
        *,
        skip_root_names: Iterable[str] = (),
        forbidden_root_names: Iterable[str] = (),
    ) -> dict[str, dict[str, str]]:
        skip = set(skip_root_names)
        forbidden = set(forbidden_root_names)
        result: dict[str, dict[str, str]] = {}

        def walk(directory_fd: int, prefix: str) -> None:
            for name in sorted(os.listdir(directory_fd)):
                if not prefix and name in forbidden:
                    raise SecureTreeError(f"forbidden root entry exists: {name}")
                if not prefix and name in skip:
                    continue
                relative = f"{prefix}/{name}" if prefix else name
                observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(observed.st_mode):
                    raise SecureTreeError(f"tree contains a symlink: {relative}")
                if stat.S_ISDIR(observed.st_mode):
                    child = os.open(name, _directory_flags(), dir_fd=directory_fd)
                    try:
                        if not os.listdir(child):
                            raise SecureTreeError(f"tree contains an unlisted empty directory: {relative}")
                        walk(child, relative)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(observed.st_mode):
                    raise SecureTreeError(f"tree contains a special file: {relative}")
                body = self.read_file(relative)
                result[relative] = {
                    "path": relative,
                    "mode": "100755" if observed.st_mode & stat.S_IXUSR else "100644",
                    "sha256": hashlib.sha256(body).hexdigest(),
                }

        walk(self._fd, "")
        return result
