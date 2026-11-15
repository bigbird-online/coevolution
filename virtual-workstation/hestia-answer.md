# 虚拟工位搭建做法（Hestia）

> 2026-11-15 · Hestia（大鸟家庭基建的 Agent）· 给 README 作素材。来源标记：实测＝在大鸟的出口或电脑上读到；官网＝厂商公开页面；待核＝没有核实。

## 结论

- 一档虚拟工位就是三件事：
  1. 一台美国云主机当出口。
  2. 电脑（手机可选）装 Tailscale（把几台设备连成私网的软件），选这台作出口。
  3. 登录 AI 账号前自查出口。
- 首选腾讯云轻量应用服务器（美国硅谷）：国内付款、好管理、便宜，大鸟实测不影响 Claude 等订阅。Oracle Cloud 美国地域作备选。
- 两个脚本：
  - `scripts/setup-exit-node.sh`：在云主机上把 Ubuntu/Debian 开成出口节点。
  - `scripts/station.py`：在电脑上运行，登记出口、查出口对不对、查干不干净。
- 二档在同一台云主机上再装 Relay（让 Agent 经聊天软件收发消息的中转程序），底座四件：专用账户、标准工具链、开机自启、磁盘与日志上限。

## 一、一档·网络出口

### 1. 开一台腾讯云美国云主机

**买什么**（腾讯云轻量应用服务器，地域选「硅谷」，镜像选 Ubuntu 24.04 或更新；价格为官网 2026-11-11 更新的标准月价，常有首年活动价，以下单页为准）：

| 用途 | 套餐 | 配置 | 月费 |
|---|---|---|---|
| 推荐：只当出口 | 锐驰型 | 2 核 2G，40G SSD，峰值 200Mbps，流量不限 | 40 元 |
| 省钱：只当出口 | 入门型 | 2 核 2G，40G SSD，峰值 20Mbps，每月 512GB | 30 元 |
| 二档：还要装 Relay、跑 Agent | 锐驰型 | 2 核 4G，60G SSD，峰值 200Mbps，流量不限 | 60 元 |

- 只当出口时，2G 内存绰绰有余：大鸟的同款出口整机只用约 0.5G（实测）。
- 推荐锐驰型，是因为流量不限，看视频、下大文件不用操心超额。入门型超出流量包后，硅谷每 GB 收 0.5 元（官网）。

**腾讯云 IP 的两个顾虑，以及怎么避开**

大鸟在用的两台腾讯云硅谷出口实测：
- 信任分 80 和 76。信任分来自 ip.net.coffee，0–100 越高越好，50 以下有风险。
- 都被认作机房 IP（云主机都是，不等于被标成 VPN），没有被标 VPN、代理或 Tor。
- 不在两个垃圾邮件黑名单上。

要知道的有两点：

1. **定位可能被认成新加坡。** 其中一台，IPinfo 定位美国，ip.net.coffee 定位新加坡。ip.net.coffee 按整个网段判断、结果最长缓存一年，网段的旧登记会一直带着（推断）。
2. **网络归属是腾讯。** 网站能看出这是腾讯云的机器，有的服务会因此提高风控。大鸟实测 Claude 等订阅没有受影响，但这只代表他的账号和用法。

避开办法：开机装好后先跑一次 `station.py clean`（见第 4 节）。有下面任一情况，就换一个 IP：控制台能更换公网 IP 就直接换，不能就按腾讯云的退款规则退掉、重新买一台。
- 「出口位置」两个来源都不是美国。
- 「信任分」低于 50。
- 「黑名单」不通过。

只有一个来源认成新加坡、另一个是美国，判「需注意」，可以先用着。

**备选：Oracle Cloud 美国地域。**
- 好处：大鸟的 Oracle 凤凰城出口三个来源都定位美国，信任分 79（实测）。
- 代价：开户要海外信用卡；免费实例常常抢不到，要按付费实例准备；控制台是英文。

**不要选**新加坡、香港等非美国地域。大鸟的一台腾讯云新加坡出口信任分只有 43，还被标了代理和滥用（实测）。

### 2. 把云主机开成出口

在腾讯云控制台用「登录」按钮或 SSH 进入云主机：

```bash
curl -fsSLO https://raw.githubusercontent.com/bigbird-online/coevolution/main/virtual-workstation/scripts/setup-exit-node.sh
sudo sh setup-exit-node.sh
```

脚本做五件事，可以重复运行：
1. 装 Tailscale。
2. 打开 IPv4/IPv6 转发，改用 BBR（一种更快的 TCP 拥塞控制）。
3. 开机时打开网卡的 UDP GRO 转发（Tailscale 官方建议的提速项），并把经隧道的 TCP 包长夹到路径上限，防止大包卡死。
4. 以出口节点身份加入你的 Tailscale：它会打印一个链接，用你自己的 Tailscale 账号登录。
5. 自查，打印这台的公网 IPv4。

它不装代理、不开新端口、不改 SSH 和防火墙。腾讯云控制台的防火墙也不用为 Tailscale 放行端口：大鸟的腾讯出口实测是直连。

之后在 Tailscale 管理后台（login.tailscale.com/admin/machines）批准这台作出口（Edit route settings → Use as exit node），并关掉密钥过期（Disable key expiry），免得半年后掉线。

随时可以用 `sudo sh setup-exit-node.sh --check` 只读核对 7 项配置。大鸟的两台出口实测 7 项全过；在一台全新云主机上的完整安装待 README 走查时实跑。

- **买家自己做**：照上面三步。
- **大鸟陪着做**：再加一步，关掉 SSH 密码登录，只留密钥。脚本不做这一步，因为买家可能还没配好密钥，做了会把自己锁在门外。

### 3. 电脑和手机装 Tailscale

- **Mac、Windows**：从 https://tailscale.com/download 下载官网安装包，不经 App Store，不涉及 Apple ID。装好后用同一个 Tailscale 账号登录，在菜单里选这台云主机作出口（Exit Node）。
- **iPhone、iPad**：只能走 App Store，而中国区 App Store 没有 Tailscale。实测 App Store 查询：中国区 0 条，美国区、香港区各 1 条。所以需要一个非中国区的 Apple ID。
- **手机建议用时再开**：整机出口会让微信等国内应用也绕到美国，变慢，还可能触发国内应用的风控。iOS 版不能按应用分流，用 AI 时打开出口，用完关掉。
- **不锁定出口**：Tailscale 可以用系统策略把出口锁死、不许断开（大鸟的 Mac 就这样），但那样国内应用也只能一直绕美国。买家版不锁，改为每次登录 AI 账号前跑一次 `station.py egress`。

### 4. 自查

电脑上要有 Python 3.8 以上：macOS 自带，Windows 从 python.org 装。

```bash
python3 scripts/station.py init --ip <云主机打印的公网 IPv4> --label "我的 Claude"   # 装好后一次
python3 scripts/station.py egress   # 每次登录 AI 账号前
python3 scripts/station.py clean    # 每周一次，或换了网络、重装了云主机之后
```

- **init**：确认 Tailscale 当前的出口在线、本机看到的公网 IPv4 就是你填的那个，再把出口的名字、编号和 IP 写进 `user/station.json`。只写账号标签，不写账号和密码。
- **egress**：查三件事：出口是不是登记的那台、在不在线、两个来源看到的公网 IPv4 是否都对。全过才说「可以登录」。
- **clean**：先跑一遍 egress，再查两边。
  - 出口：定位、信任分、VPN/代理/Tor 标记、滥用记录、两个黑名单。
  - 本机：IPv6 有没有绕开出口、UDP（网页 WebRTC 走的路）露出的地址、DNS 解析服务器在哪国、时区、系统语言。

每项只会是 通过 / 需注意 / 不通过 / 没读到 之一，没读到不算通过。退出码 0 全过，1 有不通过，2 只有需注意或没读到。

在中文系统上，时区和语言通常判「需注意」：网站能看到它们与美国出口不一致。改不改由你定；大鸟没有改。

定位与信任数据来自 IPinfo（ipinfo.io）和 ip.net.coffee。没有用 ip-api：它的免费接口限非商业用途。

## 二、二档·在出口上装 Relay

照大鸟在 Oracle 上的常驻节点做（实测：Ubuntu 24.04，专用账户跑 Relay，Relay 是 systemd 用户服务）：

| 底座 | 做法 | 买家版 |
|---|---|---|
| 系统账户 | 建一个专用非 root 账户跑 Relay 和 Agent；root 只接受密钥 SSH，关密码登录 | 保留 |
| 标准工具链 | node 22、git、python3、Claude Code；大鸟的节点另有 gh、uv、codex | 只装 node、git、python3、Claude Code |
| 开机自启 | `loginctl enable-linger <账户>`，Relay 作该账户的 systemd 用户服务 | 保留；Relay 的单元文件随 Relay 安装说明给出 |
| 磁盘与日志 | journald 设上限（`/etc/systemd/journald.conf` 里 `SystemMaxUse=500M`），开 unattended-upgrades 自动装安全更新 | 保留 |
| 监控 | 大鸟家里的探针定时采集资源，异常进告警链 | 省掉；改为每天查一次磁盘，经 Relay 发给自己（待 Relay 安装说明确定推送方式） |
| 出口代理、CI Runner、浏览器内核 | 给开发工具和自动化用 | 省掉 |

内存：Relay 空闲时约 40MB（实测）；每个同时运行的 Claude Code 会话要按几百 MB 留，所以二档选 2 核 4G。

## 三、文件

| 文件 | 在哪运行 | 做什么 |
|---|---|---|
| `scripts/setup-exit-node.sh` | 云主机（root） | 开成出口节点；`--check` 只读核对 |
| `scripts/station.py` | 电脑 | `init` 登记出口；`egress` 出口对不对；`clean` 出口干不干净、有没有泄漏 |
| `tests/test_station.py` | 电脑 | 判断逻辑的 22 项测试，不联网：`python3 -m unittest discover tests` |
| `user/station.json` | 电脑 | `init` 生成，不进 Git |
