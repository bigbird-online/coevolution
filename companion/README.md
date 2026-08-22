# companion —— 伴读引擎

**每天帮一位高级外行读到 ta 所在领域里真正值得读的那几篇文章，并陪 ta 读透。**

companion 把"信源采集 → 打分筛选 → 好文入库 → 定时送达"串成一条自足链路：从你配的信源里采文章、按你自己的基线打分、只让够格的（≥8.0）进"日推池"、每天取最高分的一篇送到你手里。丰年存粮、荒年开仓，填平"好文不是每天均匀到来"的供给波动。

## 给谁用

- 想在某个领域（科技/经济/法律/医学…）保持高质量输入、但没时间自己海选的**高级外行**。
- 想给某位具体的人做"每日伴读"的**部署者/伴读者**——你按 ta 的画像配一次，之后每天自动跑。

内核是**中性**的：不预设任何领域倾向、不含任何具体个人数据。你的画像与信源都填在运行时目录 `user/`（不入库）。

## 装载

见 [`INSTALL.md`](./INSTALL.md)。一句话：装好 `uv` → 跑 `./init.sh` → 填 `user/profile.yaml` 的 `focus_domain` / `landing_criteria` / `score_baseline`。

## 内核脚本（各一句话职责）

全部在 `scripts/`。采集器跑在 `init.sh` 建的 `.venv` 上（会自举），其余为纯标准库。

| 脚本 | 职责 |
|------|------|
| `article_collector.py` | 从 `user/sources.yaml` 采文章、提全文、打分（0-100 分写进文章 `score_100`）、生成 Markdown。子命令：`collect` / `status` / `rate` / `intake-status` / `analyze-feedback`。 |
| `select.py` | 选文：扫已采文章、把 `score_100` 换算到 0-10 制（÷10）、导出最高分文为 `selected.json` 喂给入池。衔接采集与日推池那一环。子命令无（`--out` / `--min` / `--top`）。 |
| `pool.py` | 日推池：≥8.0 文入库、取当前最高分未过期一篇、标过期/已推。子命令：`enqueue` / `pick` / `expire` / `mark-pushed` / `intake-status`。 |
| `deliver.py` | 两层送达：落盘到 `user/pushed/`（必成）+ 投递到 webhook/命令（可选、失败不阻断）。`deliver.py status` 看送达记录；作库用 `from deliver import send`。 |
| `refresh-state.py` | 打印当前动态快照（近 7 日推送 / 领域分布 vs 权重 / 近期基线校准），只读不落盘。 |
| `article-to-epub.sh` | 把单篇文章转成 epub（需另装 `pandoc >=3.0` 在 PATH，仅此脚本用到，非核心链路依赖）。 |

每日调用链：`collect → select → enqueue → expire → pick →（推成功）→ mark-pushed`。

**这条链已封装成 `./daily.sh`**——挂到 cron 上每天跑一次，就是「每日伴读」。产品本身不自带调度，**不挂定时器它就只是个手动工具**；怎么挂见 [`INSTALL.md`](./INSTALL.md) 的「让它每天自动跑」。

## 方法论

判据与设计写在 [`references/`](./references/)：

- `scoring.md` / `scoring-profile.example.md` —— 打分体系与画像化打分示例。
- `pool.md` —— 日推池（生产/消费解耦）的设计。
- `signal-schema.md` —— 反传信号的数据结构。

## 目录一览

```
scripts/       6 个引擎（见上表）
references/    4 篇方法论
assets/        2 个模板（profile / sources），init 时拷入 user/
pyproject.toml 引擎依赖（uv sync 读它建 .venv）
init.sh        装载脚本（幂等）
daily.sh       每日跑批（采→选→入池→放粮→送达），挂 cron 即成日推
user/          运行时目录（画像/信源/落盘/日志）——不入库
.venv/         引擎运行环境——不入库
```
