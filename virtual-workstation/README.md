# virtual-workstation —— 虚拟工位

**在国内用 ChatGPT、Grok 这类海外 AI 服务时，让电脑每次都从同一台你自己的美国云主机上网；登录 AI 账号前，先查一遍出口对不对、干不干净。**

虚拟工位由三件东西组成：

1. 一台美国云主机，当网络出口。
2. 电脑（手机可选）装 Tailscale（把几台设备连成一张私有网络的软件），选这台云主机作出口节点（Exit Node：替私网里其他设备上网的那台机器）。
3. `station.py`，登录 AI 账号前在电脑上跑一遍，查出口。

按这个顺序部署，前一步做完再做下一步：

1. 买美国云主机（VPS，按月租用的云服务器），先查 IP 定位
2. 注册美区 App Store ID（国家或地区选美国的 Apple ID）
3. 装 Tailscale，把云主机开成出口
4. 注册美区 PayPal
5. 订阅 Muse、Grok、ChatGPT

**最容易踩的坑在第 1 步：标着「美国」的云主机，IP 可能被定位成别国。买完先查，不是美国就换，再往下做。**

脚本只管网络出口。Apple ID、PayPal 和 AI 订阅由你本人注册、付款，本文只写要点。出口干净只是降低被风控的可能，不保证 AI 服务不封号。

## 给谁用

- 在国内长期用海外 AI 订阅的人：想让账号每次都从同一个美国 IP 登录，不露出国内地址。
- 想让 Agent（能替人做事的 AI 助手）常驻一台美国云主机的人：同一台云主机再装 Relay（让 Agent 经飞书等聊天软件收发消息的中转程序），见下文「二档」。

云主机要每月付费、自己续费。只想偶尔打开一下海外网站的人，用不着它。

## 两种装法

- **自己做**：照下文「一档」的五步做。要装 Relay，再做「二档」。
- **大鸟陪着做**：买 Bigbird 的虚拟工位服务包，大鸟远程陪你走同样的步骤。云主机、Tailscale、Apple ID、PayPal 和 AI 账号都开在你自己名下，费用你自己付；账号和密码由你本人输入，大鸟看着说，不经手。比自己做多一步：你的 SSH 密钥（免密码登录云主机用的一对密钥文件）配好之后，关掉云主机的 SSH 密码登录，只留密钥。脚本不做这一步，因为密钥没配好就关，会把自己锁在门外。二档的底座由大鸟装。

## 一档：五步部署

### 第 1 步：买一台美国云主机，先查 IP 定位

> **坑：标着「美国」的云主机，IP 可能被定位成别国。** 大鸟在腾讯云买的一台硅谷轻量服务器，IPinfo 定位美国，ip.net.coffee 却定位新加坡：不合格，不能当出口。网站各用各的定位库，有一家认成别国，就有服务会把你当成在别国登录。ip.net.coffee 按整个网段判断，结果最长缓存一年，等不得，只能换 IP。

**买完第一件事是查定位，合格了才做第 2 步：**

1. 在云主机控制台的实例详情里，找到这台的公网 IPv4。
2. 浏览器打开 `https://ipinfo.io/<公网IPv4>`，看国家是不是 United States。
3. 浏览器打开 `https://ip.net.coffee/ip/<公网IPv4>`，等实时检测加载完，看「国家/地区」是不是 United States (US)、「信任评分」是否不低于 50、黑名单有没有记录。

两个来源都定位美国、信任评分不低于 50、没有黑名单记录，才算合格。任一项不合格：控制台能更换公网 IP，就换一个，从头再查；不能换，就按云商的退款规则退掉，重新买一台。这一步必做：之后的 `station.py clean` 只能自动查 IPinfo，查不了 ip.net.coffee。

首选腾讯云轻量应用服务器，地域选「硅谷」，镜像选 Ubuntu 24.04 或更新：国内能付款，控制台是中文，便宜；大鸟实测不影响他在用的海外 AI 订阅。

但腾讯云要谨慎尝试：大鸟的两台硅谷主机，一台合格，一台被定位成新加坡。先只买一个月，不选包年或长期预付；照上面查定位，合格了再续费。不合格先换 IP，换过仍不合格就退掉，改选下面的 Oracle。

| 用途 | 套餐 | 配置 | 月费 |
|---|---|---|---|
| 推荐：只当出口 | 锐驰型 | 2 核 2G，40G SSD，峰值 200Mbps，流量不限 | 40 元 |
| 省钱：只当出口 | 入门型 | 2 核 2G，40G SSD，峰值 20Mbps，每月 512GB | 30 元 |
| 二档：还要装 Relay | 锐驰型 | 2 核 4G，60G SSD，峰值 200Mbps，流量不限 | 60 元 |

月费是腾讯云官网 2026-11-11 更新的标准价，常有首年活动价，以下单页为准。只当出口，2G 内存足够：大鸟的同款出口整机只用约 0.5G。推荐锐驰型，是因为流量不限；入门型用完每月 512GB 后，硅谷每 GB 收 0.5 元。

腾讯云的 IP 还有一点要知道：网络归属是腾讯，网站看得出这是腾讯云的机器，有的服务会因此提高风控。上面说的大鸟实测，只代表他的账号和用法。

**备选：Oracle Cloud 美国地域。** 大鸟的 Oracle 凤凰城出口，各定位来源都在美国，信任分 79。代价是开户要海外信用卡，免费实例常常抢不到、要按付费实例准备，控制台是英文。

**不要选**新加坡、香港等非美国地域：大鸟的一台腾讯云新加坡出口，信任分只有 43，还被标了代理和滥用。

### 第 2 步：注册美区 App Store ID

为什么要：iPhone、iPad 上的 Tailscale 只能从 App Store 装，中国区 App Store 搜不到它（美国区有）；第 5 步在 iPhone 上订阅，也用这个 Apple ID。

做法：用一个没注册过 Apple ID 的邮箱新建 Apple ID，国家或地区选「美国」；在手机「设置 → 你的名字 → 媒体与购买项目」里退出原账号，换它登录，只换 App Store 的账号，iCloud 不动。要填哪些信息，以 Apple 的注册页为准。

### 第 3 步：装 Tailscale，把云主机开成出口

#### 3.1 云主机开成出口

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
5. 自查，打印这台云主机的公网 IPv4。核对它就是第 1 步查过的那个 IP，3.3 要用。

脚本不装代理、不开新端口、不改 SSH 和防火墙。腾讯云控制台的防火墙也不用为 Tailscale 放行端口：Tailscale 从云主机主动向外连接，大鸟的腾讯云出口实测能直连。

跑完后打开 Tailscale 管理后台 https://login.tailscale.com/admin/machines ，在这台机器的「…」菜单里：

- 选「Edit route settings」，勾选「Use as exit node」，批准它作出口。
- 选「Disable key expiry」，关掉密钥过期，免得半年后掉线、要重新登录。

#### 3.2 电脑和手机装 Tailscale

- **Mac、Windows**：从 https://tailscale.com/download 下载官网安装包，不经 App Store。用同一个 Tailscale 账号登录，在菜单的「Exit Node」里选这台云主机。
- **iPhone、iPad**：用第 2 步的美区 Apple ID 登录 App Store，搜 Tailscale 安装，用同一个 Tailscale 账号登录。
- **手机用时再开出口**：出口一开，整台手机都从美国上网，微信等国内应用也绕到美国，变慢，还可能触发它们的风控。iOS 版不能按应用分流，用 AI 时打开出口，用完关掉。
- **出口不锁定**：Tailscale 可以用系统策略把出口锁死、不许断开，但那样国内应用也只能一直绕美国。这里不锁，改为每次登录 AI 账号前跑一次 `station.py egress`。

#### 3.3 登记出口并自查

电脑上要有 Python 3.8 以上。macOS 自带；第一次运行 `python3` 或 `git` 时，它会提示安装「命令行开发者工具」，点安装即可。Windows 从 https://www.python.org 下载安装，安装时勾选「Add python.exe to PATH」，下文命令里的 `python3` 换成 `python`。

取下本目录：

```bash
git clone https://github.com/bigbird-online/coevolution.git
cd coevolution/virtual-workstation
```

不用 git 的，在 GitHub 仓库页点「Code → Download ZIP」，解压后进 `virtual-workstation` 目录。

先在 Tailscale 里选好这台云主机作出口，再登记（把 `203.0.113.10` 换成这台的公网 IPv4）：

```bash
python3 scripts/station.py init --ip 203.0.113.10 --label "我的 ChatGPT"
```

`init` 先确认 Tailscale 当前的出口在线、本机看到的公网 IPv4 就是你填的那个，再把出口的名字、编号和 IP 写进 `user/station.json`。`--label` 只写账号标签，不写账号和密码；`user/` 不进 Git。IP 对不上时它拒绝登记，并打印本机实际的出口 IP。

接着跑一次 `python3 scripts/station.py clean`，再照它最后一行打印的网址，在 ip.net.coffee 网页上手查一遍。没有「不通过」、手查也合格，再做第 4 步。

### 第 4 步：注册美区 PayPal

为什么要：在美区 App Store 里订阅，付款方式要是美区的；美区 PayPal 能绑到第 2 步的 Apple ID 上。

做法：先跑 `python3 scripts/station.py egress`，全部通过后，在 PayPal 美国站（https://www.paypal.com/us）注册个人账户，注册时就从美国出口上网；再在 Apple ID 的付款方式里加上它。要准备哪些资料，以 PayPal 的注册页为准。

### 第 5 步：订阅 Muse、Grok、ChatGPT

为什么要：Muse（Meta 出品）、Grok（xAI 出品）、ChatGPT（OpenAI 出品）是 Personal Agent（替你干活的个人 AI 助手）要用的 AI 服务；账号从注册起就只在这个出口登录，网站每次看到的都是同一个美国 IP。Claude 不在其中，留给以后的 Personal Agent Plus 服务包。

做法：每次注册、登录、订阅前先跑 `station.py egress`，全部通过再操作。在 iPhone 上经 App Store 订阅，付款选第 4 步的 PayPal；在网页上订阅，付款方式以各家结账页为准。

## 验证装成

| 命令 | 在哪跑 | 什么时候跑 | 查什么 |
|---|---|---|---|
| `python3 scripts/station.py egress` | 电脑 | 每次登录 AI 账号前 | 当前出口是不是登记的那台、在不在线；两个来源看到的公网 IPv4 是否都等于登记的 IP |
| `python3 scripts/station.py clean` | 电脑 | 装好时；之后每周一次，换了网络、重装了云主机也跑 | 先跑一遍 egress；再查出口的 IPinfo 定位、两个黑名单；再查本机有没有绕开出口的路：IPv6、UDP（网页 WebRTC 走的路）、DNS 解析服务器在哪国，以及时区、系统语言 |
| 浏览器打开 `https://ip.net.coffee/ip/<公网IPv4>` | 任一设备 | 买完云主机时；之后每次跑完 clean | 国家/地区、信任评分、黑名单 |
| `sudo sh setup-exit-node.sh --check` | 云主机 | 云主机重启、重装后 | 只读核对 7 项：Tailscale 已装、IPv4 转发、IPv6 转发、BBR、UDP GRO 转发、MSS 夹紧、已声明为出口节点；再打印公网 IPv4 |

每项结果只会是「通过」「需注意」「不通过」「没读到」之一，没读到不算通过。`station.py` 的退出码：0 全部通过；1 有不通过；2 没有不通过，但有需注意或没读到。

怎么判：

- **egress 全部通过，才登录 AI 账号。** Tailscale 断开、出口换了、出口离线，都会判不通过，这时流量可能正从本地宽带出去，先别登录。
- **clean 有不通过，或网页手查不合格，先处理再登录。** 出口定位不是美国、信任评分低于 50、上了黑名单，都照第 1 步换 IP。
- **时区和语言**：中文系统上通常判「需注意」，网站能看到它们与美国出口不一致。改不改由你定；大鸟没有改。

`station.py` 读公网 IP 用 ipify、icanhazip，读定位用 IPinfo，这几家都公开提供给程序调用。ip.net.coffee 没有公开的接口条款，脚本不调用它，所以它的定位和信任评分只能在网页上手查，手查不能省。

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

大鸟自己的虚拟工位：工作在家里的 Mac 上，网络从一台腾讯云硅谷的云主机出去，他的 AI 账号只在这一个出口登录。他和本目录有两处不同：他的 Mac 用系统策略把出口锁死，不许断开；家里的巡检每天查四次出口的信任分与泄漏，结果过 13 小时作废。本目录不锁出口，改为登录前跑 `egress`、每周跑 `clean`。

2026-11-17，照本文在一台腾讯云轻量硅谷（2 核 2G，Ubuntu 26.04）上走了一遍。这台正是第 1 步说的反例：IPinfo 定位美国，ip.net.coffee 定位新加坡，不合格，不能当出口，只拿来验证脚本。云主机一侧照常开成出口；为了不动大鸟日常用的 Mac，「电脑」一侧也由这台代演：它暂时不当出口，改选大鸟另一台合格的腾讯云硅谷出口，跑 `station.py`。下面的输出里，IP 换成了文档专用地址，合格的那台出口名换成了 `us-exit-2`。

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

`clean` 没有不通过，退出码 2：IPinfo 定位美国，两个黑名单都没有，IPv6、UDP、DNS 都没有绕开出口；时区判「需注意」（系统时区属于中国，出口在美国）；系统语言「没读到」，因为这台云主机没设语言，在中文 Mac 上会读到 `zh-Hans-CN` 一类，判「需注意」。在 ip.net.coffee 网页上查这台出口：国家/地区 United States (US)，信任评分 78，12 家黑名单都没有记录，合格。

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
tests/test_station.py        station.py 判断逻辑的 23 项测试，不联网：python3 -m unittest discover tests
user/station.json            init 生成的登记——不入库
```
