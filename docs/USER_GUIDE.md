# qmt-execution-core 用户指南

这份文档是 **qmt-execution-core 的用户与策略集成入口**。

它从“MiniQMT 已安装并登录”开始，覆盖两件事：

1. 第一次把 Core 安全地连到 QMT 模拟账户；
2. 跑通以后，策略程序应该怎样调用 Core。

如果你只是使用一个**已经集成 Core 的策略项目**，完成 **第 1～7 节**即可；如果你在开发自己的策略，或让 coding agent 把 Core 集成到策略中，再继续阅读 **第 8～11 节**。

> 本文默认只使用 **QMT 模拟环境**。不会自动下单，也不会引导开启实盘。

---

# 第一部分：第一次安装与连接

## 1. 先准备好 3 样东西

请确认：

- QMT / MiniQMT 客户端已经打开，并登录**模拟账户**；
- 你能在 QMT 安装目录中找到 `userdata_mini`；
- 你知道当前登录账户的账户 ID。

`userdata_mini` 示例：

```text
D:\某券商QMT模拟交易端\userdata_mini
```

后文中的 `qmt_path` 都填写这个目录，**不是 QMT 安装根目录**。

如果电脑同时安装了模拟盘和实盘，请确认这里使用的是**模拟盘对应的 `userdata_mini`**。

---

## 2. 确认 Python 和 `xtquant`

Core 要求 Python `>= 3.9`。

先运行：

```bash
python --version
```

再运行：

```bash
python -c "import xtquant; print('xtquant OK')"
```

正常应看到：

```text
xtquant OK
```

如果这里报错，先不要继续。说明当前 Python 还不能访问 MiniQMT 提供的 `xtquant`。

---

## 3. 获取并安装 Core

有 Git：

```bash
git clone https://github.com/smhe00/qmt-execution-core.git
cd qmt-execution-core
python -m pip install -e .
```

没有 Git：在 GitHub 页面选择 **Code → Download ZIP**，解压后进入项目目录，再执行：

```bash
python -m pip install -e .
```

安装完成后运行：

```bash
qmt-execution-core verify
```

只有 release verification 为 PASS，才继续下一步。

### 如果提示 `qmt-execution-core` 不是可识别的命令

这通常只是 Python 的 `Scripts` 目录没有加入 PATH。

可以改用：

```bash
python -m qmt_execution_core.cli verify
```

本文后面的：

```text
qmt-execution-core <command> ...
```

都可以等价替换成：

```text
python -m qmt_execution_core.cli <command> ...
```

参数保持不变。

---

## 4. 只读探测当前 QMT 账户

先确认账户类型和健康状态，避免让新用户猜 `account_type`。

运行下面的**单行命令**，只替换你的 QMT 路径：

```bash
python examples/quickstart_account_probe.py --qmt-path "D:\某券商QMT模拟交易端\userdata_mini"
```

脚本会提示输入账户 ID，输入不会回显。

正常结果类似：

```text
[PASS] account found: 12***34
[INFO] account_type = 2
[INFO] security_account = True
[INFO] healthy = True
[SAFE] read-only probe complete; no subscribe/order/cancel was called
```

记住输出中的 `account_type` 数字。

只有下面两项都为 True 才继续：

```text
security_account = True
healthy = True
```

这个脚本只读取账户发现和状态，不订阅账户，也不会调用 order / cancel。

---

## 5. 创建模拟账户 binding

把 `<ACCOUNT_TYPE>` 替换成第 4 步看到的数字，并替换 QMT 路径：

```bash
qmt-execution-core create-binding --environment simulation --account-type <ACCOUNT_TYPE> --qmt-path "D:\某券商QMT模拟交易端\userdata_mini" --output "config\sim-binding.local.json"
```

如果使用 `python -m` 形式：

```bash
python -m qmt_execution_core.cli create-binding --environment simulation --account-type <ACCOUNT_TYPE> --qmt-path "D:\某券商QMT模拟交易端\userdata_mini" --output "config\sim-binding.local.json"
```

命令会再次要求输入 MiniQMT 账户 ID。

Core 写入的是账户指纹，不会把明文账户 ID 保存到 binding 文件。

生成的：

```text
config\sim-binding.local.json
```

属于本机配置；`.local.json` 已被仓库 `.gitignore` 排除，避免误提交。

---

## 6. 初始化 Runtime Authority

同一台电脑、同一个模拟账户第一次使用 shared runtime 时执行一次：

```bash
qmt-execution-core bootstrap-authority --binding "config\sim-binding.local.json"
```

或：

```bash
python -m qmt_execution_core.cli bootstrap-authority --binding "config\sim-binding.local.json"
```

成功后，这个账户会拥有唯一的 Core coordination domain。

你不需要自己选择 coordination DB 路径，也不要配置：

```text
coordination_path
authority_root
```

以后普通策略启动只验证已有 Authority，不会自动建立另一份。

**不要**为了绕过启动错误手工删除或替换 Runtime Authority / coordination DB。

---

## 7. 跑第一次安全连接测试

### 7.1 准备本地 Runtime 配置

如果 `runtime` 文件夹不存在：

```bash
mkdir runtime
```

复制已经准备好的模板：

```bash
copy examples\runtime_config.quickstart.json runtime_config.local.json
```

如果当前 shell 不支持 `copy`，直接用文件管理器复制并重命名即可。

打开：

```text
runtime_config.local.json
```

只修改：

```json
"qmt_path": "D:/某券商QMT模拟交易端/userdata_mini"
```

保持：

```json
"environment": "simulation",
"live_trading_enabled": false
```

### 7.2 连接 Core

确认模拟 QMT 客户端仍然打开并已登录，然后运行：

```bash
python examples/quickstart_connect.py
```

正常应看到：

```text
[PASS] Core runtime connected to MiniQMT simulation account
[PASS] execution_healthy = True
[PASS] Runtime closed cleanly
```

这个脚本会验证：

```text
Python
→ qmt-execution-core
→ simulation binding
→ Runtime Authority
→ MiniQMT 模拟账户
```

它**不会下单**。脚本内置 Guard 会拒绝所有 execution request。

### 到这里就算首次接入成功

你应当已经得到：

```text
[PASS] qmt-execution-core verify
[PASS] account probe
[PASS] simulation binding
[PASS] Runtime Authority bootstrap
[PASS] quickstart_connect.py
```

如果你只是使用 TGrid 或其他已经集成 Core 的项目，**到这里就回到那个项目自己的用户文档**。不需要理解后面的 Core 集成接口。

### 常见错误

**`QMT userdata path does not exist`**  
确认 `qmt_path` 指向实际存在的 `...\userdata_mini`。

**`xtquant is not installed`**  
回到第 2 节。Core 不会从 PyPI 安装 `xtquant`，它来自本地 MiniQMT 环境。

**`qmt-execution-core` 不是可识别的命令**  
回到第 3 节，改用 `python -m qmt_execution_core.cli ...`。

**account binding mismatch / expected account**  
当前 QMT 登录账户与创建 binding 时输入的账户不一致。

**`shared runtime requires an initialized account Runtime Authority`**  
确认第 6 节使用的是同一个 `sim-binding.local.json`。

---

# 第二部分：策略开发者 / coding agent 如何调用 Core

## 8. 普通策略只需要理解这些接口

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

策略不需要直接操作 SQLite coordination、Runtime Authority、session-id 或 callback queue。

> 如果你的目标是“让 AI 把 Core 集成到另一个策略项目”，优先把本节到第 11 节作为接入说明；涉及修改 Core 自身语义时，再阅读 [SPECIFICATION.md](SPECIFICATION.md)。

---

## 9. 创建 Runtime

```python
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig.from_json("runtime_config.local.json")

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_cash_estimator,
)
```

调用方必须提供两个安全接口。

### `ExecutionGuard`

回答：**这一笔交易现在是否允许执行？**

一般验证账户、环境、持仓、价格、数据新鲜度和策略风险条件。

### `CashRequirementEstimator`

BUY 前计算保守资金需求，例如：

```text
订单金额
+ 手续费
+ 临时冻结/扣款缓冲
+ 其他安全缓冲
```

A 股、港股、港股通等具体费用规则由上层项目提供，Core 不写死。

> Core 0.4.1 不提供“零配置直接下单”的默认 Guard。**连接成功不等于允许交易。**

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
- 不要绕过 Core 直接调用 XtQuant 的 `order_stock()` / `cancel_order_stock()`。

---

## 11. 订单生命周期：只记住 6 个方法

```python
runtime.submit(request)             # 提交新 execution
runtime.poll()                      # 查询 / 推进当前 execution
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

| 状态 | 应该怎么做 |
|---|---|
| `WORKING / PARTIALLY_FILLED` | 继续 `poll()`，或按策略决定 `cancel()` |
| `FILLED / CANCELLED / REJECTED` | lifecycle 已明确结束，可以 `next_cycle()` |
| `UNKNOWN / CANCEL_REJECTED / FAILED` | **不要重新 submit**；先通过 Core 恢复 / 对账 |

程序应确保 Runtime 最终被关闭：

```python
runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=my_cash_estimator,
)

try:
    snapshot = runtime.submit(request)
    # poll / cancel / recovery ...
finally:
    runtime.close()
```

一个 Runtime 同一时间只管理一个 active execution。需要同账户不同标的并发时，使用多个独立 runtime / strategy process，由 shared coordination 负责账户级冲突保护。

---

# 第三部分：安全边界

## 实盘不是本指南的入门流程

模拟盘和实盘必须使用不同的 binding / Runtime Authority domain。

Core 提供：

```python
runtime.confirm_live(token)
```

但它只确认 live gate，不能绕过账户验证、`ExecutionGuard`、资金 reservation 或其他安全检查。

**不要把本文模拟配置直接改成实盘配置。** 实盘部署、恢复、故障处置和 live gate 见 [OPERATIONS.md](OPERATIONS.md)。

## 普通策略不要直接使用这些接口

```text
SQLiteExecutionCoordinator
AccountRuntimeAuthority
SessionIdLease
SerialEventQueue
CoordinationDbIdentity
```

这些属于高级集成或 Core 内部实现。

## 三份主文档怎么选

```text
USER_GUIDE.md
  用户首次接入 + 策略 / coding agent 集成

SPECIFICATION.md
  Core 当前产品合同、状态机、安全不变量

OPERATIONS.md
  运行部署、Runtime Authority、恢复、实盘 gate、故障处置
```

旧规格、审计和实现任务统一在 `docs/archive/`，不作为当前使用入口。

---

## 一句话使用路径

```text
QMT 模拟账户已登录
→ verify
→ account probe
→ create binding
→ bootstrap Authority
→ quickstart_connect.py
→ 已集成项目：回项目文档
→ 自研策略 / AI 集成：Guard + CashEstimator
→ submit / poll / cancel / next_cycle
```
