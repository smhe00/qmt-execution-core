# Changelog

## 0.2.0

- Added production-shaped `MiniQmtRuntime`.
- Added fingerprint-only account/QMT path binding.
- Added exact account type/status selection and reconnect verification.
- Added bounded serial callback event queue and fail-closed health propagation.
- Added live double gate: trusted config enable + runtime-only confirmation token.
- Added disconnect recovery: reconnect → account verify → subscribe → reconcile → reconfirm.
- Added MiniQMT asset/position/trade DTO queries.
- Added cross-cycle durable idempotency for client order ids and order remarks.
- Added common allowlist/qty/notional guard wrapper and kill switch.
- Added CLI for formal verification, account binding creation and token hashing.

## 0.1.0

- Initial generic execution state machine, journal, mutex, recovery, MiniQMT status normalization and explicit-state verifier.
