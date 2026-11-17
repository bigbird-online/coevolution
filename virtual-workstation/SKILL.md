---
name: virtual-workstation
description: >
  虚拟工位：把一台美国云主机开成 Tailscale 出口节点，让本人的电脑从这个固定出口用海外 AI 服务；
  登录 AI 账号前查出口对不对（egress），每周查出口干不干净、有没有泄漏（clean）。
  本人说要搭美国出口、要用 ChatGPT、Grok 等海外订阅、问「现在能不能登录」「出口干不干净」，
  或云主机重启、换了网络之后使用。只管网络出口，不注册、不登录任何账号。
---

# 虚拟工位

本 skill 由 Agent 执行，陪本人装好并查验一个固定的美国出口。部署顺序是：美国云主机 → 美区 App Store ID → Tailscale → 美区 PayPal → 订阅 Muse、Grok、ChatGPT。买哪家云、怎么选套餐、Apple ID 与 PayPal 的要点、手机怎么用、二档装 Relay 的底座，见同目录 `README.md`，照它向本人说明。

## 规矩

- 云主机、Tailscale、Apple ID、PayPal、AI 账号都在本人名下。账号、密码、验证码、付款由本人输入，Agent 不经手、不记录。
- 云主机的 IP 没查合格，不往下做；条件见「日常」下的换 IP 条件。
- `station.py egress` 全部通过之前，告诉本人先别登录 AI 账号。
- 不替本人改系统时区和语言，不锁定 Tailscale 出口；需注意的项讲清后果，由本人定。
- 不关云主机的 SSH 密码登录，除非本人确认密钥登录已经能用。

## 本人的文件

| 文件 | 内容 |
|---|---|
| `user/station.json` | 登记的出口：节点名、节点编号、公网 IPv4、账号标签、登记时间。`station.py init` 生成，不进 Git |

## 步骤

1. **云主机买完**：请本人在浏览器打开 `https://ipinfo.io/<公网IPv4>` 和 `https://ip.net.coffee/ip/<公网IPv4>`，照 README 一档第 1 步判合格；不合格就换 IP 或退掉重买，换完重查。
2. **美区 Apple ID**：本人照 README 第 2 步注册，在手机的 App Store 换上它。
3. **云主机**（Ubuntu / Debian）：照 README 3.1 下载 `setup-exit-node.sh`，运行 `sudo sh setup-exit-node.sh`。它打印的 Tailscale 登录链接交本人打开登录；核对最后打印的公网 IPv4 就是第 1 步查过的那个。之后请本人在 Tailscale 管理后台批准这台作出口（Edit route settings → Use as exit node），并关掉密钥过期（Disable key expiry）。
4. **云主机**：`sudo sh setup-exit-node.sh --check`，7 项全过才往下走。
5. **电脑**：本人装好 Tailscale、登录同一账号、选这台作出口；手机从美区 App Store 装。
6. **电脑**（本目录下）：`python3 scripts/station.py init --ip <公网IPv4> --label <账号标签>`。报 IP 不符时，先查出口有没有选对，不要改 `--ip` 去迁就。
7. **电脑**：`python3 scripts/station.py clean`，再请本人打开它最后一行给的网址手查。没有「不通过」、手查也合格，才往下走。
8. **美区 PayPal、订阅**：每次注册、登录、订阅前先跑 `station.py egress`，退出码 0 才让本人操作；照 README 第 4、5 步。

## 日常

| 时机 | 命令 | 结果怎么用 |
|---|---|---|
| 每次登录 AI 账号前 | `python3 scripts/station.py egress` | 退出码 0 才能登录；1 先修出口 |
| 每周；换网络、重装云主机后 | `python3 scripts/station.py clean`，再手查它给的网址 | 1 或手查不合格先处理；2 逐项讲需注意与没读到的含义 |
| 云主机重启、重装后 | `sudo sh setup-exit-node.sh --check`（云主机） | 有 ❌ 就去掉 `--check` 重跑一次 |

换 IP 的条件：IPinfo 或 ip.net.coffee 任一定位不是美国，或 ip.net.coffee 信任评分低于 50，或上了黑名单。ip.net.coffee 没有公开接口条款，脚本不调用，只能由本人在网页上手查，不能省。

## 自检

`python3 -m unittest discover tests`：23 项测试，不联网，全过说明判断逻辑完好。
