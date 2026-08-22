---
name: companion
description: >
  伴读引擎：每天从配好的信源采文章、按会员自己的基线打分、只让 ≥8.0 的进日推池、
  取最高分一篇两层送达（落盘必成 + 投递可选）。Use when: 伴读 / 每日读物 / 帮某人读到
  该领域真正值得读的文章 / daily reading companion / 采集打分推送链路 / article
  collect-score-pool-deliver。装载后按会员画像跑；内核中性，不含个人数据。
---

# companion（伴读引擎）

帮一位高级外行每天读到 ta 所在领域里真正值得读的文章，并陪 ta 读透。内核把
"采集 → 打分 → 入池 → 送达"串成自足链路，全部落在本 skill 自有的 `user/` 运行时目录，
不依赖外部数据。

## 何时用

- 要为某位具体会员做"每日伴读"：按 ta 的 `focus_domain` / `score_baseline` /
  `landing_criteria` 配一次，之后每天跑采集→打分→入池→送达。
- 要判某篇是否够格进池、要看池健康、要看近期推送分布 vs 应有权重。

## 何时不用

- 会员画像（`user/profile.yaml`）还没填 `focus_domain` / `landing_criteria` /
  `score_baseline`——空着＝还没跟本人对齐，此时链路能跑但产出无价值。先对齐再跑。
- 想改内核算法/schema——那是维护者的活，不是日常使用。

## 装载状态（用前必确认）

本 skill 需先装载才能跑。装载入口：

- `./init.sh` —— 幂等装载：检查 uv、`uv sync` 建 `.venv`、拷模板到 `user/`、建 `user/pushed/`。
- 装载与填 profile 的完整步骤见 `INSTALL.md`；门面与职责总览见 `README.md`。

判断是否已装载：`.venv/bin/python3` 存在且 `user/profile.yaml` 已填三关键项。
最小自检：`./.venv/bin/python3 scripts/deliver.py status`（打印"还没有送达记录。"＝就位）。

## 内核脚本调用方式

**采集器必须跑在 skill 自有 `.venv` 上**（`article_collector.py` 会自举到
`.venv/bin/python3`，缺则报错退出）；`pool.py` / `deliver.py` / `refresh-state.py` 为
纯标准库，但为一致起见同样用 `.venv/bin/python3` 调。全部在本 skill 根目录内运行。

```bash
# 采集（联网拉 user/sources.yaml 里的源，采最近 N 天）
./.venv/bin/python3 scripts/article_collector.py collect --days 7
./.venv/bin/python3 scripts/article_collector.py status
./.venv/bin/python3 scripts/article_collector.py rate <文件> --score 4 [--reason "..."]
./.venv/bin/python3 scripts/article_collector.py intake-status
./.venv/bin/python3 scripts/article_collector.py analyze-feedback --days 30

# 选文：把采集打分后的文章挑出够格一篇 → selected.json（补的是采集↔pool 那节断链）
#   采集给每篇算 0-100 分写进 md 的 score_100；select 换算到 0-10 制（÷10）导出 enqueue 认的 json。
#   分制约定：pool 的 8.0 门 = 采集原始 80 分。换算口径写死在 select.py，不散落别处。
./.venv/bin/python3 scripts/select.py --out user/selected.json   # 选最高分一篇（--top N 多篇 / --min 门槛）

# 日推池：时序钉死 enqueue → expire → pick →（推成功）→ mark-pushed
./.venv/bin/python3 scripts/pool.py enqueue --file user/selected.json   # ≥8.0 文入池
./.venv/bin/python3 scripts/pool.py expire                           # 标过期时效文
./.venv/bin/python3 scripts/pool.py pick                             # 取最高分未过期一篇 → stdout
./.venv/bin/python3 scripts/pool.py mark-pushed --pool-id <id>       # 标某条已推
./.venv/bin/python3 scripts/pool.py intake-status                    # 池健康统计

# 送达：落盘必成 + 投递（读 user/profile.yaml 的 delivery: 段，可留空＝只落盘）
./.venv/bin/python3 scripts/deliver.py status                        # 看送达记录
# 作库用：from deliver import send; send(title, content)

# 动态快照（近 7 日推送 / 领域分布 vs 权重 / 基线校准），只读不落盘
./.venv/bin/python3 scripts/refresh-state.py

# 单篇转 epub
./scripts/article-to-epub.sh <文章.md>
```

## 方法论

打分与设计判据在 `references/`：`scoring.md`（打分体系）、
`scoring-profile.example.md`（画像化打分示例）、`pool.md`（日推池设计）、
`signal-schema.md`（反传信号 schema）。
