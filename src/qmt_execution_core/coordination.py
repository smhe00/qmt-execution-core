from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .domain import BrokerAsset, ExecutionRequest, Side
from .exceptions import (
    CashReservationRejected,
    CoordinationError,
    CoordinationIdentityError,
    SymbolClaimConflict,
)
from .finality import ExecutionFinality


_COORDINATION_SCHEMA_VERSION = 1
_IDENTITY_SCHEMA_VERSION = 1
_EPSILON = 1e-9


def _plain_non_empty(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise CoordinationError(f"{label} must be a non-empty string")
    return value


def _finite_non_negative(value: object, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise CoordinationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise CoordinationError(f"{label} must be finite and non-negative")
    return number


def account_key_from_binding_identity(
    *,
    environment: str,
    account_type: int,
    account_id_sha256: str,
) -> str:
    """Build a stable non-plaintext account coordination key.

    The qmt path is intentionally excluded: the same broker account accessed
    through another userdata path must still share the same account cash pool.
    """

    if environment not in {"simulation", "live"}:
        raise CoordinationError("environment must be simulation or live")
    if type(account_type) is not int:
        raise CoordinationError("account_type must be a plain int")
    digest = str(account_id_sha256 or "").strip().lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise CoordinationError("account_id_sha256 must be a SHA-256 digest")
    material = f"qmt-execution-core|{environment}|{account_type}|{digest}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CashRequirementEstimate:
    """Conservative cash needed before a BUY broker side effect."""

    required_cash: float
    order_notional: float
    transaction_cost_buffer: float = 0.0
    temporary_withholding_buffer: float = 0.0
    fx_rounding_buffer: float = 0.0
    safety_buffer: float = 0.0

    def __post_init__(self) -> None:
        values = {}
        for name in (
            "required_cash",
            "order_notional",
            "transaction_cost_buffer",
            "temporary_withholding_buffer",
            "fx_rounding_buffer",
            "safety_buffer",
        ):
            values[name] = _finite_non_negative(getattr(self, name), name)
            object.__setattr__(self, name, values[name])

        component_floor = (
            values["order_notional"]
            + values["transaction_cost_buffer"]
            + values["temporary_withholding_buffer"]
            + values["fx_rounding_buffer"]
            + values["safety_buffer"]
        )
        if values["required_cash"] + _EPSILON < component_floor:
            raise CoordinationError(
                "required_cash cannot be lower than the declared conservative components"
            )


class CashRequirementEstimator(Protocol):
    def estimate(
        self,
        request: ExecutionRequest,
        account_snapshot: BrokerAsset,
    ) -> CashRequirementEstimate:
        ...


@dataclass(frozen=True)
class ConservativeCashRequirementEstimator:
    """Generic configurable estimator; contains no market-specific rules.

    Projects/account adapters provide the actual values.  For example a Hong
    Kong Stock Connect policy may configure a temporary withholding buffer,
    while an A-share policy may configure different fee/minimum values.
    """

    fee_rate: float = 0.0
    minimum_fee: float = 0.0
    temporary_withholding_buffer: float = 0.0
    fx_rounding_rate: float = 0.0
    safety_buffer: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "fee_rate",
            "minimum_fee",
            "temporary_withholding_buffer",
            "fx_rounding_rate",
            "safety_buffer",
        ):
            value = _finite_non_negative(getattr(self, name), name)
            object.__setattr__(self, name, value)

    def estimate(
        self,
        request: ExecutionRequest,
        account_snapshot: BrokerAsset,
    ) -> CashRequirementEstimate:
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request must be an ExecutionRequest")
        if not isinstance(account_snapshot, BrokerAsset):
            raise TypeError("account_snapshot must be a BrokerAsset")
        if request.side is not Side.BUY:
            return CashRequirementEstimate(required_cash=0.0, order_notional=0.0)

        notional = request.qty * float(request.limit_price)
        transaction_cost = max(self.minimum_fee, notional * self.fee_rate)
        fx_rounding = notional * self.fx_rounding_rate
        required = (
            notional
            + transaction_cost
            + self.temporary_withholding_buffer
            + fx_rounding
            + self.safety_buffer
        )
        return CashRequirementEstimate(
            required_cash=required,
            order_notional=notional,
            transaction_cost_buffer=transaction_cost,
            temporary_withholding_buffer=self.temporary_withholding_buffer,
            fx_rounding_buffer=fx_rounding,
            safety_buffer=self.safety_buffer,
        )


@dataclass(frozen=True)
class CoordinationDbIdentity:
    """Persistent identity metadata of a dedicated coordination DB (0.4.1).

    Generated once per DB instance (``db_uuid``); stable across normal
    content changes, claims, reservations, VACUUM and WAL checkpoints.  Only
    explicit creation of a new DB instance produces a new ``db_uuid``.
    """

    schema_version: int
    account_key: str
    db_uuid: str
    authority_id: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise CoordinationError("identity schema_version must be a positive int")
        _plain_non_empty(self.account_key, "account_key")
        _plain_non_empty(self.db_uuid, "db_uuid")
        _plain_non_empty(self.authority_id, "authority_id")


@dataclass(frozen=True)
class SymbolClaim:
    account_key: str
    symbol: str
    execution_id: str
    client_order_id: str
    finality: ExecutionFinality


@dataclass(frozen=True)
class CashReservation:
    account_key: str
    execution_id: str
    client_order_id: str
    symbol: str
    required_cash: float
    active: bool


class ExecutionCoordinator(Protocol):
    def prepare(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        broker_available_cash: float | None,
        required_cash: float | None,
    ) -> None:
        ...

    def restore(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        required_cash: float | None,
        finality: ExecutionFinality,
    ) -> None:
        ...

    def update_finality(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        finality: ExecutionFinality,
    ) -> None:
        ...

    def has_claim(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
    ) -> bool:
        ...


class SQLiteExecutionCoordinator:
    """Durable cross-process account-resource coordinator for one machine.

    SQLite ``BEGIN IMMEDIATE`` serializes the read-check-write reservation
    critical section across independent Python processes.  The database may
    contain multiple accounts; all shared resources are scoped by account_key.

    Core 0.4.1: an Authority-bound coordinator is constructed with
    ``expected_identity`` — the DB must already exist and its persistent
    identity metadata must match exactly (account_key / db_uuid /
    authority_id / schema_version), otherwise construction FAILS CLOSED.
    ``expected_identity=None`` keeps the 0.4.0 legacy explicit-path mode
    (no uniqueness guarantee).  Use :meth:`create` for the authorized
    bootstrap of a fresh dedicated DB instance.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        timeout_seconds: float = 5.0,
        expected_identity: CoordinationDbIdentity | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be numeric")
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        if expected_identity is not None and not isinstance(
            expected_identity, CoordinationDbIdentity
        ):
            raise TypeError("expected_identity must be a CoordinationDbIdentity or None")
        self.expected_identity = expected_identity
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        """Create all coordination tables if absent (shared by init + create)."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS coordination_meta (
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_claim (
                account_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                finality TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_key, symbol)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_symbol_claim_identity
            ON symbol_claim(account_key, execution_id, client_order_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cash_reservation (
                account_key TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                required_cash REAL NOT NULL CHECK(required_cash >= 0),
                active INTEGER NOT NULL CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                released_at TEXT,
                PRIMARY KEY(account_key, execution_id, client_order_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS coordination_identity (
                account_key TEXT NOT NULL,
                db_uuid TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                identity_schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_key)
            )
            """
        )

    def _initialize(self) -> None:
        try:
            if (
                self.expected_identity is not None
                and not self.path.exists()
            ):
                raise CoordinationIdentityError(
                    "certified coordination DB file is missing; refusing to "
                    "recreate or adopt a replacement at the same path"
                )
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_schema(connection)
                row = connection.execute(
                    "SELECT schema_version FROM coordination_meta LIMIT 1"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO coordination_meta(schema_version) VALUES (?)",
                        (_COORDINATION_SCHEMA_VERSION,),
                    )
                elif int(row["schema_version"]) != _COORDINATION_SCHEMA_VERSION:
                    raise CoordinationError("coordination schema version mismatch")

                if self.expected_identity is not None:
                    count = connection.execute(
                        "SELECT COUNT(*) AS n FROM coordination_identity"
                    ).fetchone()
                    if count is None or int(count["n"]) != 1:
                        raise CoordinationIdentityError(
                            "coordination DB must contain exactly one "
                            "authority identity row for the dedicated instance"
                        )
                    identity = connection.execute(
                        """
                        SELECT account_key, db_uuid, authority_id,
                               identity_schema_version
                        FROM coordination_identity
                        LIMIT 1
                        """
                    ).fetchone()
                    if identity is None:
                        raise CoordinationIdentityError(
                            "coordination DB has no authority identity metadata "
                            "(legacy 0.4.0 DB is not silently adopted)"
                        )
                    expected = self.expected_identity
                    if (
                        str(identity["account_key"]) != expected.account_key
                        or str(identity["db_uuid"]) != expected.db_uuid
                        or str(identity["authority_id"]) != expected.authority_id
                        or int(identity["identity_schema_version"])
                        != expected.schema_version
                    ):
                        raise CoordinationIdentityError(
                            "coordination DB identity does not match the "
                            "certified account Runtime Authority "
                            "(account_key / db_uuid / authority_id mismatch)"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        except (CoordinationError, CoordinationIdentityError):
            raise
        except sqlite3.Error as exc:
            raise CoordinationError("unable to initialize coordination database") from exc

    @classmethod
    def create(
        cls,
        path: Path | str,
        identity: CoordinationDbIdentity,
        *,
        timeout_seconds: float = 5.0,
    ) -> "SQLiteExecutionCoordinator":
        """Authorized bootstrap: create a fresh dedicated DB with identity.

        Only callable from the Atomic Authority bootstrap path (under the
        per-account authority lock).  Refuses to create over an existing
        file, so a recreated/replaced DB can never silently inherit the
        certified identity.
        """
        if not isinstance(identity, CoordinationDbIdentity):
            raise TypeError("identity must be a CoordinationDbIdentity")
        target = Path(path).expanduser().resolve(strict=False)
        if target.exists():
            raise CoordinationIdentityError(
                "refusing to create a coordination DB over an existing file"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(target),
            timeout=max(0.0, float(timeout_seconds)),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            cls._ensure_schema(connection)
            row = connection.execute(
                "SELECT schema_version FROM coordination_meta LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO coordination_meta(schema_version) VALUES (?)",
                    (_COORDINATION_SCHEMA_VERSION,),
                )
            connection.execute(
                """
                INSERT INTO coordination_identity(
                    account_key, db_uuid, authority_id, identity_schema_version
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    identity.account_key,
                    identity.db_uuid,
                    identity.authority_id,
                    identity.schema_version,
                ),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise CoordinationError("unable to create coordination database") from exc
        finally:
            connection.close()
        return cls(
            path,
            timeout_seconds=timeout_seconds,
            expected_identity=identity,
        )

    @staticmethod
    def _validate_identity(
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
    ) -> None:
        _plain_non_empty(account_key, "account_key")
        _plain_non_empty(execution_id, "execution_id")
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request must be an ExecutionRequest")

    def _begin(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise CoordinationError("unable to acquire coordination write transaction") from exc

    def prepare(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        broker_available_cash: float | None,
        required_cash: float | None,
    ) -> None:
        """Atomically claim the symbol and, for BUY, reserve conservative cash."""

        self._validate_identity(account_key, execution_id, request)
        if request.side is Side.BUY:
            available = _finite_non_negative(
                broker_available_cash, "broker_available_cash"
            )
            required = _finite_non_negative(required_cash, "required_cash")
        else:
            available = 0.0
            required = 0.0

        connection = self._connect()
        try:
            self._begin(connection)
            existing_claim = connection.execute(
                """
                SELECT execution_id, client_order_id
                FROM symbol_claim
                WHERE account_key=? AND symbol=?
                """,
                (account_key, request.symbol),
            ).fetchone()
            if existing_claim is not None:
                if not (
                    existing_claim["execution_id"] == execution_id
                    and existing_claim["client_order_id"] == request.client_order_id
                ):
                    raise SymbolClaimConflict(
                        "same account/symbol is owned by another unresolved execution"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO symbol_claim(
                        account_key, symbol, execution_id, client_order_id, finality
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        account_key,
                        request.symbol,
                        execution_id,
                        request.client_order_id,
                        ExecutionFinality.OPEN.value,
                    ),
                )

            if request.side is Side.BUY:
                existing_reservation = connection.execute(
                    """
                    SELECT required_cash, active
                    FROM cash_reservation
                    WHERE account_key=? AND execution_id=? AND client_order_id=?
                    """,
                    (account_key, execution_id, request.client_order_id),
                ).fetchone()
                if existing_reservation is not None:
                    if int(existing_reservation["active"]) != 1:
                        raise CoordinationError(
                            "released cash reservation identity cannot be reused"
                        )
                    if abs(float(existing_reservation["required_cash"]) - required) > _EPSILON:
                        raise CoordinationError(
                            "active reservation amount changed for one logical execution"
                        )
                else:
                    row = connection.execute(
                        """
                        SELECT COALESCE(SUM(required_cash), 0.0) AS total
                        FROM cash_reservation
                        WHERE account_key=? AND active=1
                        """,
                        (account_key,),
                    ).fetchone()
                    already_reserved = float(row["total"] if row is not None else 0.0)
                    effective = available - already_reserved
                    if required > effective + _EPSILON:
                        raise CashReservationRejected(
                            "conservative BUY cash requirement exceeds fresh broker cash "
                            "minus active cross-process reservations"
                        )
                    connection.execute(
                        """
                        INSERT INTO cash_reservation(
                            account_key, execution_id, client_order_id, symbol,
                            required_cash, active
                        ) VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            account_key,
                            execution_id,
                            request.client_order_id,
                            request.symbol,
                            required,
                        ),
                    )
            connection.commit()
        except (CoordinationError, SymbolClaimConflict, CashReservationRejected):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise CoordinationError("coordination prepare transaction failed") from exc
        finally:
            connection.close()

    def restore(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        required_cash: float | None,
        finality: ExecutionFinality,
    ) -> None:
        """Re-establish a missing durable claim for an already unresolved order.

        Availability is intentionally not re-checked here: an already-active
        broker order may have reduced broker cash.  Re-reserving conservatively
        is safer than treating the missing local reservation as free cash.
        """

        self._validate_identity(account_key, execution_id, request)
        if not isinstance(finality, ExecutionFinality):
            raise TypeError("finality must be ExecutionFinality")
        if finality is ExecutionFinality.RESOLVED:
            self.update_finality(
                account_key=account_key,
                execution_id=execution_id,
                request=request,
                finality=finality,
            )
            return
        required = (
            _finite_non_negative(required_cash, "required_cash")
            if request.side is Side.BUY
            else 0.0
        )

        connection = self._connect()
        try:
            self._begin(connection)
            claim = connection.execute(
                """
                SELECT execution_id, client_order_id
                FROM symbol_claim
                WHERE account_key=? AND symbol=?
                """,
                (account_key, request.symbol),
            ).fetchone()
            if claim is not None and not (
                claim["execution_id"] == execution_id
                and claim["client_order_id"] == request.client_order_id
            ):
                raise SymbolClaimConflict(
                    "cannot restore claim because another execution owns the symbol"
                )
            if claim is None:
                connection.execute(
                    """
                    INSERT INTO symbol_claim(
                        account_key, symbol, execution_id, client_order_id, finality
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        account_key,
                        request.symbol,
                        execution_id,
                        request.client_order_id,
                        finality.value,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE symbol_claim
                    SET finality=?, updated_at=CURRENT_TIMESTAMP
                    WHERE account_key=? AND symbol=?
                    """,
                    (finality.value, account_key, request.symbol),
                )

            if request.side is Side.BUY:
                reservation = connection.execute(
                    """
                    SELECT required_cash, active
                    FROM cash_reservation
                    WHERE account_key=? AND execution_id=? AND client_order_id=?
                    """,
                    (account_key, execution_id, request.client_order_id),
                ).fetchone()
                if reservation is None:
                    connection.execute(
                        """
                        INSERT INTO cash_reservation(
                            account_key, execution_id, client_order_id, symbol,
                            required_cash, active
                        ) VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            account_key,
                            execution_id,
                            request.client_order_id,
                            request.symbol,
                            required,
                        ),
                    )
                else:
                    restored = max(required, float(reservation["required_cash"]))
                    connection.execute(
                        """
                        UPDATE cash_reservation
                        SET required_cash=?, active=1, released_at=NULL,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE account_key=? AND execution_id=? AND client_order_id=?
                        """,
                        (
                            restored,
                            account_key,
                            execution_id,
                            request.client_order_id,
                        ),
                    )
            connection.commit()
        except (CoordinationError, SymbolClaimConflict):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise CoordinationError("coordination restore transaction failed") from exc
        finally:
            connection.close()

    def update_finality(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
        finality: ExecutionFinality,
    ) -> None:
        self._validate_identity(account_key, execution_id, request)
        if not isinstance(finality, ExecutionFinality):
            raise TypeError("finality must be ExecutionFinality")

        connection = self._connect()
        try:
            self._begin(connection)
            claim = connection.execute(
                """
                SELECT execution_id, client_order_id
                FROM symbol_claim
                WHERE account_key=? AND symbol=?
                """,
                (account_key, request.symbol),
            ).fetchone()
            if claim is not None and not (
                claim["execution_id"] == execution_id
                and claim["client_order_id"] == request.client_order_id
            ):
                raise SymbolClaimConflict(
                    "symbol claim belongs to another unresolved execution"
                )

            if finality is ExecutionFinality.RESOLVED:
                if claim is not None:
                    connection.execute(
                        """
                        DELETE FROM symbol_claim
                        WHERE account_key=? AND symbol=? AND execution_id=?
                              AND client_order_id=?
                        """,
                        (
                            account_key,
                            request.symbol,
                            execution_id,
                            request.client_order_id,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE cash_reservation
                    SET active=0, released_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE account_key=? AND execution_id=? AND client_order_id=?
                          AND active=1
                    """,
                    (account_key, execution_id, request.client_order_id),
                )
            else:
                if claim is None:
                    raise CoordinationError(
                        "unresolved execution has no durable account/symbol claim"
                    )
                connection.execute(
                    """
                    UPDATE symbol_claim
                    SET finality=?, updated_at=CURRENT_TIMESTAMP
                    WHERE account_key=? AND symbol=?
                    """,
                    (finality.value, account_key, request.symbol),
                )
            connection.commit()
        except (CoordinationError, SymbolClaimConflict):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise CoordinationError("coordination finality update failed") from exc
        finally:
            connection.close()

    def has_claim(
        self,
        *,
        account_key: str,
        execution_id: str,
        request: ExecutionRequest,
    ) -> bool:
        self._validate_identity(account_key, execution_id, request)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT execution_id, client_order_id
                FROM symbol_claim
                WHERE account_key=? AND symbol=?
                """,
                (account_key, request.symbol),
            ).fetchone()
            if row is None:
                return False
            if (
                row["execution_id"] == execution_id
                and row["client_order_id"] == request.client_order_id
            ):
                return True
            raise SymbolClaimConflict(
                "same account/symbol is owned by another unresolved execution"
            )
        except sqlite3.Error as exc:
            raise CoordinationError("coordination claim query failed") from exc
        finally:
            connection.close()

    def active_reserved_cash(self, account_key: str) -> float:
        _plain_non_empty(account_key, "account_key")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(required_cash), 0.0) AS total
                FROM cash_reservation
                WHERE account_key=? AND active=1
                """,
                (account_key,),
            ).fetchone()
            return float(row["total"] if row is not None else 0.0)
        except sqlite3.Error as exc:
            raise CoordinationError("coordination reservation query failed") from exc
        finally:
            connection.close()

    def get_claim(self, account_key: str, symbol: str) -> SymbolClaim | None:
        _plain_non_empty(account_key, "account_key")
        _plain_non_empty(symbol, "symbol")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT account_key, symbol, execution_id, client_order_id, finality
                FROM symbol_claim
                WHERE account_key=? AND symbol=?
                """,
                (account_key, symbol),
            ).fetchone()
            if row is None:
                return None
            return SymbolClaim(
                account_key=str(row["account_key"]),
                symbol=str(row["symbol"]),
                execution_id=str(row["execution_id"]),
                client_order_id=str(row["client_order_id"]),
                finality=ExecutionFinality(str(row["finality"])),
            )
        except sqlite3.Error as exc:
            raise CoordinationError("coordination claim query failed") from exc
        finally:
            connection.close()

    def get_reservation(
        self,
        account_key: str,
        execution_id: str,
        client_order_id: str,
    ) -> CashReservation | None:
        _plain_non_empty(account_key, "account_key")
        _plain_non_empty(execution_id, "execution_id")
        _plain_non_empty(client_order_id, "client_order_id")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT account_key, execution_id, client_order_id, symbol,
                       required_cash, active
                FROM cash_reservation
                WHERE account_key=? AND execution_id=? AND client_order_id=?
                """,
                (account_key, execution_id, client_order_id),
            ).fetchone()
            if row is None:
                return None
            return CashReservation(
                account_key=str(row["account_key"]),
                execution_id=str(row["execution_id"]),
                client_order_id=str(row["client_order_id"]),
                symbol=str(row["symbol"]),
                required_cash=float(row["required_cash"]),
                active=bool(int(row["active"])),
            )
        except sqlite3.Error as exc:
            raise CoordinationError("coordination reservation query failed") from exc
        finally:
            connection.close()
