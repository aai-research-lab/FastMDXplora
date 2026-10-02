"""OS-protected provider records outside studies, with serialized updates.

Windows uses current-user DPAPI; macOS/Linux use a native OS keyring. There is
no plaintext fallback. The only plain file contains a random host identifier.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from fastmdxplora.user_dir import user_config_dir

_LOCK = threading.RLock()
MAX_RECORD_BYTES = 1_000_000


class VaultError(RuntimeError):
    """A safe error without credential material."""


def _dpapi(raw: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; no machine-wide scope or interactive prompts.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise VaultError("Windows could not protect or unlock the provider connection.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _native_keyring():
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        else:
            from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise VaultError("An unlocked native OS keyring is required for provider connections.")
        return backend
    except (ImportError, RuntimeError) as exc:
        raise VaultError("Install fastmdxplora[agent] and enable an unlocked native OS keyring.") from exc


class CredentialVault:
    def __init__(self, root: Path | None = None, *, protect: Callable | None = None,
                 unprotect: Callable | None = None):
        self.root = (root or user_config_dir() / "connections").resolve()
        self._protect = protect
        self._unprotect = unprotect

    @contextmanager
    def locked(self):
        """Hold the same lock through read, rotating refresh and atomic replace."""
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            lock_path = self.root / "vault.lock"
            if lock_path.is_symlink():
                raise VaultError("The provider storage location is invalid.")
            with lock_path.open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield self
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def host_id(self) -> str:
        target = self.root / "host.json"
        if target.is_symlink():
            raise VaultError("The provider host record is invalid.")
        if target.exists():
            try:
                host = json.loads(target.read_text(encoding="utf-8"))["host_id"]
                if host == "urn:uuid:" + str(uuid.UUID(host.removeprefix("urn:uuid:"))):
                    return host
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
            raise VaultError("The provider host record is invalid; do not replace an existing host silently.")
        host = "urn:uuid:" + str(uuid.uuid4())
        self._atomic(target, json.dumps({"host_id": host}).encode())
        return host

    def read(self) -> dict:
        target = self.root / "accounts.bin"
        if target.is_symlink():
            raise VaultError("The provider storage location is invalid.")
        try:
            if self._unprotect or os.name == "nt":
                if not target.exists():
                    return {"version": 1, "accounts": [], "active": None}
                if target.stat().st_size > MAX_RECORD_BYTES:
                    raise VaultError("The provider record exceeds its storage limit.")
                raw = target.read_bytes()
                clear = self._unprotect(raw) if self._unprotect else _dpapi(raw, decrypt=True)
            else:
                key = "host-" + self.host_id()
                text = _native_keyring().get_password("FastMDXplora connections", key)
                if text is None:
                    return {"version": 1, "accounts": [], "active": None}
                clear = text.encode("utf-8")
            if len(clear) > MAX_RECORD_BYTES:
                raise VaultError("The provider record exceeds its storage limit.")
            record = json.loads(clear)
            if not isinstance(record, dict) or record.get("version") != 1 or not isinstance(record.get("accounts"), list):
                raise VaultError("The protected provider record is invalid.")
            return record
        except (OSError, ValueError, TypeError) as exc:
            raise VaultError("The protected provider record could not be read.") from exc

    def write(self, record: dict) -> None:
        clear = json.dumps(record, allow_nan=False).encode("utf-8")
        if len(clear) > MAX_RECORD_BYTES:
            raise VaultError("The provider record exceeds its storage limit.")
        try:
            if self._protect or os.name == "nt":
                raw = self._protect(clear) if self._protect else _dpapi(clear)
                self._atomic(self.root / "accounts.bin", raw)
            else:
                _native_keyring().set_password("FastMDXplora connections", "host-" + self.host_id(), clear.decode("utf-8"))
        except OSError as exc:
            raise VaultError("The provider connection could not be securely saved.") from exc

    def _atomic(self, target: Path, data: bytes) -> None:
        if target.is_symlink():
            raise VaultError("The provider storage location is invalid.")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / (".vault-" + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
