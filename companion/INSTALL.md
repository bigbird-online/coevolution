# 装载指南（从零跑通）

照这份从零把 companion 装起来。全程只在本目录内操作，运行时产物（`.venv/`、`user/`）都不入库。

## 前置

- **uv**（唯一硬前置）：Python 包/环境管理器。装法见 https://docs.astral.sh/uv/ 。
  uv 会在装载时**自动拉取满足 `requires-python >=3.11` 的 Python**，所以你**不必**预装 Python 3.11——系统有没有 python3、是什么版本都不影响。
- 一个能联网的终端（采集要拉 RSS）。

## 一步装载

```bash
./init.sh
```

它是幂等的，做四件事：

1. 检查 uv 存在（缺则明确报错退出，不静默）。
2. `uv sync` 建本 skill 自己的 `.venv`（引擎依赖 feedparser / filelock / httpx / requests / pyyaml / python-dateutil）。
3. 把 `assets/*.template.yaml` 拷成 `user/profile.yaml`、`user/sources.yaml`——**已存在则不覆盖**，只提示（重跑不会抹掉你已填的配置）。
4. 建 `user/pushed/` 运行时目录，并打印下一步。

## 填 profile（必做——工具不替你猜）

打开 `user/profile.yaml`，逐项填。**每一项都因人而异，空着＝还没跟本人对齐，不是"随便填也能跑"。** 最关键的三项：

| 字段 | 填什么 |
|------|--------|
| `focus_domain` | 本轮聚焦领域。把"宽口径"收窄到本人真正在追的问题。 |
| `landing_criteria` | 认什么算"读有所获、产生了真实决定"。不填就会掉进"推送成功≠有价值"的陷阱。 |
| `score_baseline` | 本人的评分基线/分布。**不能沿用别人的门槛**——一人的基线不代表另一人。 |

其余（`display_name` / `domain_weight` / `source_preference` / `timezone` 等）按模板注释填。

**`delivery:` 段可以完全留空**——不配 delivery＝只把文章落盘到 `user/pushed/`，你自己去目录里读，这是**完全合法**的用法。要投递到 webhook / 外部命令时再按模板里 deliver 的注释填。

## 调 sources

`user/sources.yaml` 是**中性种子池**，只保证"能跑起来"，不保证"对味"。按你的 `focus_domain`：删不关心的领域、加信任的源、标 `avoid`。真正对味的池子靠"用中迭代"长出来。

## 最小验证（证明装成功）

```bash
./.venv/bin/python3 scripts/deliver.py status
```

装成功会打印 `还没有送达记录。`（送达层就位、venv 可用）。

进一步验采集链路（会联网拉源）：

```bash
./.venv/bin/python3 scripts/article_collector.py collect --days 7
./.venv/bin/python3 scripts/pool.py intake-status
```

## 让它每天自动跑（日推）

上面装完只是"能手动跑一次"。**要真正变成「每日伴读」，得挂定时器**——产品本身不自带调度。

一次完整跑批用 `./daily.sh`，它按钉死的时序把链走完：采集 → 选文 → 入池 → 清过期 → 取今日一篇 → 送达落盘 → 标记已推。

先手动跑一次确认没问题：

```bash
./daily.sh
tail -20 user/daily.log     # 看这次跑批做了什么
ls user/pushed/             # 今天推的那篇就在这
```

确认无误后挂 cron（以每天早 8 点为例，**把路径换成你自己的**）：

```bash
crontab -e
# 加这一行：
0 8 * * * cd /你的路径/companion && ./daily.sh >> user/cron.log 2>&1
```

macOS 上 cron 可能受权限限制，也可以用 `launchd`；服务器上用 `systemd timer` 同理——**关键是每天定时执行 `daily.sh` 这一件事**。

关于跑批的两个设计取舍，先知道免得误判成故障：

- **今天没有够格的文章就不推**，脚本正常退出（exit 0）。「丰年存粮、荒年开仓」的前提就是允许荒年——硬凑一篇比不推更糟。日志里会写明"池空——今天不推"。
- **只有落盘失败才算真失败**（非 0 退出）。投递（webhook / 外部命令）失败只告警不阻断，因为文章已经落在 `user/pushed/` 了，照样读得到。

## 常见坑

- **`ERROR: skill venv not found`**：`.venv` 没建成。回本目录跑 `uv sync`（或重跑 `./init.sh`）。`article_collector.py` 会自举到 `.venv/bin/python3`，没有它直接报错退出。
- **重跑 `init.sh` 怕覆盖配置**：不会。已存在的 `user/*.yaml` 只提示 `[skip]`，不覆盖。想重置某个配置，先手动删它再重跑。
- **`collect` 拉不到文章**：多为源网络/RSS 问题或 `--days` 窗口太窄。先 `./.venv/bin/python3 scripts/article_collector.py status` 看源统计，再逐源排查。
- **只想落盘、不想配投递**：那就别动 `profile.yaml` 的 `delivery:` 段，文章会稳稳落在 `user/pushed/`。
