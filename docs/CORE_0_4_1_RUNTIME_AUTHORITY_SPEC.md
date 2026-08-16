# qmt-execution-core 0.4.1 — Account Runtime Authority 安全增量规格

> Status: FROZEN DELTA FOR IMPLEMENTATION
> Baseline: qmt-execution-core 0.4.0 @ `acf20d9fe5cf2aede3cc0ad0e8936ecb0c5b2692`
> Scope: coordination-domain uniqueness hardening only
> Real/simulation QMT order/cancel: NOT AUTHORIZED

## 1. 问题

Core 0.4.0 的 shared runtime 安全性依赖调用方为同一账户配置同一个 `coordination_path`。如果两个独立策略进程对同一 `account_key` 误配两个 SQLite 文件，则两个数据库彼此不可见，`(account_key, symbol)` claim 和 shared BUY cash reservation 都会形成 split-brain。

因此以下条件不能继续只是部署约束：

```text
same authoritative account
→ same coordination domain
```

Core 0.4.1 必须把它提升为运行时可验证的不变量。

## 2. 目标模型

每个账户拥有一个唯一的 Account Runtime Authority。Authority 认证该账户唯一允许使用的 coordination DB 实例。

```text
Authoritative QMT Account Identity
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
            |
            +-- account_key
            +-- db_uuid
            +-- authority_id
            +-- symbol claims
            +-- cash reservations
```

0.4.1 的推荐部署模型为 **每账户一个 coordination DB**。不同账户不共享同一 DB 文件。

## 3. Runtime Authority 唯一性

### INV-AUTH-001

For one authoritative account identity, exactly one canonical Account Runtime Authority path may be used by Core shared runtime on the supported host/user coordination domain.

中文：

> 对同一权威账户身份，Core shared runtime 只能解析到一个 canonical Runtime Authority 文件路径。

Authority 文件名必须从稳定 `account_key` 推导，策略不得自行选择文件名。

推荐：

```text
<canonical-authority-root>/
    <account_key>.authority.json
    <account_key>.authority.lock
```

其中 `<canonical-authority-root>` 是 Core 的 host/user 级固定运行时目录，不得由每个 strategy 随意覆盖。测试可以通过显式 test-only injection 使用临时目录。

## 4. Authority 内容

Authority 至少包含：

```json
{
  "schema_version": 1,
  "authority_id": "UUID",
  "account_key": "...",
  "environment": "live|simulation",
  "account_type": 2,
  "account_id_sha256": "...",
  "coordination_db_path": "canonical absolute path",
  "coordination_db_uuid": "UUID"
}
```

`coordination_db_uuid` 是 DB 实例身份，不是内容 hash。

DB 内容变化、claim/reservation 增删、VACUUM/WAL checkpoint 均不得改变 `db_uuid`。

只有显式创建一个新的 coordination DB 实例时才生成新的 `db_uuid`。

## 5. DB 身份绑定

Dedicated coordination DB 必须有持久 metadata，至少包含：

```text
schema_version
account_key
db_uuid
authority_id
```

### INV-AUTH-002

Shared execution is allowed only when all of the following are true:

```text
runtime actual account_key
== authority.account_key
== coordination DB metadata.account_key

canonical(opened DB path)
== authority.coordination_db_path

authority.coordination_db_uuid
== coordination DB metadata.db_uuid

authority.authority_id
== coordination DB metadata.authority_id
```

任意一项不匹配必须 FAIL CLOSED。

## 6. Runtime API 边界

Production shared runtime 不应再把任意 `coordination_path` 当作安全来源。

目标 API 语义：

```text
actual account binding
→ derive account_key
→ resolve canonical Runtime Authority
→ verify Authority
→ open Authority-certified DB
→ verify DB identity metadata
→ construct SQLiteExecutionCoordinator
```

策略只表达“使用 shared runtime”；不决定“这个账户应该同步到哪个 DB”。

允许 test-only injection coordinator/authority store，但 production builder 必须明确隔离该入口。

## 7. 首次初始化 / Bootstrap

首次 Authority/DB 建立必须在 `<account_key>.authority.lock` 下原子执行：

```text
derive account_key
→ acquire authority lock
→ authority exists?
    yes: verify only; never silently rewrite
    no : explicit bootstrap path
         → create dedicated DB
         → generate authority_id
         → generate db_uuid
         → persist DB metadata
         → fsync DB as applicable
         → atomic temp-write + fsync + replace Authority
→ release lock
```

两个并发进程不得创建两个 authority/DB domain。

普通 strategy runtime 不应在发现 Authority 缺失/损坏时静默新建第二个 domain。若支持自动首次 bootstrap，必须证明并发初始化原子性，并且只能使用 canonical DB destination；更推荐显式 bootstrap API/CLI。

## 8. DB 替换检测

路径匹配不足以证明 DB 实例相同。

例如：

```text
原 DB 被删除
→ 相同路径重新生成空 DB
```

如果新 DB 的 `db_uuid` 与 Authority 不一致：

```text
FAIL CLOSED
```

UUID 不用于检测正常内容变化，也不用于抵抗恶意完整复制；其目标是检测误删后重建、误替换、错误恢复、错误 DB 指向等 operational fault。

## 9. 不允许的行为

- 同一账户由不同 strategy 自行指定不同 coordination DB。
- shared runtime 在 Authority 校验失败后 fallback 到新的空 DB。
- DB UUID mismatch 后自动“修复” Authority。
- Authority 缺失时从现有 DB 内容猜测并静默 adopt。
- 不同 `account_key` 复用同一个 Authority/DB identity。
- 为了兼容 0.4.0 而绕过 identity verification。

## 10. 兼容策略

0.4.0 的直接 `coordination_path` 可作为低层/legacy API 保留，但：

- production `MiniQmtRuntime` shared mode应优先/默认使用 Runtime Authority；
- TGrid production composition 必须使用 Authority 模式；
- legacy explicit-path 模式不得被文档描述为具备 coordination-domain uniqueness guarantee。

如 0.4.1 改动现有 coordinator schema，旧 0.4.0 DB 不得静默迁移为 Authority-bound DB。必须提供显式 bootstrap/adopt/migration 流程，并验证空闲/无 unresolved execution 条件。

## 11. 最低验收场景

必须至少自动化证明：

1. 同一账户两个进程解析出同一个 Authority 文件。
2. 同一账户两个进程解析出同一个 certified DB path + UUID。
3. 不同账户解析出不同 Authority 和不同 DB。
4. Authority account_key mismatch → fail closed。
5. Authority DB path 与打开路径 mismatch → fail closed。
6. Authority DB UUID 与 DB metadata mismatch → fail closed。
7. DB account_key mismatch → fail closed。
8. DB authority_id mismatch → fail closed。
9. 删除原 DB 后在原路径创建新 DB → UUID mismatch → fail closed。
10. 两个进程并发首次 bootstrap → 只产生一个 authority_id/db_uuid/domain。
11. Authority 文件损坏/截断 → fail closed，不创建 fallback DB。
12. Authority lock contention 在 Windows/Linux 正确工作。
13. 在认证通过后，Core 0.4 的三进程 formal/coordination invariants 保持不变。
14. Python >=3.9、wheel clean install、Windows safety gates 保持通过。

## 12. 一句话规格

> **同一账户的 shared execution 必须先通过唯一 Runtime Authority 对唯一 dedicated coordination DB 实例进行身份认证；DB 路径相同但实例 UUID 不同也必须拒绝执行。**
