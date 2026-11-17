---
name: virtual-workstation
description: >
  虚拟工位：把一台美国云主机开成 Tailscale 出口节点，让本人的电脑从这个固定出口用海外 AI 服务；
  登录 AI 账号前查出口对不对（egress），每周查出口干不干净、有没有泄漏（clean）。
  本人说要搭美国出口、要用 Claude / ChatGPT 等海外订阅、问「现在能不能登录」「出口干不干净」，
  或云主机重启、换了网络之后使用。只管网络出口，不注册、不登录任何账号。
---

# 虚拟工位

本 skill 由 Agent 执行，陪本人装好并查验一个固定的美国出口。买哪家云、怎么选套餐、手机怎么用、二档装 Relay 的底座，见同目录 `README.md`，照它向本人说明。

## 规矩

- 云主机、Tailscale、AI 账号都在本人名下。账号、密码、验证码、付款由本人输入，Agent 不经手、不记录。
- `station.py egress` 全部通过之前，告诉本人先别登录 AI 账号。
- 不替本人改系统时区和语言，不锁定 Tailscale 出口；需注意的项讲清后果，由本人定。
- 不关云主机的 SSH 密码登录，除非本人确认密钥登录已经能用。

## 本人的文件

| 文件 | 内容 |
|---|---|
| `user/station.json` | 登记的出口：节点名、节点编号、公网 IPv4、账号标签、登记时间。`station.py init` 生成，不进 Git |

## 步骤

1. **云主机**（Ubuntu / Debian）：照 `README.md` 一档第 2 步下载 `setup-exit-node.sh`，运行 `sudo sh setup-exit-node.sh`。它打印的 Tailscale 登录链接交本人打开登录；记下最后打印的公网 IPv4。之后请本人在 Tailscale 管理后台批准这台作出口（Edit route settings → Use as exit node），并关掉密钥过期（Disable key expiry）。
2. **云主机**：`sudo sh setup-exit-node.sh --check`，7 项全过才往下走。
3. **电脑**：本人装好 Tailscale、登录同一账号、选这台作出口。
4. **电脑**（本目录下）：`python3 scripts/station.py init --ip <第 1 步的公网 IPv4> --label <账号标签>`。报 IP 不符时，先查出口有没有选对，不要改 `--ip` 去迁就。
5. **电脑**：`python3 scripts/station.py clean`。没有「不通过」，本人才登录 AI 账号。

## 日常

| 时机 | 命令 | 结果怎么用 |
|---|---|---|
| 每次登录 AI 账号前 | `python3 scripts/station.py egress` | 退出码 0 才能登录；1 先修出口 |
| 每周；换网络、重装云主机后 | `python3 scripts/station.py clean` | 1 先处理；2 逐项讲需注意与没读到的含义 |
| 云主机重启、重装后 | `sudo sh setup-exit-node.sh --check`（云主机） | 有 ❌ 就去掉 `--check` 重跑一次 |

换 IP 的条件：「出口位置」两个来源都不是美国，或「信任分」低于 50，或「黑名单」不通过。只有一个来源定位不是美国，判需注意，可以先用。ip.net.coffee 的项没读到时，请本人打开 https://ip.net.coffee/claude/ 手查。

## 自检

`python3 -m unittest discover tests`：22 项测试，不联网，全过说明判断逻辑完好。
