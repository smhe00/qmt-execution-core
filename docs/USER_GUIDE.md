# qmt-execution-core 用户指南

本文是**策略项目和 coding agent 的首选接入文档**。内部状态机、SQLite schema、Runtime Authority 实现细节不在这里展开；需要修改 Core 本身时再读 [SPECIFICATION.md](SPECIFICATION.md) 和根目录 [AGENTS.md](../AGENTS.md)。

## 1. 使用前提

- Windows 上已安装 MiniQMT / QMT；
- 优先使用已登录的**模拟账户**完成首次接入；
- Python `>=3.9`；
- 已确认 MiniQMT 的 `userdata_mini` 路径。

安装：

```bash
git clone https://github.com/smhe00/qmt-execution-core.git
cd qmt-execution-core
python -m pip install -e .
qmt-execution-core verify
```

只有 release verification PASS 后再继续。

## 2. 创建账户绑定

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path "D:\...\userdata_mini" \
  --output "config\sim-binding.json"
```

binding 只保存账户/路径指纹，不保存明文账户号。

普通证券账户当前已验证的 `account-type` 为 `2`；不同券商/账户类型不要猜测。

## 3. 初始化 Runtime Authority

shared runtime 第一次使用某账户时显式执行：

```bash
qmt-execution-core bootstrap-authority \
  --binding "config\sim-binding.json"
```

0.4.1 的生产模型是：

```text
authoritative account
    -> stable account_key
    -> unique Runtime Authority
    -> Authority-certified coordination DB
```

策略项目**不要自行配置** `coordination_path` 或 `authority_root`。普通启动只验证既有 Authority；Authority 缺失、损坏或 DB 身份不匹配时必须 fail closed。

## 4. Runtime 配置

示例：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "D:/.../userdata_mini",
  "binding_path": "config/sim-binding.json",
  "journal_path": "runtime/my_strategy.journal.json",
  "lock_path": "runtime/my_strategy.lock",
  "strategy_name": "my_strategy",
  "live_trading_enabled": false,
  "confirmation_token_sha256": "",
  "session_id": null,
  "query_attempts": 3,
  "query_delay_seconds": 0.15,
  "event_queue_size": 1024,
  "runtime_lock_mode": "shared",
  "session_id_pool_start": 100000000,
  "session_id_pool_size": 1000,
  "session_id_attempts": 32
}
```

首次接入保持：

```text
environment = simulation
live_trading_enabled = false
```

## 5. 创建 Runtime

```python
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig(
    environment="simulation",
    qmt_path="D:/.../userdata_mini",
    binding_path="config/sim-binding.json",
    journal_path="runtime/my_strategy.journal.json",
    lock_path="runtime/my_strategy.lock",
    strategy_name="my_strategy",
    runtime_lock_mode="shared",
)

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_cash_estimator,
)
```

多策略共享同一账户时推荐 `shared`。单策略或保守迁移可使用默认 `exclusive`。

## 6. 构造 ExecutionRequest

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
```

`client_order_id` / `order_remark` 必须遵守幂等规则，不得重复复用历史 logical order identity。

## 7. 策略只依赖六个运行时动作

```python
snapshot = runtime.submit(request)
snapshot = runtime.poll()
snapshot = runtime.cancel()
runtime.next_cycle()
runtime.recover_after_disconnect()
runtime.close()
```

典型分支：

```python
snapshot = runtime.submit(request)

if snapshot.state.value in {"working", "partially_filled"}:
    # 继续 poll，或由策略决定 cancel
    pass
elif snapshot.state.value in {"filled", "cancelled", "rejected"}:
    runtime.next_cycle()
elif snapshot.state.value in {"unknown", "cancel_rejected", "failed"}:
    # 禁止重新 submit；先通过 Core 恢复/对账
    pass
```

不要绕过 Core 直接调用 `order_stock()` / `cancel_order_stock()`。

## 8. 策略必须提供的边界

### ExecutionGuard

策略负责验证其业务事实，例如：

- 交易日/时间窗口；
- fresh quote；
- position / can_use；
- strategy risk budget；
- 账户/环境符合项目规则。

### CashRequirementEstimator

BUY 前提供**保守资金需求**，例如：

```text
订单金额
+ 交易费用
+ 临时冻结/扣款缓冲
+ FX/rounding buffer
+ safety buffer
```

A 股、港股、港股通等具体费用规则属于上层策略/项目，不写死在 Core。

## 9. 返回结果

主要读取 `ExecutionSnapshot`：

```python
snapshot.state
snapshot.client_order_id
snapshot.broker_order_id
snapshot.ordered_qty
snapshot.filled_qty
snapshot.average_fill_price
snapshot.reason
```

关键状态：

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

必须遵守：

- `UNKNOWN` 不是“没有订单”；
- `FAILED` 不自动等价于资源可释放；
- cancel API 成功只表示撤单请求已发出；
- broker authoritative reality 决定最终成交/撤单事实。

## 10. 第一次接入的安全验收

至少完成：

```text
[PASS] qmt-execution-core verify
[PASS] simulation binding created
[PASS] Runtime Authority initialized
[PASS] examples/quickstart_connect.py connected and closed cleanly
```

`examples/quickstart_connect.py` 只做安全连接验证，内置 Guard 拒绝 execution request，不会自动下单。

随后可参考：

```text
examples/project_integration.py
examples/runtime_config.example.json
```

把策略信号、仓位和风控接到 `ExecutionRequest + ExecutionGuard + CashRequirementEstimator` 上。

## 11. 实盘边界

实盘必须同时满足配置 gate 和运行时确认，例如：

```python
runtime.confirm_live(token)
```

`confirm_live()` 不能绕过：

- account binding；
- account type/status；
- Runtime Authority；
- `ExecutionGuard`；
- cash reservation；
- disconnect/recovery safety。

任何不确定条件都应保持 fail closed。
