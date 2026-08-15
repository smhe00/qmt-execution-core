from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Callable

from ..exceptions import AccountBindingError


_BINDING_SCHEMA_VERSION = 1
_ALLOWED_ENVIRONMENTS = {"simulation", "live"}
_BINDING_FIELDS = {
    "schema_version",
    "environment",
    "account_type",
    "account_id_sha256",
    "qmt_path_sha256",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def account_id_fingerprint(account_id: str) -> str:
    if type(account_id) is not str or not account_id:
        raise AccountBindingError("account_id must be a non-empty string")
    return _sha256_text(account_id)


def normalized_qmt_path(path: Path | str) -> str:
    candidate = Path(path).expanduser().resolve(strict=False)
    return os.path.normcase(str(candidate))


def qmt_path_fingerprint(path: Path | str) -> str:
    return _sha256_text(normalized_qmt_path(path))


def _validate_sha256(value: object, label: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise AccountBindingError(f"invalid {label}")
    return digest


@dataclass(frozen=True)
class QmtAccountBinding:
    environment: str
    account_type: int
    account_id_sha256: str
    qmt_path_sha256: str

    def __post_init__(self) -> None:
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            raise AccountBindingError("environment must be simulation or live")
        if type(self.account_type) is not int:
            raise AccountBindingError("account_type must be a plain int")
        object.__setattr__(
            self,
            "account_id_sha256",
            _validate_sha256(self.account_id_sha256, "account_id_sha256"),
        )
        object.__setattr__(
            self,
            "qmt_path_sha256",
            _validate_sha256(self.qmt_path_sha256, "qmt_path_sha256"),
        )

    @classmethod
    def create(
        cls,
        *,
        environment: str,
        account_type: int,
        account_id: str,
        qmt_path: Path | str,
    ) -> "QmtAccountBinding":
        return cls(
            environment=environment,
            account_type=account_type,
            account_id_sha256=account_id_fingerprint(account_id),
            qmt_path_sha256=qmt_path_fingerprint(qmt_path),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": _BINDING_SCHEMA_VERSION,
            "environment": self.environment,
            "account_type": self.account_type,
            "account_id_sha256": self.account_id_sha256,
            "qmt_path_sha256": self.qmt_path_sha256,
        }

    def write(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class BoundQmtAccount:
    account_id: str
    account_type: int
    account: object


def load_account_binding(
    path: Path | str,
    *,
    environment: str,
    qmt_path: Path | str,
) -> QmtAccountBinding:
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise AccountBindingError("environment must be simulation or live")
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountBindingError("account binding is unreadable") from exc
    if not isinstance(payload, dict):
        raise AccountBindingError("account binding must be a JSON object")
    if set(payload) != _BINDING_FIELDS:
        raise AccountBindingError("account binding fields do not match strict schema")
    if payload.get("schema_version") != _BINDING_SCHEMA_VERSION:
        raise AccountBindingError("account binding schema mismatch")
    if "account_id" in payload or "qmt_path" in payload:
        raise AccountBindingError("plaintext account/path is forbidden in binding")
    binding = QmtAccountBinding(
        environment=str(payload.get("environment", "")),
        account_type=payload.get("account_type"),
        account_id_sha256=payload.get("account_id_sha256"),
        qmt_path_sha256=payload.get("qmt_path_sha256"),
    )
    if binding.environment != environment:
        raise AccountBindingError("account binding environment mismatch")
    if binding.qmt_path_sha256 != qmt_path_fingerprint(qmt_path):
        raise AccountBindingError("QMT path fingerprint mismatch")
    return binding


def strict_non_none_query(
    fn: Callable[[], object],
    *,
    label: str,
    attempts: int = 3,
    delay_seconds: float = 0.15,
) -> object:
    if type(attempts) is not int or attempts <= 0:
        raise AccountBindingError("query attempts must be positive")
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            value = fn()
        except Exception as exc:
            last_exc = exc
            value = None
        if value is not None:
            return value
        if attempt + 1 < attempts:
            sleep(max(0.0, float(delay_seconds)))
    raise AccountBindingError(f"{label} remained ambiguous") from last_exc


def select_bound_account(
    trader: object,
    *,
    binding: QmtAccountBinding,
    security_account_type: int,
    account_status_ok: int,
    stock_account_factory: Callable[[str], object],
    attempts: int = 3,
    delay_seconds: float = 0.15,
) -> BoundQmtAccount:
    if type(security_account_type) is not int or type(account_status_ok) is not int:
        raise AccountBindingError("QMT account constants must be plain ints")
    if binding.account_type != security_account_type:
        raise AccountBindingError("binding account_type is not the expected securities type")

    infos = strict_non_none_query(
        lambda: getattr(trader, "query_account_infos")(),
        label="query_account_infos",
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    statuses = strict_non_none_query(
        lambda: getattr(trader, "query_account_status")(),
        label="query_account_status",
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    if not isinstance(infos, (list, tuple)) or not isinstance(statuses, (list, tuple)):
        raise AccountBindingError("account discovery returned unexpected types")

    candidates: list[tuple[str, int]] = []
    for info in infos:
        try:
            account_id = str(getattr(info, "account_id"))
            account_type = int(getattr(info, "account_type"))
        except (TypeError, ValueError, AttributeError):
            continue
        if (
            account_type == security_account_type
            and account_id_fingerprint(account_id) == binding.account_id_sha256
        ):
            candidates.append((account_id, account_type))

    if len(candidates) != 1:
        raise AccountBindingError("bound account discovery is not unique")
    account_id, account_type = candidates[0]

    healthy = [
        status
        for status in statuses
        if _status_matches(
            status,
            account_id=account_id,
            account_type=security_account_type,
            account_status_ok=account_status_ok,
        )
    ]
    if len(healthy) != 1:
        raise AccountBindingError("bound account is not uniquely healthy")

    account = stock_account_factory(account_id)
    return BoundQmtAccount(account_id=account_id, account_type=account_type, account=account)


def verify_bound_account_healthy(
    trader: object,
    bound: BoundQmtAccount,
    *,
    security_account_type: int,
    account_status_ok: int,
    attempts: int = 3,
    delay_seconds: float = 0.15,
) -> None:
    statuses = strict_non_none_query(
        lambda: getattr(trader, "query_account_status")(),
        label="query_account_status",
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    if not isinstance(statuses, (list, tuple)):
        raise AccountBindingError("account status query returned unexpected type")
    matches = [
        status
        for status in statuses
        if _status_matches(
            status,
            account_id=bound.account_id,
            account_type=security_account_type,
            account_status_ok=account_status_ok,
        )
    ]
    if len(matches) != 1:
        raise AccountBindingError("bound account is not healthy")


def _status_matches(
    status: object,
    *,
    account_id: str,
    account_type: int,
    account_status_ok: int,
) -> bool:
    try:
        return (
            str(getattr(status, "account_id")) == account_id
            and int(getattr(status, "account_type")) == account_type
            and int(getattr(status, "status")) == account_status_ok
        )
    except (TypeError, ValueError, AttributeError):
        return False
