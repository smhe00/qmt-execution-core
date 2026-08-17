# qmt-execution-core 运行与运维

本文说明 MiniQMT 生产形态 runtime、0.4.1 Runtime Authority、恢复流程和运维边界。策略调用 API 见 [USER_GUIDE.md](USER_GUIDE.md)，执行语义见 [SPECIFICATION.md](SPECIFICATION.md)。

## 1. Runtime 启动生命周期

概念顺序：

```text
load strict runtime config
-> verify QMT userdata path
-> load fingerprint-only account binding
-> initialize callback EventQueue
-> construct/register XtQuant callback
-> acquire runtime/session safety resources
-> trader.start()
-> trader.connect() == exact int 0
-> exact account info/status verification
-> subscribe(account) == exact int 0
-> resolve/verify Runtime Authority when shared
-> construct/open ExecutionSession
-> verify journal state-machine/source binding
-> restart reconciliation if needed
-> mark recovery complete
```

任何关键步骤失败均保持 broker execution disabled。

## 2. Account Binding

binding 不保存明文账户号：

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "account_type": 2,
  "account_id_sha256": "...",
  "qmt_path_sha256": "..."
}
```

运行时必须找到唯一匹配且健康的账户，否则 fail closed。

稳定 `account_key` 由环境、账户类型和账户指纹派生。

## 3. Runtime Authority

0.4.1 shared runtime 不允许 strategy 自选 production coordination DB。

首次初始化：

```bash
qmt-execution-core bootstrap-authority --binding config/sim-binding.local.json
```

Authority 认证：

```text
account_key
canonical DB path
db_uuid
authority_id
```

正常策略启动仅验证。以下情况都应停止执行并人工检查：

- Authority 文件缺失/损坏；
- account key 不匹配；
- Authority 与 DB canonical path 不匹配；
- `db_uuid` 不匹配；
- `authority_id` 不匹配；
- DB 被删除后在原路径重新建立；
- 调用方试图用另一份 coordination DB 绕过 Authority。

不要通过删除 Authority、删除 journal、创建空 DB 或修改 metadata 来“修复”启动错误。

## 4. exclusive 与 shared

### exclusive

```python
runtime_lock_mode="exclusive"
```

默认、最保守：

```text
qmt_path -> runtime-wide mutex
```

适用于：

- 单策略；
- 初次迁移；
- 尚未完成 shared Runtime Authority 初始化的环境。

### shared

```python
runtime_lock_mode="shared"
```

安全边界：

```text
per-session journal mutex
+ unique Account Runtime Authority
+ Authority-certified SQLite coordination DB
+ bounded MiniQMT session-id lease
+ (account_key, symbol) durable claim
+ atomic shared BUY cash reservation
```

同账户不同标的可并发；同账户同标的 unresolved execution 被阻止。

## 5. Session ID

shared runtime 使用 bounded session-id pool：

- caller supplied exact `session_id` 可保留；
- 自动候选由稳定 key 派生；
- lease 冲突时只做有限 fallback；
- connect failure 只做有限尝试；
- session ID lease 使用 OS file lock；
- process crash 后由 OS lock lifetime 释放；
- 不允许无限随机 retry。

## 6. BUY 资金协调

coordinated BUY：

```text
Core durable intent
-> fresh broker query_asset()
-> conservative cash estimate
-> atomic shared claim/reservation
-> project before_broker_submit sidecar
-> broker order_stock()
```

以下在 broker side effect 前 fail closed：

- estimator 缺失；
- broker cash facts 不新鲜/不可用；
- symbol claim 冲突；
- available cash 不足；
- coordination identity 不合法。

Core 不维护结算中的“本地现金返还”。下一笔 BUY 始终重新读取 broker available cash。

## 7. Callback 与断线

QMT callbacks 只生成 immutable observation，进入 bounded `SerialEventQueue`。

断线立即：

```text
transport_connected = false
account_healthy = false
live confirmation revoked
broker execution disabled
```

恢复：

```text
connect exact success
-> bound account health verification
-> subscribe exact success
-> durable execution reconciliation
-> project session evidence re-verification
-> mark recovery complete
-> live mode fresh confirmation
```

不要因为 transport 恢复或单独收到账户 OK callback 就直接恢复新订单权限。

## 8. Journal / restart

journal 的 transition/source hash 是安全边界。

升级 Core 或修改 execution semantics 前：

1. 用旧版本 + broker authoritative query 确认旧 execution 已权威闭环；
2. 不得存在 `UNKNOWN`、`WORKING`、`CANCELLING` 等未解决状态；
3. 保留旧 journal 作为审计记录；
4. 新语义使用新的兼容 journal；
5. 不得通过忽略 hash 或直接删除 active journal 绕过检查。

## 9. 模拟环境验收

首次部署至少运行：

```bash
qmt-execution-core verify
python examples/quickstart_connect.py
```

并确认：

```text
release verification PASS
binding verified
Runtime Authority verified
execution_healthy = True
runtime closed cleanly
```

`quickstart_connect.py` 不应提交订单。

## 10. 实盘 gate

实盘默认关闭。至少同时要求：

```text
live_trading_enabled == true
AND
runtime.confirm_live(token)
```

此外必须继续通过：

- exact account binding/type/status；
- Runtime Authority；
- execution health；
- event queue health；
- `ExecutionGuard`；
- shared resource coordination；
- disconnect/recovery reconciliation。

`confirm_live()` 不是绕过其他安全检查的 superuser 开关。

## 11. 常见故障处理

### `QMT userdata path does not exist`

确认 `qmt_path` 指向真正的 `userdata_mini`，不是 QMT 安装根目录。

### account binding mismatch

当前登录账户与 binding 不一致。确认环境、账户类型和账户本身，不要直接重建/修改 binding 来绕过错误。

### shared runtime requires initialized Runtime Authority

执行显式 bootstrap：

```bash
qmt-execution-core bootstrap-authority --binding <binding>
```

如果 Authority 本来应该存在却消失，先调查文件/磁盘/用户目录问题，不要自动生成新 domain。

### Authority / DB UUID mismatch

视为可能的 DB 替换、误恢复或路径错误。停止 shared execution，保留现场并核对 Authority 与 DB metadata；禁止自动 rewrite Authority。

### `xtquant is not installed`

`xtquant` 由本地 MiniQMT 环境提供。确认 Python 解释器可以访问券商提供的 XtQuant 环境。

### `UNKNOWN` / `CANCEL_REJECTED` / unresolved `FAILED`

不要重新 submit。通过 Core 的 poll/reconcile/recover 路径取得 broker authoritative reality。

## 12. 发布与验证

修改执行语义：

```bash
python -m pytest
python -m compileall -q src tests
qmt-execution-core verify
```

涉及 mutex / coordination / Runtime Authority / session-id lease：

- 运行跨进程 race / fault-injection；
- 运行 Windows safety probes；
- 验证 Python 3.9+ / wheel clean install；
- CI 不得发送真实或模拟 QMT 订单。
