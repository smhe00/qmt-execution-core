# qmt-execution-core Quick Start

这份文档面向第一次使用 MiniQMT + qmt-execution-core 的用户。

假设你已经完成：

- Windows 上已经安装 MiniQMT / QMT；
- 已经登录**模拟账户**；
- 电脑上可以运行 Python `>=3.9`；
- 会复制命令并修改文件路径。

> 本 Quick Start 默认只做**模拟环境 + 安全连接验证**。不会自动下单，也不会开启实盘。

---

## 1. 找到 MiniQMT 的 `userdata_mini`

在你的 QMT 安装目录里找到：

```text
userdata_mini
```

例如：

```text
D:\某券商QMT模拟交易端\userdata_mini
```

后面所有 `qmt_path` 都填写这个目录，而不是 QMT 安装根目录。

如果你同时安装了模拟盘和实盘，请先确认当前使用的是**模拟盘**的 `userdata_mini`。

---

## 2. 安装 Core

```bash
git clone https://github.com/smhe00/qmt-execution-core.git
cd qmt-execution-core
python -m pip install -e .
```

然后运行：

```bash
qmt-execution-core verify
```

看到 release verification PASS 后再继续。

---

## 3. 创建模拟账户 binding

先创建本地目录：

```text
config/
runtime/
```

然后执行：

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path "D:\某券商QMT模拟交易端\userdata_mini" \
  --output "config\sim-binding.json"
```

命令会要求输入 MiniQMT 账户 ID。

Core 保存的是账户指纹，不会把明文账户 ID 写入 `sim-binding.json`。

> 当前已验证的普通证券账户类型使用 `--account-type 2`。如果你的券商账户类型不同，不要猜测，先确认账户类型再继续。

---

## 4. 初始化 Runtime Authority

同一个账户第一次使用 shared runtime 时执行一次：

```bash
qmt-execution-core bootstrap-authority \
  --binding "config\sim-binding.json"
```

成功后 Core 会为这个账户建立唯一的本地 coordination domain。

以后普通策略启动只会验证它，不会自动创建第二份。

不要手工修改或删除 Runtime Authority / coordination DB 来解决启动错误。

---

## 5. 创建本地 runtime 配置

在仓库根目录创建：

```text
runtime_config.local.json
```

内容如下，只需要修改 `qmt_path`：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "D:/某券商QMT模拟交易端/userdata_mini",
  "binding_path": "config/sim-binding.json",
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

保持：

```text
"environment": "simulation"
"live_trading_enabled": false
```

不要添加：

```text
coordination_path
authority_root
```

shared mode 会自动使用账户自己的 Runtime Authority。

---

## 6. 运行安全连接测试

确保模拟 QMT 客户端仍然打开并已登录，然后运行：

```bash
python examples/quickstart_connect.py
```

正常情况下会看到类似：

```text
[PASS] Core runtime connected to MiniQMT simulation account
[PASS] execution_healthy = True
[PASS] Runtime closed cleanly
```

这个脚本：

- 会连接 MiniQMT；
- 会验证 binding / Runtime Authority / 账户健康状态；
- 会建立并关闭 Core runtime；
- **不会提交订单**；
- 内置 Guard 会拒绝任何 execution request。

如果这里报错，先不要尝试交易。

---

## 7. 常见问题

### `QMT userdata path does not exist`

`qmt_path` 填错了。确认指向实际存在的：

```text
...\userdata_mini
```

### `expected account` / account binding mismatch

当前登录的账户和你创建 binding 时使用的账户不一致。

重新确认你登录的是同一个模拟账户。

### `shared runtime requires an initialized account Runtime Authority`

先执行：

```bash
qmt-execution-core bootstrap-authority --binding config\sim-binding.json
```

### `xtquant is not installed`

Core 本身不会从 PyPI 安装 `xtquant`。`xtquant` 由本地 MiniQMT 环境提供。

确认你使用的是能够访问 MiniQMT Python 环境的 Python 解释器。

---

## 8. 什么时候算 Quick Start 完成？

下面四项都完成即可：

```text
[PASS] qmt-execution-core verify
[PASS] simulation binding created
[PASS] Runtime Authority initialized
[PASS] examples/quickstart_connect.py connected and closed cleanly
```

到这里，你已经证明：

```text
Python
→ qmt-execution-core
→ simulation binding
→ Runtime Authority
→ MiniQMT account
```

这条基础链路可以正常工作。

---

## 9. 下一步：接入自己的策略

Core 不决定“买什么、什么时候买、买多少”，所以真正提交订单前，你的策略还需要提供：

```text
ExecutionGuard
CashRequirementEstimator
```

然后通过：

```python
runtime.submit(request)
runtime.poll()
runtime.cancel()
runtime.next_cycle()
```

管理订单生命周期。

继续阅读：

- [`USER_API.md`](USER_API.md)：策略程序真正需要调用的接口；
- 根目录 [`README.md`](../README.md)：完整能力与架构说明。

> 当前 Core 0.4.1 没有提供“无策略判断、零配置直接下模拟单”的默认 Guard。这是有意的安全边界：Quick Start 先证明运行环境正确，不把“连接成功”自动升级为“允许交易”。
