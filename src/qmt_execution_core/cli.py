from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from .miniqmt.binding import QmtAccountBinding
from .miniqmt.runtime_gate import token_sha256
from .verifier import verify_release_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qmt-execution-core")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "verify",
        help=(
            "run single-process state-machine proof, implementation refinement, "
            "three-process coordination proof, and source-manifest verification"
        ),
    )

    binding = sub.add_parser(
        "create-binding",
        help="create a fingerprint-only MiniQMT account binding",
    )
    binding.add_argument("--environment", choices=("simulation", "live"), required=True)
    binding.add_argument("--account-type", type=int, required=True)
    binding.add_argument("--account-id", default="")
    binding.add_argument("--qmt-path", required=True)
    binding.add_argument("--output", required=True)

    token = sub.add_parser(
        "hash-token",
        help="hash a runtime confirmation token without storing plaintext",
    )
    token.add_argument("--token", default="")

    args = parser.parse_args(argv)
    if args.command == "verify":
        print(json.dumps(verify_release_model(), indent=2, sort_keys=True))
        return 0

    if args.command == "create-binding":
        account_id = args.account_id or getpass.getpass("MiniQMT account id: ")
        result = QmtAccountBinding.create(
            environment=args.environment,
            account_type=args.account_type,
            account_id=account_id,
            qmt_path=Path(args.qmt_path),
        )
        result.write(Path(args.output))
        print(f"binding written: {args.output}")
        return 0

    if args.command == "hash-token":
        token_value = args.token or getpass.getpass("Runtime confirmation token: ")
        print(token_sha256(token_value))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
