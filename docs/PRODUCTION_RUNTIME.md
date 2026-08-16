# MiniQMT Production Runtime / 生产运行时

`MiniQmtRuntime` 是 Generic Execution Core 与 MiniQMT/XtQuant 之间的生产形态适配层。0.4 支持两种运行模式：默认 `exclusive` 与显式 `shared`。

## 1. 基本生命周期

```text
load strict runtime config
→ verify QMT userdata path
→ load fingerprint-only account binding
→ initialize callback EventQueue
→ construct/register XtQuant callback
→ acquire runtime/session safety resources
→ trader.start()
→ trader.connect() == exact int 0
→ exact account info/status verification
→ subscribe(account) == exact int 0
→ construct/open ExecutionSession
→ verify journal state-machine/source binding
→ restart reconciliation if needed
→ mark recovery complete
```

实盘额外要求：

```text
live_trading_enabled == true
AND
runtime.confirm_live(token)
```

明文 confirmation token 不持久化，只保存 SHA-256 digest。

---

## 2. Account Binding

binding 文件不保存明文账户号和明文 QMT path：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "account_type": 2,
  "account_id_sha256": "...",
  "qmt_path_sha256": "..."
}
```

运行时必须找到**唯一匹配且健康**的证券账户，否则 fail closed。

`account_key` 用于 0.4 shared coordination，由：

```text
environment + account_type + account_id_sha256
```

稳定派生，不需要共享 DB 保存明文账户号。

---

## 3. Runtime Config

默认兼容配置：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "C:/.../userdata_mini",
  "binding_path": "account-binding.local.json",
  "journal_path": "../runtime/demo-journal.json",
  "lock_path": "../runtime/demo-execution.lock",
  "strategy_name": "demo",
  "live_trading_enabled": false,
  "runtime_lock_mode": "exclusive"
}
```

shared 模式：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "C:/.../userdata_mini",
  "binding_path": "account-binding.local.json",
  "journal_path": "../runtime/demo-journal.json",
  "lock_path": "../runtime/demo-execution.lock",
  "strategy_name": "demo",
  "runtime_lock_mode": "shared",
  "coordination_path": "../runtime/qec_coord_sim.db",
  "session_id_pool_start": 100000000,
  "session_id_pool_size": 1000,
  "session_id_attempts": 32
}
```

JSON 相对路径按配置文件所在目录解析。

---

## 4. exclusive 模式

```python
runtime_lock_mode="exclusive"
```

这是默认值，保持 0.3 行为：

```text
qmt_path fingerprint
      |
      v
qmt-path-wide ExecutionMutex
```

同一个 QMT userdata path 同时只有一个 runtime。

适合：

- 单策略；
- 尚未启用共享协调的旧项目；
- 最保守的迁移阶段。

---

## 5. shared 模式

```python
runtime_lock_mode="shared"
```

shared 模式**不再获取 qmt-path-wide exclusive runtime mutex**，但并不是无锁运行。

安全边界改为：

```text
per-session execution mutex
+ bounded MiniQMT session-id lease
+ SQLite (account_key, symbol) claim
+ atomic shared BUY cash reservation
```

shared mode 必须提供：

```text
coordination_path
```

或显式注入 `ExecutionCoordinator`，否则 runtime 构造 fail closed。

同账户不同标的可以并发；同账户同标的在 broker side effect 前被阻止。

---

## 6. Session ID 管理

shared 模式使用 `BoundedSessionIdAllocator`。

规则：

- caller supplied exact `session_id` 继续支持；
- 自动模式使用有限 pool；
- preferred candidate 从稳定 key 派生；
- ID 已被 lease 时有限 fallback；
- trader connect 失败时有限 fallback；
- 不进行无限随机 retry；
- session ID 使用 OS file-lock lease；
- process crash 后 lease 随 OS lock 释放。

MiniQMT session id 不被假设为 broker-issued lease，也不存在虚构的 `release_session_id()` API。

---

## 7. Coordinated BUY

shared runtime 通常配合 `CashRequirementEstimator`：

```python
runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_estimator,
)
```

每笔 BUY：

```text
Core durable intent
→ fresh broker query_asset()
→ conservative cash estimate
→ SQLite atomic claim/reservation
→ project sidecar
→ broker order_stock()
```

缺少 estimator、fresh account facts 失败、symbol conflict 或 cash 不足均在 broker side effect 前 fail closed。

Core 不维护 settlement-pending 本地现金账。execution RESOLVED 后释放本地 reservation；下一笔 BUY 必须重新查询 broker available cash。

---

## 8. Project Integration

Project 继续提供 `ExecutionGuard`：

```python
class ProjectGuard:
    def verify_session(self) -> SessionEvidence:
        ...

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        ...
```

项目层负责：

- trading-day / time-window；
- fresh quote；
- position / can_use；
- strategy risk budget；
- strategy-specific business invariants。

共享 account cash 的跨进程互斥由 0.4 coordinator 负责，不应由多个 strategy 各自独立猜测。

---

## 9. Callback 与断线恢复

QMT callback 只产生 immutable observation，并进入 bounded `SerialEventQueue`。

断线会立即：

```text
transport_connected = false
account_healthy = false
live confirmation revoked
broker execution disabled
```

恢复流程：

```text
connect exact success
→ bound account health verification
→ subscribe exact success
→ durable execution reconciliation
→ project session evidence re-verification
→ mark recovery complete
→ live mode fresh confirmation token
```

transport reconnect 本身永远不足以恢复新订单权限。

---

## 10. 安全边界

Runtime 不会：

- 自动选择未知账户；
- 把 query `None` 当空列表；
- blind resend UNKNOWN；
- 让 callback 直接 submit/cancel；
- 在 shared mode 缺少 coordinator 时继续运行；
- 在 coordinated BUY 缺少 estimator 时按 notional-only 静默降级；
- 持久化明文实盘 confirmation token。

当前推荐模型：

> **少量策略采用“多进程 + 每进程一个同步 ExecutionSession + SQLite 共享账户资源协调”，不需要中央 execution daemon。**
