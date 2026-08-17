# qmt-execution-core 用户接口

本文只说明**策略使用者真正需要调用的接口**。内部状态机、SQLite coordination、Runtime Authority、session-id 等实现细节不在这里展开。

## 1. 第一次使用

### 1.1 检查安装

```bash
qmt-execution-core verify
```

要求返回 release verification PASS。

### 1.2 创建账户绑定

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path <MiniQMT userdata_mini> \
  --output binding.json
```

`binding.json` 保存账户指纹，不保存明文账户 ID。

### 1.3 初始化账户 Runtime Authority

shared runtime 第一次使用该账户时执行一次：

```bash
qmt-execution-core bootstrap-authority --binding binding.json
```

以后普通策略启动只验证已有 Authority，不自动重建。

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

推荐多策略/同账户场景使用 `runtime_lock_mode="shared"`。

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

## 4. 订单生命周期

普通策略主要只需要下面 6 个接口：

```python
runtime.submit(request)             # 提交一笔新 execution
runtime.poll()                      # 查询/推进当前 execution
runtime.cancel()                    # 请求撤销当前 execution
runtime.next_cycle()                # 当前 execution 权威结束后进入下一周期
runtime.recover_after_disconnect()  # QMT 断线后的恢复与 reconciliation
runtime.close()                     # 关闭 runtime 并释放资源
```

典型流程：

```python
snapshot = runtime.submit(request)

while snapshot.state.value not in {"filled", "cancelled", "rejected"}:
    snapshot = runtime.poll()

runtime.next_cycle()
runtime.close()
```

不要直接调用 XtQuant 的 `order_stock()` / `cancel_order_stock()` 绕过 Core。

---

## 5. 返回结果

所有执行接口主要返回 `ExecutionSnapshot`：

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

`UNKNOWN`、`CANCEL_REJECTED` 或其他 broker reality 不明确状态，**不要重新 submit 同一交易**；继续通过 Core reconciliation/recovery 处理。

---

## 6. 策略需要提供的两个接口

Core 不决定策略逻辑，因此调用方通常需要提供：

### `ExecutionGuard`

负责判断这笔交易是否允许执行，例如：账户、环境、持仓、价格、策略风险条件等。

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

`confirm_live()` 只确认 live gate；它不会绕过 `ExecutionGuard`、账户验证、Runtime Authority、资金 reservation 或其他 Core 安全检查。

---

## 8. 用户应该依赖的稳定接口

策略代码优先只依赖：

```text
MiniQmtRuntime
MiniQmtRuntimeConfig
ExecutionRequest
ExecutionSnapshot
Side
TradeState
ExecutionFinality
ExecutionGuard
CashRequirementEstimator
```

`SQLiteExecutionCoordinator`、`AccountRuntimeAuthority`、`SessionIdLease`、`SerialEventQueue` 等属于高级/内部接口，普通策略不要直接使用。

更详细的架构和内部机制请阅读根目录 `README.md` 和 `docs/` 下的设计文档。
