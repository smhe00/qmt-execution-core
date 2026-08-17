# qmt-execution-core 用户接口

本文只说明**策略使用者需要调用的接口**。内部状态机、SQLite coordination、Runtime Authority、session-id 等实现细节不展开。

> 第一次使用 MiniQMT + Core？请先按 [`QUICK_START.md`](QUICK_START.md) 完成模拟账户连接验证，再阅读本文。

## 1. 第一次使用

### 检查安装

```bash
qmt-execution-core verify
```

要求 release verification PASS。

### 创建账户绑定

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path <MiniQMT userdata_mini> \
  --output binding.json
```

`binding.json` 保存账户指纹，不保存明文账户 ID。

### 初始化账户 Runtime Authority

shared runtime 第一次使用该账户时执行一次：

```bash
qmt-execution-core bootstrap-authority --binding binding.json
```

之后普通策略启动只验证已有 Authority，不自动重建。

---

## 2. 创建 Runtime

```python
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig(
    environment="simulation",
    qmt_path=".../userdata_mini",
    binding_path="binding.json",
    journal_path="runtime/order.journal.json",
    lock_path="runtime/order.lock",
    strategy_name="my_strategy",
    runtime_lock_mode="shared",
)

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_cash_estimator,
)
```

多策略共用同一账户时推荐 `runtime_lock_mode="shared"`。

用户**不要配置或自行选择** `coordination_path`、`authority_root`。

---

## 3. 提交订单

```python
from qmt_execution_core import ExecutionRequest, Side

request = ExecutionRequest(
    client_order_id="my_strategy-20260817-001",
    symbol="510300.SH",
    side=Side.BUY,
    qty=100,
    limit_price=4.70,
    strategy_id="my_strategy",
    order_remark="MY-001",
)

snapshot = runtime.submit(request)
```

`client_order_id` 必须唯一，不要重复使用历史订单 ID。

---

## 4. 运行时只需要 6 个方法

```python
runtime.submit(request)             # 提交一笔新 execution
runtime.poll()                      # 查询/推进当前 execution
runtime.cancel()                    # 请求撤销当前 execution
runtime.next_cycle()                # 当前 execution 明确结束后进入下一周期
runtime.recover_after_disconnect()  # QMT 断线后的恢复与 reconciliation
runtime.close()                     # 关闭 runtime 并释放资源
```

典型使用方式：

```python
snapshot = runtime.submit(request)
snapshot = runtime.poll()

if snapshot.state.value in {"working", "partially_filled"}:
    # 继续 poll，或根据策略决定 cancel
    pass
elif snapshot.state.value in {"filled", "cancelled", "rejected"}:
    runtime.next_cycle()
elif snapshot.state.value in {"unknown", "cancel_rejected", "failed"}:
    # 不要重新 submit；先通过 Core 恢复/对账
    pass
```

不要直接调用 XtQuant 的 `order_stock()` / `cancel_order_stock()` 绕过 Core。

---

## 5. 返回结果

执行接口主要返回 `ExecutionSnapshot`：

```python
snapshot.state
snapshot.client_order_id
snapshot.broker_order_id
snapshot.ordered_qty
snapshot.filled_qty
snapshot.average_fill_price
snapshot.reason
```

常见状态：

```text
WORKING
PARTIALLY_FILLED
FILLED
CANCELLED
REJECTED
UNKNOWN
CANCEL_REJECTED
FAILED
```

其中：

- `FILLED / CANCELLED / REJECTED`：该订单生命周期已明确结束；
- `WORKING / PARTIALLY_FILLED`：订单仍在进行；
- `UNKNOWN / CANCEL_REJECTED / FAILED`：**不要重新提交同一交易**，先让 Core 恢复/对账。

---

## 6. 策略需要提供两个接口

### `ExecutionGuard`

判断这笔交易是否允许执行，例如账户、环境、持仓、价格、策略风险条件。

### `CashRequirementEstimator`

BUY 前计算保守资金需求，例如：

```text
订单金额 + 手续费 + 临时冻结/扣款缓冲 + 其他安全缓冲
```

A 股、港股、港股通等费用规则由上层策略/项目提供，Core 不写死。

---

## 7. 实盘

模拟盘和实盘必须使用不同的 binding / Runtime Authority domain。

实盘还需要显式配置 live gate，并调用：

```python
runtime.confirm_live(token)
```

`confirm_live()` 只确认 live gate，不会绕过账户验证、`ExecutionGuard`、资金 reservation 或其他安全检查。

---

## 8. 普通策略应依赖的接口

```text
MiniQmtRuntime
MiniQmtRuntimeConfig
ExecutionRequest
ExecutionSnapshot
Side
TradeState
ExecutionGuard
CashRequirementEstimator
```

以下属于高级/内部接口，普通策略不要直接使用：

```text
SQLiteExecutionCoordinator
AccountRuntimeAuthority
SessionIdLease
SerialEventQueue
CoordinationDbIdentity
```

更多内部设计请阅读根目录 `README.md` 和 `docs/` 下的开发文档。
