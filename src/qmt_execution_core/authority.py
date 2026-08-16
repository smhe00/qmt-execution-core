"""Account Runtime Authority — Core 0.4.1 (docs/CORE_0_4_1_RUNTIME_AUTHORITY_SPEC.md).

Closes the Core 0.4.0 split-brain configuration hole: shared execution for
one authoritative account must resolve through ONE canonical Runtime
Authority that certifies exactly ONE dedicated coordination DB instance by
canonical path + persistent DB UUID.

Invariants (INV-AUTH-001 / INV-AUTH-002):

* authority filename is derived from the stable ``account_key`` — strategy
  code never chooses it;
* shared execution requires
  ``runtime account_key == authority.account_key == DB metadata.account_key``,
  ``canonical(opened DB path) == authority.coordination_db_path``,
  ``authority.coordination_db_uuid == DB metadata.db_uuid``,
  ``authority.authority_id == DB metadata.authority_id``;
* any mismatch, missing authority (non-bootstrap), corrupt/truncated
  authority, or recreated DB at the same path FAILS CLOSED — Core never
  silently adopts, rewrites, or falls back to a second domain.

First initialization is protected by an OS-backed per-account authority lock
(:class:`ExecutionMutex`, cross-process on Windows and POSIX); two racing
processes converge on one authority_id / db_uuid / domain.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .coordination import (
    CoordinationDbIdentity,
    SQLiteExecutionCoordinator,
    account_key_from_binding_identity,
)
from .exceptions import RuntimeAuthorityError
from .mutex import ExecutionMutex

_AUTHORITY_SCHEMA_VERSION = 1
_IDENTITY_SCHEMA_VERSION = 1
_AUTHORITY_LOCK_TIMEOUT_SECONDS = 10.0


def default_authority_root() -> Path:
    """Canonical host/user-level Core authority root (INV-AUTH-001).

    NOT strategy-configurable and NOT derived from mutable process
    environment (no LOCALAPPDATA / USERPROFILE / HOME / Path.home / XDG
    overrides).  The root comes from the OS user identity itself, so every
    strategy process for the same OS user on the same host resolves the
    identical root and the same account cannot be split across two
    Authority/DB domains:

    * Windows: ``FOLDERID_LocalAppData`` via the Windows Known Folder API
      (``SHGetKnownFolderPath``), never ``%LOCALAPPDATA%``;
    * POSIX: the OS user's home from the user database (``pwd.getpwuid``),
      never ``$HOME``.

    If the authoritative OS lookup fails, this raises
    :class:`RuntimeAuthorityError` and fails closed — it never falls back to
    an environment-derived path.

    Tests inject an explicit root only through the low-level
    :class:`AccountRuntimeAuthority` / ``MiniQmtRuntime.connect(authority=)``
    API, never through production runtime configuration.
    """
    if os.name == "nt":
        return (
            _windows_known_folder_local_appdata()
            / "qmt-execution-core"
            / "authority"
        )
    return (
        _posix_user_home()
        / ".local"
        / "share"
        / "qmt-execution-core"
        / "authority"
    )


def _windows_known_folder_local_appdata() -> Path:
    """Resolve FOLDERID_LocalAppData through the Windows Known Folder API.

    Isolated so tests can force a lookup failure without touching the OS.
    """
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    # FOLDERID_LocalAppData = {F1B32785-6FBA-4FCF-9D55-7B8E7F157091}
    folder_id = _GUID(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (wintypes.BYTE * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    path_ptr = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr)
        )
        if hr != 0 or not path_ptr.value:
            raise RuntimeAuthorityError(
                "Windows Known Folder lookup failed (FOLDERID_LocalAppData); "
                "cannot resolve a canonical authority root"
            )
        return Path(path_ptr.value)
    except RuntimeAuthorityError:
        raise
    except Exception as exc:
        raise RuntimeAuthorityError(
            "Windows Known Folder lookup failed (FOLDERID_LocalAppData); "
            "cannot resolve a canonical authority root"
        ) from exc
    finally:
        if path_ptr.value:
            try:
                ctypes.windll.ole32.CoTaskMemFree(
                    ctypes.cast(path_ptr, ctypes.c_void_p)
                )
            except Exception:
                pass


def _posix_user_home() -> Path:
    """Resolve the OS user's home through the user database (no $HOME)."""
    try:
        import pwd

        home = pwd.getpwuid(os.getuid()).pw_dir
    except Exception as exc:
        raise RuntimeAuthorityError(
            "POSIX user-database home lookup failed; cannot resolve a "
            "canonical authority root"
        ) from exc
    if not home:
        raise RuntimeAuthorityError(
            "POSIX user-database home is empty; cannot resolve a canonical "
            "authority root"
        )
    return Path(home)


def _require_account_key(account_key: object) -> str:
    if type(account_key) is not str or not account_key:
        raise RuntimeAuthorityError("account_key must be a non-empty string")
    return account_key


def _require_environment(environment: object) -> str:
    if type(environment) is not str or environment not in {"simulation", "live"}:
        raise RuntimeAuthorityError("environment must be 'simulation' or 'live'")
    return environment


def _require_account_type(account_type: object) -> int:
    if type(account_type) is not int:
        raise RuntimeAuthorityError("account_type must be a plain int")
    return account_type


def _require_account_id_sha256(account_id_sha256: object) -> str:
    digest = str(account_id_sha256 or "").strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RuntimeAuthorityError("account_id_sha256 must be a SHA-256 hex digest")
    return digest


def _require_uuid(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise RuntimeAuthorityError(f"{label} must be a non-empty string")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise RuntimeAuthorityError(f"{label} is not a valid UUID") from exc
    return value


@dataclass(frozen=True)
class AccountAuthority:
    """Canonical per-account Runtime Authority record (spec §4)."""

    schema_version: int
    authority_id: str
    account_key: str
    environment: str
    account_type: int
    account_id_sha256: str
    coordination_db_path: str
    coordination_db_uuid: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise RuntimeAuthorityError("authority schema_version must be a positive int")
        _require_uuid(self.authority_id, "authority_id")
        _require_account_key(self.account_key)
        _require_environment(self.environment)
        _require_account_type(self.account_type)
        _require_account_id_sha256(self.account_id_sha256)
        if type(self.coordination_db_path) is not str or not self.coordination_db_path:
            raise RuntimeAuthorityError("coordination_db_path must be a non-empty string")
        if not Path(self.coordination_db_path).is_absolute():
            raise RuntimeAuthorityError("coordination_db_path must be absolute")
        _require_uuid(self.coordination_db_uuid, "coordination_db_uuid")

    def to_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "authority_id": self.authority_id,
            "account_key": self.account_key,
            "environment": self.environment,
            "account_type": self.account_type,
            "account_id_sha256": self.account_id_sha256,
            "coordination_db_path": self.coordination_db_path,
            "coordination_db_uuid": self.coordination_db_uuid,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "AccountAuthority":
        if not isinstance(payload, dict):
            raise RuntimeAuthorityError("authority payload must be a JSON object")
        required = {
            "schema_version",
            "authority_id",
            "account_key",
            "environment",
            "account_type",
            "account_id_sha256",
            "coordination_db_path",
            "coordination_db_uuid",
        }
        if set(payload) != required:
            raise RuntimeAuthorityError("authority payload fields do not match the schema")
        try:
            return cls(
                schema_version=int(payload["schema_version"]),
                authority_id=str(payload["authority_id"]),
                account_key=str(payload["account_key"]),
                environment=str(payload["environment"]),
                account_type=int(payload["account_type"]),
                account_id_sha256=str(payload["account_id_sha256"]),
                coordination_db_path=str(payload["coordination_db_path"]),
                coordination_db_uuid=str(payload["coordination_db_uuid"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeAuthorityError("authority payload is malformed") from exc


class AccountRuntimeAuthority:
    """OS-lock-backed store for the canonical per-account Runtime Authority.

    ``root`` is the canonical host/user authority root in production (see
    :func:`default_authority_root`); tests inject an explicit temporary root
    (spec §3, test-only injection is explicitly isolated).
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)

    def authority_path(self, account_key: object) -> Path:
        key = _require_account_key(account_key)
        return self.root / f"{key}.authority.json"

    def lock_path(self, account_key: object) -> Path:
        key = _require_account_key(account_key)
        return self.root / f"{key}.authority.lock"

    def default_coordination_db_path(self, account_key: object) -> Path:
        key = _require_account_key(account_key)
        return self.root / f"{key}.coordination.db"

    def resolve(
        self,
        *,
        account_key: str,
        environment: str,
        account_type: int,
        account_id_sha256: str,
        coordination_db_path: object | None = None,
        bootstrap: bool = False,
    ) -> AccountAuthority:
        """Resolve and verify the canonical Authority for one account.

        If the Authority exists it is verified only — never silently
        rewritten.  If it is missing and ``bootstrap`` is True, the atomic
        first-initialization path creates the dedicated DB + Authority under
        the per-account OS lock.  If it is missing and ``bootstrap`` is
        False, construction FAILS CLOSED (no silent adoption, no fallback).
        """
        key = _require_account_key(account_key)
        env = _require_environment(environment)
        acct_type = _require_account_type(account_type)
        digest = _require_account_id_sha256(account_id_sha256)
        # P2 hardening: the identity tuple must be internally consistent — an
        # Authority record can never be created with a logically inconsistent
        # account_key for the given environment/account_type/account_id.
        derived_key = account_key_from_binding_identity(
            environment=env,
            account_type=acct_type,
            account_id_sha256=digest,
        )
        if derived_key != key:
            raise RuntimeAuthorityError(
                "account_key is inconsistent with the given "
                "environment/account_type/account_id identity tuple"
            )

        lock = ExecutionMutex(
            self.lock_path(key),
            timeout_seconds=_AUTHORITY_LOCK_TIMEOUT_SECONDS,
            poll_seconds=0.1,
        )
        lock.acquire()
        try:
            authority = self._read_authority(self.authority_path(key), key)
            if authority is not None:
                self._verify_authority(
                    authority,
                    account_key=key,
                    environment=env,
                    account_type=acct_type,
                    account_id_sha256=digest,
                    coordination_db_path=coordination_db_path,
                )
                return authority
            if not bootstrap:
                raise RuntimeAuthorityError(
                    "account Runtime Authority is missing; explicit bootstrap "
                    "required — refusing to silently create a second "
                    "coordination domain"
                )
            return self._bootstrap(
                account_key=key,
                environment=env,
                account_type=acct_type,
                account_id_sha256=digest,
                coordination_db_path=coordination_db_path,
            )
        finally:
            lock.release()

    def _bootstrap(
        self,
        *,
        account_key: str,
        environment: str,
        account_type: int,
        account_id_sha256: str,
        coordination_db_path: object | None,
    ) -> AccountAuthority:
        """Atomic first-initialization (spec §7) — caller owns the lock."""
        db_path = (
            Path(coordination_db_path).expanduser().resolve(strict=False)
            if coordination_db_path is not None
            else self.default_coordination_db_path(account_key)
        )
        if not db_path.is_absolute():
            raise RuntimeAuthorityError("coordination_db_path must be absolute")
        authority_id = str(uuid.uuid4())
        db_uuid = str(uuid.uuid4())
        identity = CoordinationDbIdentity(
            schema_version=_IDENTITY_SCHEMA_VERSION,
            account_key=account_key,
            db_uuid=db_uuid,
            authority_id=authority_id,
        )
        # Refuses to create over an existing file (fail closed on a
        # recreated/replaced DB at the same path).
        SQLiteExecutionCoordinator.create(db_path, identity)
        authority = AccountAuthority(
            schema_version=_AUTHORITY_SCHEMA_VERSION,
            authority_id=authority_id,
            account_key=account_key,
            environment=environment,
            account_type=account_type,
            account_id_sha256=account_id_sha256,
            coordination_db_path=str(db_path),
            coordination_db_uuid=db_uuid,
        )
        self._write_authority_atomic(self.authority_path(account_key), authority)
        return authority

    def _read_authority(
        self, path: Path, account_key: str
    ) -> Optional[AccountAuthority]:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeAuthorityError(
                f"account Runtime Authority is corrupt or unreadable: {path}"
            ) from exc
        authority = AccountAuthority.from_payload(payload)
        if authority.account_key != account_key:
            raise RuntimeAuthorityError(
                "account Runtime Authority identity does not match the "
                "requested account_key"
            )
        return authority

    @staticmethod
    def _verify_authority(
        authority: AccountAuthority,
        *,
        account_key: str,
        environment: str,
        account_type: int,
        account_id_sha256: str,
        coordination_db_path: object | None,
    ) -> None:
        """INV-AUTH-002 pre-checks on the Authority record itself."""
        if authority.account_key != account_key:
            raise RuntimeAuthorityError(
                "authority account_key mismatch with the runtime account"
            )
        if (
            authority.environment != environment
            or authority.account_type != account_type
            or authority.account_id_sha256 != account_id_sha256
        ):
            raise RuntimeAuthorityError(
                "authority environment/account identity does not match the "
                "runtime account binding"
            )
        if coordination_db_path is not None:
            intended = Path(coordination_db_path).expanduser().resolve(strict=False)
            certified = Path(authority.coordination_db_path).expanduser().resolve(
                strict=False
            )
            if intended != certified:
                raise RuntimeAuthorityError(
                    "intended coordination DB path does not match the "
                    "Authority-certified canonical path"
                )

    def _write_authority_atomic(
        self, path: Path, authority: AccountAuthority
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(authority.to_payload(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
