# 日推池（常平仓）参考 — pool.py 机制

> SKILL.md §①-日推池 的按需查参考。顶层只留"为什么有池 + 池非空即不 both-fail"触发语义；池的 schema / 子命令 / 时序 / 原子性等实现细节在此。

## 为什么有池

连续两周 both-fail 的根不是"某天真没好文"，而是**"当天采、当天必须凑出一篇 ≥8.0"把生产和消费绑死在同一天**——好文（带个体落点的 ≥8.0）不是每天均匀到来，但阅读需求是每天的。日推池 = **库存缓冲**：丰年存粮、荒年开仓，填平供给波动。**它治波动，不治产能绝对不足**（池从建起是空的，荒年无粮可放）。

**一句话**：把"打分"和"推送"解耦成两个独立日动作——每天①尽力往池蓄水（≥8.0 入库）②从池取最高分放粮。

## 池文件与 schema

`user/daily-push-pool.jsonl`，每行一条池条：
```json
{"pool_id":"pool-YYYYMMDD-NN", "title":"", "url":"", "source":"", "domain":"", "score":8.1,
 "scoring_path":"", "reach_gap_exemption":null, "enqueued_date":"2026-07-11",
 "expiry_date":"2026-07-18", "time_sensitive":true,
 "status":"in_pool|pushed|expired", "pushed_date":null}
```

**三态流转**：`in_pool`（入库待推）→ `pushed`（已推）或 `expired`（时效文过期作废，不再推）。

## pool.py 子命令

（`python3 scripts/pool.py <cmd>`，从 companion/ 根跑）

| 子命令 | 作用 |
|--------|------|
| `enqueue --file <selected.json>` | 一篇 ≥8.0 文入池（<8.0 直接 REJECT，8.0 硬门再钉一道）。判 time_sensitive，true 则 expiry=enqueued+7天 |
| `pick` | 取库存最高分未过期一篇 → stdout（**只读不改状态**）；池空返回退出码非 0 |
| `mark-pushed --pool-id <id>` | 推**真成功后**调，该条 status=pushed+pushed_date；已 pushed 则幂等 no-op（防重推） |
| `expire` | 扫全池，time_sensitive 且过 expiry_date 未推 → status=expired。每日跑一次 |
| `intake-status` | 输出 `{intake_today, pool_size, pushed_from_pool_today, oldest_in_pool_age_days, consecutive_zero_intake_days}` |

## 每日调用时序（钉死）

`expire → pick →（推真成功）→ mark-pushed`。**expire 必在 pick 之前**——否则当天该过期的时效文可能被 pick 取走推出去（过期时事文=负价值）。enqueue 在 1b 打分后随时发生，与放粮链独立。

**放粮优先级**（pick 内建）：先按 `time_sensitive:true 且快到期` 优先（防浪费），再按分值；同分先推快到期。

## pick→mark-pushed 原子性 + 防重推（防 0606 重推事故同型）

- `pick` **只从 `status=in_pool` 取**，不改状态；调用方推**真成功后才** `mark-pushed`。pick 与 mark-pushed 之间崩溃 → 该条仍 `in_pool`，下次照常被 pick 到（**不漏推**）。反之若 pick 就改状态，崩溃即漏推无痕。
- **防池内重推靠池自身状态，不靠 1a-bis**：1a-bis 是**采集侧**去重，库存文走 pick 出池推送**根本不过采集环**（且 14 天窗口对老库存超窗）。真正防重推 = ①pick 只取 `in_pool`（pushed/expired 不再被取）②崩溃恢复时先查当日 reading-log 有无该 `pool_id` 的 pushed 记录，有则直接 mark-pushed 不重发。

## 时效文 7 天保鲜

入池判 `time_sensitive`（时事/政策/市场=true；认知/框架/健康原理=false）。true → `expiry_date=enqueued+7天`，到期未推 → expired 不再推；false → 长存（认知类可长期作库存）。

## 入库速率监控（防库存掩盖产能病）

库存让推送数天天 ≥1（好看），但**入库速率连续多天=0 = 产能真断了、只是被存粮盖住**。`consecutive_zero_intake_days` 由 pool.py **自算**（扫各条 enqueued_date，从今天往回数连续 0 入库天数），周 audit **只消费不自算**；≥3 → 报 P0，**与推送是否连续无关**（详见 SKILL §周节奏 audit）。

## reading-log 每日必落 `pool_status`（含 0 入库日）

每次 cron 跑**无条件**追加一条：
```json
{"action":"pool_status", "date":"YYYY-MM-DD", "intake_today":N, "pool_size":N,
 "pushed_from_pool_today":N, "oldest_in_pool_age_days":N, "consecutive_zero_intake_days":N}
```
字段直接取 `python3 scripts/pool.py intake-status` 输出。**即使 `intake_today=0` 也必须落这条**——这正是"入库断供"要被看见的那天；漏落 = 连续计数断链、周 audit 验不到闭环。
