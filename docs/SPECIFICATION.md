# qmt-execution-core 0.4.1 当前规格

> **Status: CURRENT CANONICAL SPECIFICATION**  
> Version: **0.4.1**  
> 本文汇总 0.4 frozen resource-coordination spec 与 0.4.1 Runtime Authority 安全增量。历史冻结文本、审计和实现证据位于 [`archive/`](archive/)；发生冲突时，以当前代码、测试、`AGENTS.md` 和本文为当前维护基线。

## 1. 产品定位

`qmt-execution-core` 是 broker-neutral、strategy-neutral 的可靠执行层。

```text
Strategy
   |
   v
ExecutionSession / CoordinatedExecutionSession
   |
   v
BrokerPort
   |
   +-- MiniQmtBrokerAdapter
   +-- FakeBroker
   +-- future adapter
```

Generic Core 不直接 import `xtquant`；MiniQMT 依赖仅存在于适配层并采用 lazy import / dependency injection。

Core 不拥有：

- 策略信号、择时、仓位目标；
- TGrid / ETF / repo 等业务语义；
- Portfolio Manager / central OMS；
- 跨机器 distributed coordination；
- 策略专属 T+1 / settlement；
- `order_stock_async()` execution path。

## 2. 可靠执行原则

1. **Durable before side effect**：broker submit/cancel 前先持久化足够的恢复证据。
2. **Fail closed**：无法证明安全时，不产生新的 broker side effect。
3. **Broker is authoritative**：订单存在、成交和最终撤单以权威 broker query/reconciliation 为准。
4. **No blind resend**：`UNKNOWN`、网络异常、进程异常不产生自动重发权限。
5. **Concurrency by independence**：只同步真正冲突的账户资源，不用全局锁阻塞无关标的。

## 3. 状态机

主要 `TradeState`：

```text
IDLE
WAIT_TRIGGER
TRIGGER
PRE_CHECK
SUBMITTED
ACCEPTED
WORKING
UNKNOWN
PARTIALLY_FILLED
PENDING_CANCEL
CANCELLING
CANCEL_REJECTED
FILLED
CANCELLED
REJECTED
FAILED
```

单个 `ExecutionSession` 始终保持 one-active-execution-at-a-time；并发来自多个独立 session/process。

正常路径：

```text
IDLE
 -> WAIT_TRIGGER
 -> TRIGGER
 -> PRE_CHECK
 -> SUBMITTED
 -> ACCEPTED
 -> WORKING / PARTIALLY_FILLED
 -> FILLED / CANCELLED / REJECTED
```

### Pre-broker 结果

`PRE_BROKER_REJECTED`：

- broker 未调用；
- `submitted_once = false`；
- `unresolved_order = false`；
- 用于同标 claim 冲突、资金不足、缺 estimator 等正常 fail-closed 拒绝。

`PRE_BROKER_ABORTED`：

- 同步 pre-broker hook 失败；
- broker side effect 被证明未发生；
- 可表现为 `FAILED`，但 `ExecutionFinality = RESOLVED`。

### UNKNOWN

ambiguous submit/query 进入 `UNKNOWN`。它只能：

```text
UNKNOWN -> authoritative query/reconcile -> resolved/active state
```

若恢复失败并仍无法证明 broker reality：

```text
UNKNOWN -> FAILED + unresolved_order=True
```

此时 finality 为 `QUARANTINED`，不得释放同标 claim。

### Cancel

```text
WORKING/PARTIALLY_FILLED
 -> PENDING_CANCEL
 -> CANCELLING
 -> authoritative query
```

cancel API success 只表示请求已发送；撤单过程中如果 broker 报告全部成交，最终必须收敛为 `FILLED`。

## 4. Execution Finality

资源释放不能只看 `TradeState`：

```text
OPEN
RESOLVED
QUARANTINED
```

典型映射：

```text
WORKING / PARTIALLY_FILLED / UNKNOWN / CANCEL_REJECTED -> OPEN
FILLED / CANCELLED / definitive REJECTED               -> RESOLVED
FAILED + unresolved_order=True                          -> QUARANTINED
FAILED + broker submit proven absent                    -> RESOLVED
```

只有 `RESOLVED` 才允许释放共享 symbol/cash resource。

## 5. 跨进程协调

### 同账户同标的

核心不变量：

```text
At most one unresolved execution
for the same (account_key, symbol)
across processes.
```

第二个 execution 必须在 broker side effect 前 fail closed。

### 同账户不同标的

允许独立并发：

```text
Account A / 0700.HK   -> WORKING
Account A / 510300.SH -> CANCELLING
```

### Shared BUY cash

每笔 coordinated BUY：

```text
fresh broker available cash
- other active same-account reservations
>= conservative required cash
```

read/check/reserve 必须在原子跨进程事务中完成。

Core 不维护本地 `SETTLEMENT_PENDING` 现金账。释放 reservation 不会本地“加回现金”；下一笔 BUY 必须重新查询 broker authoritative available cash。

## 6. 0.4.1 Account Runtime Authority

0.4.0 的显式 `coordination_path` 不能作为生产 shared runtime 的安全来源。0.4.1 把“同一账户只能有一个 coordination domain”提升为运行时可验证不变量。

目标模型：

```text
Authoritative account identity
        |
        v
stable account_key
        |
        v
Unique Account Runtime Authority
        |
        +-- canonical coordination DB path
        +-- coordination DB UUID
        +-- authority ID
        |
        v
Dedicated Coordination DB
```

### AUTH-001

对同一 authoritative account，在支持的 host/user coordination domain 中，production shared runtime 只能解析到一个 canonical Authority。

策略不得自行选择 Authority 文件名、`authority_root` 或生产 coordination DB 路径。

### AUTH-002

shared execution 只有在以下全部匹配时才允许：

```text
runtime actual account_key
== authority.account_key
== DB metadata.account_key

canonical(opened DB path)
== authority.coordination_db_path

authority.coordination_db_uuid
== DB metadata.db_uuid

authority.authority_id
== DB metadata.authority_id
```

任一不一致均 fail closed。

### Bootstrap

Authority/DB 第一次建立必须通过显式 bootstrap，在 account-specific authority lock 下原子完成。普通策略启动：

- 只验证既有 Authority；
- 不静默新建第二个 domain；
- 不因 Authority/DB mismatch 自动“修复”；
- 不在同路径 DB 被替换后忽略 `db_uuid` mismatch。

推荐生产部署：**每账户一个 dedicated coordination DB**。

## 7. Submit 固定顺序

协调模式：

```text
Guard / PRE_CHECK
      |
      v
Core Durable Intent COMMIT
      |
      v
Shared Coordination
  - claim(account_key, symbol)
  - fresh broker cash
  - conservative BUY reservation
      |
      v
Project before_broker_submit sidecar
      |
      v
BrokerPort.place_order()
```

任何前置阶段失败都不得触发 broker submit。

## 8. MiniQMT adapter 语义

Generic Core 不解释 raw XtQuant constants；`MiniQmtBrokerAdapter` 归一化为 broker-neutral status。

关键映射：

| QMT raw | Value | Core |
|---|---:|---|
| `ORDER_UNREPORTED` | 48 | `ACCEPTED` |
| `ORDER_WAIT_REPORTING` | 49 | `ACCEPTED` |
| `ORDER_REPORTED` | 50 | `WORKING` |
| `ORDER_REPORTED_CANCEL` | 51 | `CANCEL_PENDING` |
| `ORDER_PARTSUCC_CANCEL` | 52 | `CANCEL_PENDING` |
| `ORDER_PART_CANCEL` | 53 | `PARTIAL_CANCELLED` |
| `ORDER_CANCELED` | 54 | `CANCELLED` |
| `ORDER_PART_SUCC` | 55 | `PARTIALLY_FILLED` |
| `ORDER_SUCCEEDED` | 56 | `FILLED` |
| `ORDER_JUNK` | 57 | `REJECTED` |
| `ORDER_UNKNOWN` | 255 | `UNKNOWN` |
| 未识别/矛盾 payload | other | `UNKNOWN` |

同步 submit：

```text
positive plain-int broker order id -> accepted for authoritative poll
definitive rejection              -> REJECTED
exception/malformed/ambiguous      -> UNKNOWN
```

strict query 的 `None`、exception、非唯一 recovery match 均视为 ambiguity，不得解释为“没有订单”。

## 9. Callback / disconnect

broker callback 只能产生 immutable observation 并进入 bounded serial queue；callback 不拥有 submit/cancel/journal/strategy mutation 权限。

断线立即使 execution health 失效，并撤销 live confirmation。恢复必须完成：

```text
transport reconnect
-> exact bound account verification
-> subscribe
-> durable reconciliation
-> project session evidence re-verification
-> runtime recovery complete
-> live mode fresh confirmation
```

仅 transport reconnect 不足以恢复新订单权限。

## 10. Runtime mode

`exclusive`：

- 默认兼容模式；
- qmt-path-wide runtime mutex；
- 适合单策略或保守迁移。

`shared`：

- 不使用 qmt-path-wide exclusive mutex；
- 必须通过 Runtime Authority；
- 使用 per-session mutex、bounded session-id lease、durable symbol claim 和 atomic cash reservation；
- 不允许 fallback 到未协调运行。

## 11. Source integrity / verification

状态机和受保护执行源码通过 verifier / source hash 绑定。不能为了打开旧 journal 而绕过 hash mismatch。

修改执行语义至少运行：

```bash
python -m pytest
python -m compileall -q src tests
PYTHONPATH=src python -c "from qmt_execution_core import verify_state_machine; print(verify_state_machine())"
```

mutex、coordination、session-lease 变化还必须覆盖 Windows safety probes。

## 12. 历史规格

原始冻结规格和实现证据保留在：

- [`archive/v0.4/`](archive/v0.4/)
- [`archive/v0.4.1/`](archive/v0.4.1/)

它们用于审计和追溯，不再作为当前 onboarding 或 API 入口。
