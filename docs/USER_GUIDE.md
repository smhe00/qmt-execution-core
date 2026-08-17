# qmt-execution-core 用户指南

这是一份从 **“MiniQMT 已安装并登录”** 开始的完整使用说明。

如果你第一次接触 Core，从上到下照做即可；如果你已经跑通过连接验证，可以直接跳到 **第 8 节：策略如何调用 Core**。

> 默认只使用 **QMT 模拟环境**。本文不会引导你开启实盘，也不会自动下单。

---

## 0. 先确认你现在处于什么阶段

### 你只是使用一个已经集成 Core 的策略项目

例如某个项目已经替你实现了 `ExecutionGuard`、资金估算和策略逻辑。

你只需要完成本文 **第 1～7 节**，确认 Core 能正确连接你的模拟账户。之后回到该策略项目自己的使用说明。

### 你正在开发自己的交易策略

先完成 **第 1～7 节**，再继续阅读 **第 8～11 节**，了解策略程序真正需要调用的接口。

---

# 第一部分：第一次安装与连接

## 1. 前置条件

请先确认：

- Windows 已安装 MiniQMT / QMT；
- QMT 客户端已经打开，并登录 **模拟账户**；
- Python 版本 `>= 3.9`；
- 你知道自己的 QMT 账户 ID；
- 能找到当前 QMT 的 `userdata_mini` 目录。

### 1.1 找到 `userdata_mini`

它位于你的 QMT 安装目录中，例如：

```text
D:\某券商QMT模拟交易端\userdata_mini
```

后文中的 `qmt_path` 都填写这个目录，**不是 QMT 安装根目录**。

如果电脑上同时有模拟盘和实盘客户端，请确认这里使用的是 **模拟盘对应的 `userdata_mini`**。

### 1.2 确认当前 Python 能导入 `xtquant`

在 PowerShell / CMD 中运行：

```bash
python -c "import xtquant; print('xtquant OK')"
```

正常应看到：

```text
xtquant OK
```

如果这里已经报错，先不要继续配置 Core。你当前使用的 Python 环境还不能访问 MiniQMT 提供的 `xtquant`。

---

## 2. 获取并安装 Core

如果电脑已经安装 Git：

```bash
git clone https://github.com/smhe00/qmt-execution-core.git
cd qmt-execution-core
python -m pip install -e .
```

如果没有 Git，也可以在 GitHub 页面使用 **Code → Download ZIP**，解压后进入项目目录，再执行：

```bash
python -m pip install -e .
```

安装后运行：

```bash
qmt-execution-core verify
```

只有看到 release verification 为 PASS，才继续下一步。

---

## 3. 创建模拟账户绑定

先在项目目录创建两个文件夹：

```bash
mkdir config
mkdir runtime
```

然后运行下面这条 **单行命令**，只替换你的 QMT 路径：

```bash
qmt-execution-core create-binding --environment simulation --account-type 2 --qmt-path "D:\某券商QMT模拟交易端\userdata_mini" --output "config\sim-binding.local.json"
```

命令会提示你输入 MiniQMT 账户 ID。

Core 写入的是账户指纹，不会把明文账户 ID 保存到 binding 文件。

### `--account-type 2` 是什么？

当前验证过的普通证券账户使用 `2`。如果你的账户不是普通证券账户，或券商环境与此不同，**不要猜一个数字继续**；先确认对应的 QMT account type。

> `sim-binding.local.json` 属于本机配置，文件名使用 `.local.json` 是为了避免误提交到 Git。

---

## 4. 初始化账户 Runtime Authority

同一台电脑、同一个模拟账户第一次使用 shared runtime 时执行一次：

```bash
qmt-execution-core bootstrap-authority --binding "config\sim-binding.local.json"
```

成功后，这个账户会拥有自己的本地 coordination domain。

之后普通策略启动只会验证已有 Authority，不会自动创建另一份。

**不要**为了绕过启动错误去手工删除或替换 Runtime Authority / coordination DB。

---

## 5. 创建本地 Runtime 配置

在项目根目录新建：

```text
runtime_config.local.json
```

复制下面内容，只修改 `qmt_path`：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "D:/某券商QMT模拟交易端/userdata_mini",
  "binding_path": "config/sim-binding.local.json",
  "journal_path": "runtime/quickstart.journal.json",
  "lock_path": "runtime/quickstart.lock",
  "strategy_name": "quickstart",
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

这里有三条硬规则：

```text
environment = simulation
live_trading_enabled = false
不要添加 coordination_path / authority_root
```

shared mode 会根据账户身份找到已经初始化好的 Runtime Authority。

---

## 6. 跑第一次安全连接测试

确认模拟 QMT 客户端仍然打开并已登录，然后在项目根目录运行：

```bash
python examples/quickstart_connect.py
```

正常会看到类似：

```text
[PASS] Core runtime connected to MiniQMT simulation account
[PASS] execution_healthy = True
[PASS] Runtime closed cleanly
```

这个脚本会：

- 连接 MiniQMT；
- 验证账户 binding；
- 验证 Runtime Authority；
- 建立 Core runtime；
- 检查 runtime health；
- 正常关闭 runtime。

它**不会下单**。脚本内置的 Guard 会拒绝所有 execution request。

如果这里报错，先解决错误，不要跳过这一步去尝试交易。

---

## 7. 到这里是否算安装成功？

下面四项都通过即可：

```text
[PASS] qmt-execution-core verify
[PASS] simulation binding 已创建
[PASS] Runtime Authority 已初始化
[PASS] quickstart_connect.py 连接并正常关闭
```

此时已经证明基础链路成立：

```text
Python
  ↓
qmt-execution-core
  ↓
simulation binding
  ↓
Runtime Authority
  ↓
MiniQMT 模拟账户
```

### 常见错误

**`QMT userdata path does not exist`**  
`qmt_path` 填错了，确认它指向实际的 `...\userdata_mini`。

**account binding mismatch / expected account**  
当前 QMT 登录账户与创建 binding 时的账户不一致。

**`shared runtime requires an initialized account Runtime Authority`**  
重新检查第 4 步是否对同一个 binding 成功执行。

**`xtquant is not installed`**  
回到第 1.2 步。Core 不会从 PyPI 安装 `xtquant`，它来自本地 MiniQMT 环境。

---

# 第二部分：策略如何使用 Core

> 如果你只是使用 TGrid 或其他已经集成 Core 的项目，可以停在这里，回到项目自己的用户文档。下面内容主要给策略开发者。

## 8. 你真正需要理解的对象

普通策略只需要长期依赖下面这些接口：

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

Core 内部的 SQLite coordination、Runtime Authority、session-id 和 callback queue 不需要由策略直接操作。

---

## 9. 创建 Runtime

典型结构如下：

```python
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig.from_json("runtime_config.local.json")

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_cash_estimator,
)
```

这里策略必须提供两个安全接口：

### `ExecutionGuard`

负责回答：**这一笔交易现在是否允许执行？**

通常包含账户、环境、持仓、价格、数据新鲜度和策略风险条件。

### `CashRequirementEstimator`

BUY 前计算保守资金需求，例如：

```text
订单金额
+ 手续费
+ 临时冻结/扣款缓冲
+ 其他安全缓冲
```

A 股、港股、港股通等市场规则由上层项目实现，Core 不写死。

> 当前 Core 0.4.1 不提供“无策略判断、零配置即可下单”的默认 Guard。连接成功并不等于允许交易。

---

## 10. 提交一笔 execution

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

注意：

- `client_order_id` 必须唯一；
- `strategy_id` 必须与 Runtime 的 `strategy_name` 一致；
- 不要重复使用历史订单 ID；
- 不要绕过 Core 直接调用 XtQuant `order_stock()` / `cancel_order_stock()`。

---

## 11. 订单生命周期：只记住 6 个方法

```python
runtime.submit(request)             # 提交新 execution
runtime.poll()                      # 查询/推进当前 execution
runtime.cancel()                    # 请求撤销当前 execution
runtime.next_cycle()                # 当前 execution 明确结束后进入下一周期
runtime.recover_after_disconnect()  # QMT 断线后的恢复与对账
runtime.close()                     # 关闭 runtime
```

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

最重要的状态处理规则：

| 状态 | 用户应该怎么做 |
|---|---|
| `WORKING / PARTIALLY_FILLED` | 继续 `poll()`，或按策略决定 `cancel()` |
| `FILLED / CANCELLED / REJECTED` | lifecycle 已明确结束，可以 `next_cycle()` |
| `UNKNOWN / CANCEL_REJECTED / FAILED` | **不要重新 submit**；先通过 Core 恢复/对账 |

一个最小的状态处理骨架：

```python
snapshot = runtime.submit(request)
snapshot = runtime.poll()

if snapshot.state.value in {"working", "partially_filled"}:
    pass  # 继续 poll，或决定 cancel
elif snapshot.state.value in {"filled", "cancelled", "rejected"}:
    runtime.next_cycle()
elif snapshot.state.value in {"unknown", "cancel_rejected", "failed"}:
    pass  # 不重新 submit，先恢复 / reconcile
```

程序结束时：

```python
runtime.close()
```

---

# 第三部分：边界与下一步

## 实盘不是 Quick Start 的一部分

模拟盘和实盘必须使用不同的 binding / Runtime Authority domain。

Core 虽然提供：

```python
runtime.confirm_live(token)
```

但它只确认 live gate，不能绕过账户验证、`ExecutionGuard`、资金 reservation 或其他安全检查。

**不要从本文的模拟示例直接修改成实盘配置。** 实盘应由具体项目经过单独验证和授权后启用。

## 普通策略不要直接使用这些接口

```text
SQLiteExecutionCoordinator
AccountRuntimeAuthority
SessionIdLease
SerialEventQueue
CoordinationDbIdentity
```

这些属于高级集成或 Core 内部实现。

## 需要更深入的信息

- 根目录 [`README.md`](../README.md)：Core 的完整能力和设计背景；
- `docs/` 中的 architecture / state machine / formal / runtime 文档：开发和审计使用。

---

## 一句话使用路径

```text
刚装好 QMT
→ verify
→ simulation binding
→ bootstrap Authority
→ quickstart_connect.py
→ 已集成项目：回项目文档
→ 自研策略：实现 Guard + CashEstimator
→ submit / poll / cancel / next_cycle
```
