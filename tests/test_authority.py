"""Core 0.4.1 Account Runtime Authority — in-process acceptance matrix.

Spec: docs/CORE_0_4_1_RUNTIME_AUTHORITY_SPEC.md §11 (scenarios 1-9, 11,
13-14) plus the P1-5 fail-closed matrix.  Cross-process scenarios 10 and 12
live in test_authority_cross_process.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from qmt_execution_core import (
    AccountAuthority,
    AccountRuntimeAuthority,
    CoordinationDbIdentity,
    ExecutionRequest,
    PrecheckEvidence,
    SessionEvidence,
    Side,
    TradeState,
)
from qmt_execution_core.authority import default_authority_root
from qmt_execution_core.coordination import (
    SQLiteExecutionCoordinator,
    account_key_from_binding_identity,
)
from qmt_execution_core.exceptions import (
    CoordinationIdentityError,
    RuntimeAuthorityError,
    RuntimeConfigurationError,
)
from qmt_execution_core.miniqmt import (
    MiniQmtRuntime,
    MiniQmtRuntimeConfig,
    QmtAccountBinding,
)

from test_miniqmt_runtime import (
    AllowGuard,
    CallbackBase,
    FakeTrader,
    StockAccount,
    XtConstant,
)

IDENTITY_SCHEMA_VERSION = 1


def _zero_estimator():
    from qmt_execution_core.coordination import ConservativeCashRequirementEstimator

    return ConservativeCashRequirementEstimator(fee_rate=0.0, minimum_fee=0.0)


def _key(account_id_sha256: str = "a" * 64) -> str:
    """Derive the canonical account_key for a fake account identity."""
    from qmt_execution_core.coordination import account_key_from_binding_identity

    return account_key_from_binding_identity(
        environment="simulation", account_type=2,
        account_id_sha256=account_id_sha256,
    )


def _identity_for(authority: AccountAuthority) -> CoordinationDbIdentity:
    return CoordinationDbIdentity(
        schema_version=IDENTITY_SCHEMA_VERSION,
        account_key=authority.account_key,
        db_uuid=authority.coordination_db_uuid,
        authority_id=authority.authority_id,
    )


def _tamper_db_identity(db_path: Path, **fields) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        if fields:
            assignments = ", ".join(f"{name}=?" for name in fields)
            connection.execute(
                f"UPDATE coordination_identity SET {assignments}", tuple(fields.values())
            )
            connection.commit()
    finally:
        connection.close()


class TestAuthorityResolveAndVerify:
    def test_scenario1_same_account_resolves_same_authority_file(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        first = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        second = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=False,
        )
        # Same canonical Authority file for the same account.
        assert authority.authority_path(key) == root / f"{key}.authority.json"
        assert first.authority_id == second.authority_id
        assert first.coordination_db_uuid == second.coordination_db_uuid
        assert first.coordination_db_path == second.coordination_db_path

    def test_scenario2_same_certified_db_path_and_uuid(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        first = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        second = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=False,
        )
        db = Path(first.coordination_db_path)
        assert db.exists()
        coordinator = SQLiteExecutionCoordinator(
            db, expected_identity=_identity_for(first)
        )
        assert coordinator.path == db.resolve()
        assert first.coordination_db_uuid == second.coordination_db_uuid

    def test_scenario3_different_accounts_different_authority_and_db(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key_a = _key("a" * 64)
        key_b = _key("b" * 64)
        auth_a = authority.resolve(
            account_key=key_a, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        auth_b = authority.resolve(
            account_key=key_b, environment="simulation", account_type=2,
            account_id_sha256="b" * 64, bootstrap=True,
        )
        assert key_a != key_b
        assert authority.authority_path(key_a) != authority.authority_path(key_b)
        assert auth_a.authority_id != auth_b.authority_id
        assert auth_a.coordination_db_uuid != auth_b.coordination_db_uuid
        assert auth_a.coordination_db_path != auth_b.coordination_db_path

    def test_scenario4_authority_account_key_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key_a = _key("a" * 64)
        key_b = _key("b" * 64)
        authority.resolve(
            account_key=key_a, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        # Force the A1 authority record onto the A2 canonical filename.
        (root / f"{key_a}.authority.json").rename(root / f"{key_b}.authority.json")
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key_b, environment="simulation", account_type=2,
                account_id_sha256="b" * 64, bootstrap=False,
            )

    def test_scenario4b_authority_identity_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        # Environment/identity mismatch on the existing authority.
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key, environment="live", account_type=2,
                account_id_sha256="a" * 64, bootstrap=False,
            )
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key, environment="simulation", account_type=2,
                account_id_sha256="b" * 64, bootstrap=False,
            )

    def test_scenario5_db_path_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64,
            coordination_db_path=str(tmp_path / "custom.db"), bootstrap=True,
        )
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key, environment="simulation", account_type=2,
                account_id_sha256="a" * 64,
                coordination_db_path=str(tmp_path / "other.db"), bootstrap=False,
            )

    def test_scenario6_db_uuid_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        auth = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        _tamper_db_identity(Path(auth.coordination_db_path), db_uuid="0" * 36)
        with pytest.raises(CoordinationIdentityError):
            SQLiteExecutionCoordinator(
                auth.coordination_db_path, expected_identity=_identity_for(auth)
            )

    def test_scenario7_db_account_key_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        auth = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        _tamper_db_identity(Path(auth.coordination_db_path), account_key="x" * 64)
        with pytest.raises(CoordinationIdentityError):
            SQLiteExecutionCoordinator(
                auth.coordination_db_path, expected_identity=_identity_for(auth)
            )

    def test_scenario8_db_authority_id_mismatch_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        auth = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        _tamper_db_identity(Path(auth.coordination_db_path), authority_id="0" * 36)
        with pytest.raises(CoordinationIdentityError):
            SQLiteExecutionCoordinator(
                auth.coordination_db_path, expected_identity=_identity_for(auth)
            )

    def test_scenario9_db_recreated_at_same_path_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        auth = authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        db_path = Path(auth.coordination_db_path)
        db_path.unlink()
        # Recreate an empty DB at the same path: no identity metadata -> the
        # certified instance is NOT silently adopted.
        sqlite3.connect(str(db_path)).close()
        with pytest.raises(CoordinationIdentityError):
            SQLiteExecutionCoordinator(
                db_path, expected_identity=_identity_for(auth)
            )
        # The authorized create path also refuses to overwrite the file.
        with pytest.raises(CoordinationIdentityError):
            SQLiteExecutionCoordinator.create(db_path, _identity_for(auth))

    def test_scenario11_corrupt_authority_fails_closed_no_fallback_db(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        authority.resolve(
            account_key=key, environment="simulation", account_type=2,
            account_id_sha256="a" * 64, bootstrap=True,
        )
        db_files_before = set(root.glob("*.coordination.db"))
        authority_path = authority.authority_path(key)
        authority_path.write_text("{truncated json", encoding="utf-8")
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key, environment="simulation", account_type=2,
                account_id_sha256="a" * 64, bootstrap=False,
            )
        # No NEW/fallback coordination domain is created anywhere.
        assert set(root.glob("*.coordination.db")) == db_files_before

    def test_missing_authority_without_bootstrap_fails_closed(self, tmp_path):
        root = tmp_path / "auth"
        authority = AccountRuntimeAuthority(root)
        key = _key()
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve(
                account_key=key, environment="simulation", account_type=2,
                account_id_sha256="a" * 64, bootstrap=False,
            )
        # No authority record and no coordination DB were created (only the
        # OS lock file may exist from the failed resolve).
        assert not list(root.glob("*.authority.json"))
        assert not list(root.glob("*.coordination.db"))


class TestRuntimeAuthorityIntegration:
    """Production shared runtime resolves through the Account Runtime Authority."""

    def _connect(self, tmp_path, *, account_id="A123", strategy_name="demo",
                 authority_root=None, bootstrap_authority=True):
        qmt_path = tmp_path / "userdata_mini"
        qmt_path.mkdir(parents=True, exist_ok=True)
        binding_path = tmp_path / "binding.json"
        binding = QmtAccountBinding.create(
            environment="simulation", account_type=2, account_id=account_id,
            qmt_path=qmt_path,
        )
        binding.write(binding_path)
        config = MiniQmtRuntimeConfig(
            environment="simulation",
            qmt_path=qmt_path,
            binding_path=binding_path,
            journal_path=tmp_path / f"j-{strategy_name}.json",
            lock_path=tmp_path / f"e-{strategy_name}.lock",
            strategy_name=strategy_name,
            query_delay_seconds=0,
            runtime_lock_mode="shared",
        )
        holder = {}

        def factory(path, session_id):
            trader = FakeTrader(path, session_id)
            holder["trader"] = trader
            return trader

        # Test-only low-level injection: the runtime itself NEVER bootstraps.
        root = Path(authority_root) if authority_root else (tmp_path / "authority")
        store = AccountRuntimeAuthority(root)
        if bootstrap_authority:
            account_key = account_key_from_binding_identity(
                environment=binding.environment,
                account_type=binding.account_type,
                account_id_sha256=binding.account_id_sha256,
            )
            store.resolve(
                account_key=account_key,
                environment=binding.environment,
                account_type=binding.account_type,
                account_id_sha256=binding.account_id_sha256,
                coordination_db_path=None,
                bootstrap=True,
            )
        runtime = MiniQmtRuntime.connect(
            config,
            guard=AllowGuard(),
            trader_factory=factory,
            stock_account_factory=StockAccount,
            xtconstant=XtConstant,
            callback_base=CallbackBase,
            cash_estimator=_zero_estimator(),
            authority=store,
        )
        return runtime, holder["trader"]

    def _request(self, key="c1", strategy="demo"):
        return ExecutionRequest(
            key, "510300.SH", Side.BUY, 100, 4.7, strategy, key,
        )

    def test_shared_runtime_bootstraps_and_executes_via_authority(self, tmp_path):
        runtime, trader = self._connect(tmp_path)
        try:
            from qmt_execution_core.coordinated_session import CoordinatedExecutionSession

            assert isinstance(runtime.session, CoordinatedExecutionSession)
            out = runtime.submit(self._request())
            assert out.state is TradeState.WORKING
            assert trader.place_calls == 1
            # Authority + dedicated DB exist under the canonical root.
            root = tmp_path / "authority"
            assert list(root.glob("*.authority.json"))
            assert list(root.glob("*.coordination.db"))
        finally:
            runtime.close()

    def test_same_account_two_runtimes_share_one_certified_domain(self, tmp_path):
        runtime_a, trader_a = self._connect(
            tmp_path, strategy_name="demo-a", authority_root=tmp_path / "auth"
        )
        runtime_b, trader_b = self._connect(
            tmp_path, strategy_name="demo-b", authority_root=tmp_path / "auth"
        )
        try:
            # Both runtimes resolve the SAME Authority file (canonical per
            # account) and open the SAME certified DB, without being told the
            # DB path independently.
            root = tmp_path / "auth"
            authority_files = list(root.glob("*.authority.json"))
            assert len(authority_files) == 1
            assert (
                runtime_a.session.coordinator.path
                == runtime_b.session.coordinator.path
            )
            out_a = runtime_a.submit(self._request("c-a", "demo-a"))
            assert out_a.state is TradeState.WORKING
            assert trader_a.place_calls == 1
            # Same-symbol exclusivity holds through the shared certified DB.
            dup = runtime_b.submit(self._request("c-dup", "demo-b"))
            assert dup.state is TradeState.REJECTED
            assert trader_b.place_calls == 0
            assert "symbol" in dup.reason.lower()
        finally:
            runtime_a.close()
            runtime_b.close()

    def test_different_accounts_resolve_different_domains(self, tmp_path):
        # Distinct bindings but a SHARED authority root: each account gets its
        # own Authority file + dedicated DB.
        binding_a = tmp_path / "ba"
        binding_b = tmp_path / "bb"
        qmt = tmp_path / "userdata_mini"
        qmt.mkdir(parents=True, exist_ok=True)
        QmtAccountBinding.create(
            environment="simulation", account_type=2, account_id="A1",
            qmt_path=qmt,
        ).write(binding_a)
        QmtAccountBinding.create(
            environment="simulation", account_type=2, account_id="A2",
            qmt_path=qmt,
        ).write(binding_b)
        root = tmp_path / "auth"
        store = AccountRuntimeAuthority(root)

        def connect(binding_path, strategy, account_id):
            config = MiniQmtRuntimeConfig(
                environment="simulation", qmt_path=qmt, binding_path=binding_path,
                journal_path=tmp_path / f"j-{strategy}.json",
                lock_path=tmp_path / f"e-{strategy}.lock",
                strategy_name=strategy, query_delay_seconds=0,
                runtime_lock_mode="shared",
            )
            holder = {}

            def factory(path, session_id):
                trader = FakeTrader(path, session_id)
                trader.account_id = account_id
                holder["trader"] = trader
                return trader

            payload = json.loads(binding_path.read_text(encoding="utf-8"))
            store.resolve(
                account_key=account_key_from_binding_identity(
                    environment="simulation", account_type=2,
                    account_id_sha256=payload["account_id_sha256"],
                ),
                environment="simulation", account_type=2,
                account_id_sha256=payload["account_id_sha256"],
                coordination_db_path=None, bootstrap=True,
            )
            return MiniQmtRuntime.connect(
                config, guard=AllowGuard(), trader_factory=factory,
                stock_account_factory=StockAccount, xtconstant=XtConstant,
                callback_base=CallbackBase, authority=store,
            ), holder["trader"]

        runtime_a, _ = connect(binding_a, "demo-a", "A1")
        runtime_b, _ = connect(binding_b, "demo-b", "A2")
        try:
            authority_files = sorted(root.glob("*.authority.json"))
            assert len(authority_files) == 2
            payloads = [json.loads(p.read_text(encoding="utf-8")) for p in authority_files]
            assert payloads[0]["coordination_db_path"] != payloads[1]["coordination_db_path"]
            assert payloads[0]["coordination_db_uuid"] != payloads[1]["coordination_db_uuid"]
        finally:
            runtime_a.close()
            runtime_b.close()

    def test_runtime_fails_closed_on_db_recreated_at_same_path(self, tmp_path):
        runtime, _ = self._connect(tmp_path, strategy_name="demo")
        try:
            root = tmp_path / "authority"
            auth_file = next(root.glob("*.authority.json"))
            payload = json.loads(auth_file.read_text(encoding="utf-8"))
            db_path = Path(payload["coordination_db_path"])
        finally:
            runtime.close()
        # Recreate the DB at the same path without identity metadata.
        db_path.unlink()
        sqlite3.connect(str(db_path)).close()
        with pytest.raises(CoordinationIdentityError):
            self._connect(tmp_path, strategy_name="demo2")

    def test_normal_runtime_missing_authority_fails_closed_no_files(self, tmp_path):
        # P1-2: normal runtime NEVER bootstraps; a missing Authority fails
        # closed and creates no replacement files.
        with pytest.raises(RuntimeConfigurationError):
            self._connect(
                tmp_path, strategy_name="demo", bootstrap_authority=False,
            )
        root = tmp_path / "authority"
        assert not list(root.glob("*.authority.json"))
        assert not list(root.glob("*.coordination.db"))

    def test_deleting_authority_and_db_blocks_runtime_no_replacement(self, tmp_path):
        # P1-2 regression: after BOTH the established Authority and its DB are
        # deleted, a normal runtime restart must refuse to start and must not
        # auto-create a new empty domain.
        runtime, _ = self._connect(tmp_path, strategy_name="demo")
        try:
            root = tmp_path / "authority"
            auth_file = next(root.glob("*.authority.json"))
            payload = json.loads(auth_file.read_text(encoding="utf-8"))
            db_path = Path(payload["coordination_db_path"])
        finally:
            runtime.close()
        auth_file.unlink()
        db_path.unlink()
        with pytest.raises(RuntimeConfigurationError):
            self._connect(
                tmp_path, strategy_name="demo2", bootstrap_authority=False,
            )
        assert not list(root.glob("*.authority.json"))
        assert not list(root.glob("*.coordination.db"))

    def test_after_bootstrap_runtime_only_verifies_no_rewrite(self, tmp_path):
        # P1-2: once bootstrapped, ordinary runtime resolution only VERIFIES;
        # it never creates/replaces the Authority or the DB.
        root = tmp_path / "authority"
        runtime, _ = self._connect(tmp_path, strategy_name="demo")
        try:
            auth_file = next(root.glob("*.authority.json"))
            before = auth_file.read_text(encoding="utf-8")
            db_paths_before = set(root.glob("*.coordination.db"))
        finally:
            runtime.close()
        runtime2, _ = self._connect(tmp_path, strategy_name="demo2")
        try:
            auth_file = next(root.glob("*.authority.json"))
            assert auth_file.read_text(encoding="utf-8") == before
            assert set(root.glob("*.coordination.db")) == db_paths_before
        finally:
            runtime2.close()

    def test_runtime_config_schema_rejects_production_bypass_fields(self, tmp_path):
        # P1-1 + P1-3: production runtime configuration must not expose
        # authority_root or coordination_path as bypass routes.
        qmt_path = tmp_path / "userdata_mini"
        qmt_path.mkdir(parents=True, exist_ok=True)
        binding_path = tmp_path / "binding.json"
        QmtAccountBinding.create(
            environment="simulation", account_type=2, account_id="A123",
            qmt_path=qmt_path,
        ).write(binding_path)
        base = {
            "schema_version": 1,
            "environment": "simulation",
            "qmt_path": str(qmt_path),
            "binding_path": str(binding_path),
            "journal_path": str(tmp_path / "j.json"),
            "lock_path": str(tmp_path / "e.lock"),
            "strategy_name": "demo",
            "runtime_lock_mode": "shared",
        }
        for field in ("authority_root", "coordination_path"):
            with pytest.raises(RuntimeConfigurationError):
                payload = dict(base)
                payload[field] = str(tmp_path / "bypass")
                MiniQmtRuntimeConfig.from_json(_write_json(tmp_path, field, payload))

    def test_account_key_inconsistent_identity_tuple_rejected(self, tmp_path):
        # P2-1: account_key must equal the recomputed identity tuple.
        store = AccountRuntimeAuthority(tmp_path / "auth")
        with pytest.raises(RuntimeAuthorityError):
            store.resolve(
                account_key=_key(),
                environment="simulation", account_type=2,
                account_id_sha256="b" * 64, bootstrap=True,
            )
        assert not list((tmp_path / "auth").glob("*"))


def _write_json(tmp_path, name, payload) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDefaultAuthorityRoot:
    def test_default_root_is_deterministic_and_host_level(self):
        root = default_authority_root()
        assert root.is_absolute()
        assert root.name == "authority"
        assert default_authority_root() == root


class TestCanonicalRootNonOverridable:
    @pytest.mark.skipif(os.name != "nt", reason="Windows Known Folder API")
    def test_windows_localappdata_env_is_ignored(self, monkeypatch, tmp_path):
        baseline = default_authority_root()
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "fake-localappdata"))
        # Mutable process environment must not change the canonical root.
        assert default_authority_root() == baseline
        assert not str(default_authority_root()).startswith(
            str(tmp_path / "fake-localappdata")
        )

    @pytest.mark.skipif(os.name != "nt", reason="Windows Known Folder API")
    def test_windows_known_folder_failure_fails_closed(self, monkeypatch):
        from qmt_execution_core import authority as auth_mod

        def _boom():
            raise RuntimeAuthorityError("forced Known Folder failure")

        monkeypatch.setattr(auth_mod, "_windows_known_folder_local_appdata", _boom)
        with pytest.raises(RuntimeAuthorityError):
            default_authority_root()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX user database")
    def test_posix_user_db_failure_fails_closed(self, monkeypatch):
        from qmt_execution_core import authority as auth_mod

        def _boom():
            raise RuntimeAuthorityError("forced user-database failure")

        monkeypatch.setattr(auth_mod, "_posix_user_home", _boom)
        with pytest.raises(RuntimeAuthorityError):
            default_authority_root()


class TestBootstrapAuthorityCli:
    def test_help_has_no_authority_root_option(self, capsys):
        from qmt_execution_core import cli

        with pytest.raises(SystemExit) as exc:
            cli.main(["bootstrap-authority", "--help"])
        assert exc.value.code == 0
        assert "--authority-root" not in capsys.readouterr().out

    def test_bootstrap_cli_and_runtime_share_canonical_root(
        self, tmp_path, monkeypatch
    ):
        # P1: operator bootstrap and normal runtime MUST call the same
        # canonical resolver.  Monkeypatch both namespaces to one temp root
        # and prove bootstrap then runtime-verify land on the same Authority.
        from qmt_execution_core import cli as cli_mod
        from qmt_execution_core.miniqmt import runtime as runtime_mod

        canonical = tmp_path / "canonical-auth"
        monkeypatch.setattr(cli_mod, "default_authority_root", lambda: canonical)
        monkeypatch.setattr(runtime_mod, "default_authority_root", lambda: canonical)

        qmt = tmp_path / "qmt"
        qmt.mkdir(parents=True, exist_ok=True)
        binding_path = tmp_path / "binding.json"
        QmtAccountBinding.create(
            environment="simulation", account_type=2, account_id="A123",
            qmt_path=qmt,
        ).write(binding_path)

        cli_mod.main(["bootstrap-authority", "--binding", str(binding_path)])
        assert list(canonical.glob("*.authority.json"))

        config = MiniQmtRuntimeConfig(
            environment="simulation", qmt_path=qmt, binding_path=binding_path,
            journal_path=tmp_path / "j.json", lock_path=tmp_path / "e.lock",
            strategy_name="demo", query_delay_seconds=0,
            runtime_lock_mode="shared",
        )
        holder = {}

        def factory(path, session_id):
            trader = FakeTrader(path, session_id)
            holder["trader"] = trader
            return trader

        runtime = MiniQmtRuntime.connect(
            config, guard=AllowGuard(), trader_factory=factory,
            stock_account_factory=StockAccount, xtconstant=XtConstant,
            callback_base=CallbackBase, cash_estimator=_zero_estimator(),
        )
        try:
            assert runtime.session.coordinator.expected_identity is not None
            assert holder["trader"].place_calls == 0  # verify-only, no side effect
        finally:
            runtime.close()

