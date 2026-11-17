# virtual-workstation —— 虚拟工位

**在国内用 Claude、ChatGPT 这类海外 AI 服务时，让电脑每次都从同一台你自己的美国云主机上网；登录 AI 账号前，先查一遍出口对不对、干不干净。**

虚拟工位由三件东西组成：

1. 一台美国云主机，当网络出口。
2. 电脑（手机可选）装 Tailscale（把几台设备连成一张私有网络的软件），选这台云主机作出口节点（Exit Node：替私网里其他设备上网的那台机器）。
3. `station.py`，登录 AI 账号前在电脑上跑一遍，查出口。

它只管网络出口，不代你注册、登录任何账号。出口干净只是降低被风控的可能，不保证 AI 服务不封号。

## 给谁用

- 在国内长期用海外 AI 订阅的人：想让账号每次都从同一个美国 IP 登录，不露出国内地址。
- 想让 Agent（能替人做事的 AI 助手）常驻一台美国云主机的人：同一台云主机再装 Relay（让 Agent 经飞书等聊天软件收发消息的中转程序），见下文「二档」。

云主机要每月付费、自己续费。只想偶尔打开一下海外网站的人，用不着它。

## 两种装法

- **自己做**：照下文「一档」的四步做。要装 Relay，再做「二档」。
- **大鸟陪着做**：买 Bigbird 的虚拟工位服务包，大鸟远程陪你走同样的步骤。云主机、Tailscale、AI 账号都开在你自己名下，费用你自己付；账号和密码由你本人输入，大鸟看着说，不经手。比自己做多一步：你的 SSH 密钥（免密码登录云主机用的一对密钥文件）配好之后，关掉云主机的 SSH 密码登录，只留密钥。脚本不做这一步，因为密钥没配好就关，会把自己锁在门外。二档的底座由大鸟装。

## 一档：只要网络出口

### 1. 买一台美国云主机

首选腾讯云轻量应用服务器，地域选「硅谷」，镜像选 Ubuntu 24.04 或更新：国内能付款，控制台是中文，便宜；大鸟实测不影响 Claude 等订阅。

| 用途 | 套餐 | 配置 | 月费 |
|---|---|---|---|
| 推荐：只当出口 | 锐驰型 | 2 核 2G，40G SSD，峰值 200Mbps，流量不限 | 40 元 |
| 省钱：只当出口 | 入门型 | 2 核 2G，40G SSD，峰值 20Mbps，每月 512GB | 30 元 |
| 二档：还要装 Relay | 锐驰型 | 2 核 4G，60G SSD，峰值 200Mbps，流量不限 | 60 元 |

月费是腾讯云官网 2026-11-11 更新的标准价，常有首年活动价，以下单页为准。只当出口，2G 内存足够：大鸟的同款出口整机只用约 0.5G。推荐锐驰型，是因为流量不限；入门型用完每月 512GB 后，硅谷每 GB 收 0.5 元。

**腾讯云 IP 要知道两点：**

1. **定位可能被认成新加坡。** 大鸟的两台腾讯云硅谷出口里有一台，IPinfo 定位美国，ip.net.coffee 定位新加坡。ip.net.coffee 按整个网段判断，结果最长缓存一年。
2. **网络归属是腾讯。** 网站看得出这是腾讯云的机器，有的服务会因此提高风控。大鸟实测 Claude 等订阅没受影响，但这只代表他的账号和用法。

所以装好后先跑一次 `station.py clean`（第 4 步）。出现下面任一情况就换 IP：控制台能更换公网 IP 就直接换；不能，就按腾讯云的退款规则退掉，重新买一台。

- 「出口位置」两个来源都不是美国。
- 「信任分」低于 50。
- 「黑名单」不通过。

只有一个来源认成新加坡、另一个是美国，判「需注意」，可以先用着。

**备选：Oracle Cloud 美国地域。** 大鸟的 Oracle 凤凰城出口，各定位来源都在美国，信任分 79。代价是开户要海外信用卡，免费实例常常抢不到、要按付费实例准备，控制台是英文。

**不要选**新加坡、香港等非美国地域：大鸟的一台腾讯云新加坡出口，信任分只有 43，还被标了代理和滥用。

### 2. 把云主机开成出口

在腾讯云控制台点「登录」，或用 SSH 进入云主机，执行：

```bash
curl -fsSLO https://raw.githubusercontent.com/bigbird-online/coevolution/main/virtual-workstation/scripts/setup-exit-node.sh
sudo sh setup-exit-node.sh
```

腾讯云的 Ubuntu 镜像以 `ubuntu` 用户登录，所以前面加 `sudo`（以管理员身份运行）。脚本做五件事，重复运行也安全：

1. 装 Tailscale，以及它用到的 curl、ethtool、iptables（已装的会升到最新版）。
2. 打开 IPv4/IPv6 转发，TCP 拥塞控制改用 BBR（一种更快的拥塞控制算法）。
3. 开机时打开网卡的 UDP GRO 转发（Tailscale 官方建议的提速项），并把经隧道的 TCP 包长夹到路径上限，防止大包卡死。
4. 以出口节点身份加入你的 Tailscale：它打印一个链接，在浏览器里打开，用你自己的 Tailscale 账号登录，没有账号就在这一步注册。
5. 自查，打印这台云主机的公网 IPv4。**记下这个 IP**，第 4 步要用。

脚本不装代理、不开新端口、不改 SSH 和防火墙。腾讯云控制台的防火墙也不用为 Tailscale 放行端口：Tailscale 从云主机主动向外连接，大鸟的腾讯云出口实测能直连。

跑完后打开 Tailscale 管理后台 https://login.tailscale.com/admin/machines ，在这台机器的「…」菜单里：

- 选「Edit route settings」，勾选「Use as exit node」，批准它作出口。
- 选「Disable key expiry」，关掉密钥过期，免得半年后掉线、要重新登录。

### 3. 电脑和手机装 Tailscale

- **Mac、Windows**：从 https://tailscale.com/download 下载官网安装包，不经 App Store，不需要 Apple ID。用同一个 Tailscale 账号登录，在菜单的「Exit Node」里选这台云主机。
- **iPhone、iPad**：只能从 App Store 装，而中国区 App Store 搜不到 Tailscale（美国区、香港区有），所以要一个非中国区的 Apple ID。
- **手机用时再开出口**：出口一开，整台手机都从美国上网，微信等国内应用也绕到美国，变慢，还可能触发它们的风控。iOS 版不能按应用分流，用 AI 时打开出口，用完关掉。
- **出口不锁定**：Tailscale 可以用系统策略把出口锁死、不许断开，但那样国内应用也只能一直绕美国。这里不锁，改为每次登录 AI 账号前跑一次 `station.py egress`。

### 4. 登记出口

电脑上要有 Python 3.8 以上。macOS 自带；第一次运行 `python3` 或 `git` 时，它会提示安装「命令行开发者工具」，点安装即可。Windows 从 https://www.python.org 下载安装，安装时勾选「Add python.exe to PATH」，下文命令里的 `python3` 换成 `python`。

取下本目录：

```bash
git clone https://github.com/bigbird-online/coevolution.git
cd coevolution/virtual-workstation
```

不用 git 的，在 GitHub 仓库页点「Code → Download ZIP」，解压后进 `virtual-workstation` 目录。

先在 Tailscale 里选好这台云主机作出口，再登记（把 `203.0.113.10` 换成第 2 步记下的公网 IPv4）：

```bash
python3 scripts/station.py init --ip 203.0.113.10 --label "我的 Claude"
```

`init` 先确认 Tailscale 当前的出口在线、本机看到的公网 IPv4 就是你填的那个，再把出口的名字、编号和 IP 写进 `user/station.json`。`--label` 只写账号标签，不写账号和密码；`user/` 不进 Git。IP 对不上时它拒绝登记，并打印本机实际的出口 IP。

接着跑一次 `python3 scripts/station.py clean`。没有「不通过」，就可以登录 AI 账号；之后这个账号只在这个出口登录。

## 验证装成

| 命令 | 在哪跑 | 什么时候跑 | 查什么 |
|---|---|---|---|
| `python3 scripts/station.py egress` | 电脑 | 每次登录 AI 账号前 | 当前出口是不是登记的那台、在不在线；两个来源看到的公网 IPv4 是否都等于登记的 IP |
| `python3 scripts/station.py clean` | 电脑 | 装好时；之后每周一次，换了网络、重装了云主机也跑 | 先跑一遍 egress；再查出口的定位、信任分、VPN/代理/Tor 标记、滥用记录、两个黑名单；再查本机有没有绕开出口的路：IPv6、UDP（网页 WebRTC 走的路）、DNS 解析服务器在哪国，以及时区、系统语言 |
| `sudo sh setup-exit-node.sh --check` | 云主机 | 云主机重启、重装后 | 只读核对 7 项：Tailscale 已装、IPv4 转发、IPv6 转发、BBR、UDP GRO 转发、MSS 夹紧、已声明为出口节点；再打印公网 IPv4 |

每项结果只会是「通过」「需注意」「不通过」「没读到」之一，没读到不算通过。`station.py` 的退出码：0 全部通过；1 有不通过；2 没有不通过，但有需注意或没读到。

怎么判：

- **egress 全部通过，才登录 AI 账号。** Tailscale 断开、出口换了、出口离线，都会判不通过，这时流量可能正从本地宽带出去，先别登录。
- **clean 有不通过，先处理再登录。** 出口的定位、信任分或黑名单出现第 1 步说的情况，就换 IP。
- **时区和语言**：中文系统上通常判「需注意」，网站能看到它们与美国出口不一致。改不改由你定；大鸟没有改。

定位与信任数据来自 IPinfo（ipinfo.io）和 ip.net.coffee。ip.net.coffee 没有公开接口条款，接口可能随时变；它读不到时，信任分等三项判「没读到」，可以打开 https://ip.net.coffee/claude/ 在网页上手查。

## 二档：这台云主机还要装 Relay

云主机选 2 核 4G。Relay 空闲时约占 40MB 内存，每个同时运行的 Agent 会话要按几百 MB 留。底座照大鸟在 Oracle 上的常驻节点做（Ubuntu 24.04，专用账户跑 Relay，Relay 作该账户的 systemd 用户服务）：

| 底座 | 做法 |
|---|---|
| 系统账户 | 建一个专用的非 root 账户跑 Relay 和 Agent；root 只接受密钥登录，关掉 SSH 密码登录 |
| 工具链 | node 22、git、python3，加 Relay 所用的 Agent 命令行（这次交付配 Codex） |
| 开机自启 | `loginctl enable-linger <账户>`，让该账户的 systemd 用户服务开机就跑，不必有人登录 |
| 磁盘与日志 | `/etc/systemd/journald.conf` 里设 `SystemMaxUse=500M`；开 unattended-upgrades，自动装安全更新 |

大鸟的节点还有出口代理、CI Runner（自动化测试与构建程序）、浏览器内核和家里的监控探针，这里都不装。Relay 本身怎么装、它的 systemd 单元文件、每天怎样把磁盘用量发给自己，见 Relay 的安装说明（由 PULSE 项目维护，尚未发布）。

## 以大鸟为例走一遍

大鸟自己的虚拟工位：工作在家里的 Mac 上，网络从一台腾讯云硅谷的云主机出去，一个 Claude 账号只在这一个出口登录。他和本目录有两处不同：他的 Mac 用系统策略把出口锁死，不许断开；家里的巡检每天查四次出口的信任分与泄漏，结果过 13 小时作废。本目录不锁出口，改为登录前跑 `egress`、每周跑 `clean`。

2026-11-17，照本文在一台腾讯云轻量硅谷（2 核 2G，Ubuntu 26.04）上走了一遍。为了不动大鸟日常用的 Mac，「电脑」这一侧由这台云主机代演：它暂时不当出口，改选另一台腾讯云硅谷出口，跑 `station.py`。下面的输出里，IP 换成了文档专用地址，节点名换成了 `us-exit-2`。

**云主机上**，`sudo sh setup-exit-node.sh` 跑完五步，自查 7 项全过；再跑 `--check`，结果相同：

```text
出口节点自查（网卡 eth0）
  ✅ Tailscale 已安装：1.104.7
  ✅ IPv4 转发已开
  ✅ IPv6 转发已开
  ✅ 拥塞控制 BBR
  ✅ UDP GRO 转发已开
  ✅ MSS 夹紧已生效
  ✅ 已声明为出口节点（还需在 Tailscale 管理后台批准）
  这台的公网 IPv4：203.0.113.10
结论：配好了。
```

这台的出口在网站眼里：IPinfo 定位美国，ip.net.coffee 定位新加坡，信任分 76，没有 VPN、代理、滥用标记，不在黑名单上。这正是第 1 步说的「需注意」，可以先用着。

**电脑这一侧**，先故意填错 IP，`init` 拒绝登记：

```text
本机现在从 203.0.113.20 出去，与 --ip 203.0.113.99 不符；先核对出口
```

填对之后登记成功，`egress` 全过：

```text
已登记：出口 us-exit-2，公网 IPv4 203.0.113.20，写入 …/virtual-workstation/user/station.json
出口自查（登记：us-exit-2 / 203.0.113.20）
  ✅ 通过  出口节点：经 us-exit-2
  ✅ 通过  公网 IPv4：2 个来源都看到 203.0.113.20
结论：出口正确，可以登录。
```

`clean` 没有不通过，退出码 2：

```text
出口干净度自查（us-exit-2 / 203.0.113.20，2026-11-17 16:43）
  ✅ 通过  出口节点：经 us-exit-2
  ✅ 通过  公网 IPv4：2 个来源都看到 203.0.113.20
  ✅ 通过  出口位置：两个来源都定位在 US；网络归属 AS132203 Tencent Building, Kejizhongyi Avenue
  ✅ 通过  信任分：80/100，50 以下有风险；机房 IP（云服务器都是，不等于 VPN）
  ✅ 通过  VPN/代理标记：没有被标为 VPN、代理或 Tor
  ✅ 通过  滥用记录：没有滥用记录
  ✅ 通过  黑名单：2 个名单都没有
  ✅ 通过  IPv6：IPv6 也在 US
  ✅ 通过  UDP（WebRTC）：UDP 也经出口
  ✅ 通过  DNS 定位：解析服务器在 US，与出口同国
  ⚠️ 需注意  时区：系统时区属于 CN，出口在 US；网站能看到时区
  ❔ 没读到  系统语言：认不出系统语言 C 的地区
结论：没有不通过；需注意的项看说明自行决定，没读到的稍后重跑。
定位数据来自 IPinfo（ipinfo.io）与 ip.net.coffee。
```

系统语言没读到，是因为这台云主机没设语言；在中文 Mac 上会读到 `zh-Hans-CN` 一类，判「需注意」。

最后关掉出口再跑 `egress`，两项都不通过，提示先别登录：

```text
出口自查（登记：us-exit-2 / 203.0.113.20）
  ❌ 不通过  出口节点：没有选出口：流量从本地宽带直接出去
  ❌ 不通过  公网 IPv4：网站看到的是 203.0.113.10，不是登记的 203.0.113.20
结论：先别登录 AI 账号，按上面的提示处理后重跑。
```

## 升级

`user/` 不在仓库里，更新不会动它。电脑上在原处 `git pull`；用 ZIP 装的，下载新的 ZIP，把旧目录里的 `user/` 拷进新目录。云主机上重新下载 `setup-exit-node.sh` 再跑一次。

## 目录一览

```
README.md                    本文
SKILL.md                     给 Agent 的安装与自查步骤
scripts/setup-exit-node.sh   在云主机上（root）开成出口节点；--check 只读核对
scripts/station.py           在电脑上：init 登记出口，egress 查出口对不对，clean 查干不干净、有没有泄漏（只用 Python 标准库）
tests/test_station.py        station.py 判断逻辑的 22 项测试，不联网：python3 -m unittest discover tests
user/station.json            init 生成的登记——不入库
```
