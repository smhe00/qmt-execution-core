"""Read-only MiniQMT account probe for the beginner User Guide.

This helper connects to the local QMT client, reads account discovery/status,
and reports the account_type for the account ID entered by the operator.
It never subscribes an account and never calls any order/cancel API.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
from pathlib import Path


def _mask(account_id: str) -> str:
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return f"{account_id[:2]}***{account_id[-2:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only MiniQMT account probe")
    parser.add_argument("--qmt-path", required=True, help="MiniQMT userdata_mini path")
    args = parser.parse_args()

    qmt_path = Path(args.qmt_path).expanduser().resolve(strict=False)
    if not qmt_path.is_dir():
        raise RuntimeError(f"QMT userdata path does not exist: {qmt_path}")

    try:
        from xtquant import xtconstant
        from xtquant.xttrader import XtQuantTrader
    except ImportError as exc:
        raise RuntimeError(
            "xtquant is not importable from this Python environment"
        ) from exc

    account_id = getpass.getpass("MiniQMT account id (input hidden): ").strip()
    if not account_id:
        raise RuntimeError("account id cannot be empty")

    security_account_type = int(getattr(xtconstant, "SECURITY_ACCOUNT"))
    account_status_ok = int(getattr(xtconstant, "ACCOUNT_STATUS_OK"))

    session_id = secrets.randbelow(900_000_000) + 100_000_000
    trader = XtQuantTrader(str(qmt_path), session_id)
    try:
        trader.start()
        result = trader.connect()
        if type(result) is not int or result != 0:
            raise RuntimeError(f"trader.connect failed: {result!r}")

        infos = trader.query_account_infos()
        statuses = trader.query_account_status()
        if not isinstance(infos, (list, tuple)):
            raise RuntimeError("query_account_infos returned unexpected data")
        if not isinstance(statuses, (list, tuple)):
            raise RuntimeError("query_account_status returned unexpected data")

        matches = []
        for info in infos:
            try:
                discovered_id = str(getattr(info, "account_id"))
                account_type = int(getattr(info, "account_type"))
            except (AttributeError, TypeError, ValueError):
                continue
            if discovered_id == account_id:
                matches.append(account_type)

        if len(matches) != 1:
            raise RuntimeError(
                "the entered account id was not found uniquely in QMT account discovery"
            )

        account_type = matches[0]
        healthy = False
        for status in statuses:
            try:
                sid = str(getattr(status, "account_id"))
                stype = int(getattr(status, "account_type"))
                svalue = int(getattr(status, "status"))
            except (AttributeError, TypeError, ValueError):
                continue
            if (
                sid == account_id
                and stype == account_type
                and svalue == account_status_ok
            ):
                healthy = True
                break

        security_account = account_type == security_account_type

        print(f"[PASS] account found: {_mask(account_id)}")
        print(f"[INFO] account_type = {account_type}")
        print(f"[INFO] security_account = {security_account}")
        print(f"[INFO] healthy = {healthy}")
        print("[SAFE] read-only probe complete; no subscribe/order/cancel was called")

        if not security_account:
            raise RuntimeError(
                "this account is not the securities account type expected by MiniQmtRuntime"
            )
        if not healthy:
            raise RuntimeError("the selected account is not reported healthy by QMT")
        return 0
    finally:
        try:
            trader.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
