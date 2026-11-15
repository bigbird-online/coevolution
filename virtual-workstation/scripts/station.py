#!/usr/bin/env python3
"""虚拟工位自查：这台电脑是不是从登记的美国出口上网，这个出口在网站眼里干不干净。

  station.py init --ip <云主机公网 IPv4> [--label <账号标签>]   装好出口后登记一次
  station.py egress                                             每次登录 AI 账号前跑：出口对不对
  station.py clean                                              每周跑一次：出口干不干净、有没有泄漏

只读：不改 Tailscale、不改系统设置、不登录任何账号。只用 Python 标准库（3.8 以上）。
每项结果是 通过 / 需注意 / 不通过 / 没读到；没读到不算通过。
退出码：0 全部通过；1 有不通过；2 没有不通过，但有需注意或没读到。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import locale
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE.parent / "user" / "station.json"
IPV4_SOURCES = ("https://api.ipify.org", "https://ipv4.icanhazip.com")
IPV6_SOURCE = "https://api6.ipify.org"
TRUST_OK = 50  # ip.net.coffee：50–79 良好，80 以上纯净
BLOCKLISTS = ("bl.spamcop.net", "b.barracudacentral.org")
STUN_SERVERS = (("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478))
# 出口所在国的常见时差（小时）。只看时差，分不出同时区的国家。
UTC_OFFSETS = {"US": (-10, -4), "CA": (-8, -3.5), "GB": (0, 1), "DE": (1, 2), "FR": (1, 2), "JP": (9, 9)}
ZONE_TAB = Path("/usr/share/zoneinfo/zone.tab")

PASS, WARN, FAIL, UNKNOWN = "pass", "warn", "fail", "unknown"
MARK = {PASS: "✅ 通过", WARN: "⚠️ 需注意", FAIL: "❌ 不通过", UNKNOWN: "❔ 没读到"}


def item(label: str, state: str, detail: str) -> dict:
    return {"label": label, "state": state, "detail": detail}


# ---------- 读数 ----------

def http_text(url: str, timeout: float = 8) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "coevolution-virtual-workstation"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


def http_json(url: str, timeout: float = 10) -> dict:
    return json.loads(http_text(url, timeout))


def ip_or_none(text) -> str | None:
    try:
        return str(ipaddress.ip_address(str(text or "").strip()))
    except ValueError:
        return None


def read_ip(url: str) -> str | None:
    try:
        return ip_or_none(http_text(url))
    except (OSError, ValueError):
        return None


def public_ipv4() -> list[str | None]:
    """每个来源各读一次；读不到的记 None。"""
    return [read_ip(u) for u in IPV4_SOURCES]


TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")  # Tailscale 给设备的 IPv6 网段


def ipv6_path() -> str | None:
    """本机 IPv6 往公网走哪条路：none 没有出路；tunnel 进 Tailscale 通道；direct 走本地网络；None 读不到。
    对一个公网 IPv6 地址做 UDP connect（不发包），看系统选的源地址。"""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2001:4860:4860::8888", 53))
            source = ipaddress.ip_address(s.getsockname()[0].split("%")[0])
    except OSError as e:
        # 不支持 IPv6、没有 IPv6 地址、网络或主机不可达（macOS / Linux / Windows 的错误号）
        if getattr(e, "errno", None) in (47, 49, 51, 65, 97, 99, 101, 113, 10047, 10049, 10051, 10065):
            return "none"
        return None
    except ValueError:
        return None
    return "tunnel" if source in TAILNET_V6 else "none" if source.is_link_local else "direct"


def tailscale_cli() -> list[str] | None:
    if os.environ.get("TAILSCALE"):
        return [os.environ["TAILSCALE"]]
    found = shutil.which("tailscale")
    if found:
        return [found]
    for path in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                 r"C:\Program Files\Tailscale\tailscale.exe"):
        if Path(path).exists():
            return [path]
    return None


def tailscale_status() -> dict | None:
    cli = tailscale_cli()
    if not cli:
        return None
    env = dict(os.environ, TAILSCALE_BE_CLI="1")  # macOS 应用自带的程序没有它会打开图形界面
    try:
        out = subprocess.run(cli + ["status", "--json"], capture_output=True, text=True, timeout=20, env=env)
        return json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def exit_node(status: dict) -> dict | None:
    """当前出口：{id, name, online}；没开出口返回 None。"""
    st = status.get("ExitNodeStatus")
    if not st or not st.get("ID"):
        return None
    peer = next((p for p in (status.get("Peer") or {}).values() if p.get("ID") == st["ID"]), {})
    name = str(peer.get("DNSName") or peer.get("HostName") or "").split(".", 1)[0] or st["ID"]
    return {"id": st["ID"], "name": name, "online": bool(st.get("Online"))}


def ipinfo(ip: str) -> dict | None:
    try:
        d = http_json(f"https://ipinfo.io/{ip}/json")
    except (OSError, ValueError):
        return None
    return {"country": d.get("country"), "network": d.get("org")} if d.get("country") else None


def iprisk(ip: str) -> dict | None:
    """ip.net.coffee 的判断：信任分 0–100 与机房、VPN、代理、Tor、滥用标记；按网段给出，最长缓存一年。"""
    try:
        r = http_json(f"https://ip.net.coffee/api/iprisk/{ip}", timeout=12)
    except (OSError, ValueError):
        return None
    # 不认识的地址会回一个「住宅、100 分、没有网络归属」的答案，那不是判断。
    if not isinstance(r, dict) or not isinstance(r.get("trust_score"), int) or not r.get("asn"):
        return None
    return {"trust": r["trust_score"], "datacenter": r.get("is_datacenter"), "vpn": r.get("is_vpn"),
            "proxy": r.get("is_proxy"), "tor": r.get("is_tor"), "abuser": r.get("is_abuser"),
            "country": (r.get("countryCode") or "").upper() or None}


def lookup(name: str) -> str | None:
    try:
        return socket.gethostbyname(name)
    except socket.gaierror as e:
        if e.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5)):
            return None
        raise


def blocklists(ip: str) -> dict[str, str]:
    """每个名单先查它自己的测试条目，答不对就判没读到：有的系统把 DNS 故障也报成「查无此名」。"""
    reverse = ".".join(reversed(ip.split(".")))
    result = {}
    for bl in BLOCKLISTS:
        try:
            if lookup(f"2.0.0.127.{bl}") != "127.0.0.2":
                result[bl] = UNKNOWN
                continue
            answer = lookup(f"{reverse}.{bl}")
            result[bl] = "clean" if answer is None else "listed" if answer.startswith("127.0.0.") else UNKNOWN
        except OSError:
            result[bl] = UNKNOWN
    return result


def stun() -> str | None:
    """STUN 服务器看到的本机 UDP 公网地址，即网页 WebRTC 会露出的地址。"""
    for host, port in STUN_SERVERS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(4)
                s.sendto(struct.pack(">HHI", 1, 0, 0x2112A442) + os.urandom(12), (host, port))
                data, _ = s.recvfrom(2048)
        except OSError:
            continue
        i = 20
        while i + 4 <= len(data):
            kind, length = struct.unpack(">HH", data[i:i + 4])
            if kind == 0x20 and length >= 8:
                raw = bytes(b ^ m for b, m in zip(data[i + 8:i + 12], struct.pack(">I", 0x2112A442)))
                return socket.inet_ntoa(raw)
            i += 4 + length + (-length % 4)
    return None


def zone_country() -> str | None:
    """macOS、Linux：按系统时区表认出时区所属国家；认不出返回 None。"""
    try:
        zone = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
        for line in ZONE_TAB.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and not line.startswith("#") and parts[2] == zone:
                return parts[0]
    except OSError:
        pass
    return None


def utc_offset_hours() -> float:
    return datetime.now().astimezone().utcoffset().total_seconds() / 3600


def language() -> str | None:
    """系统首选语言，如 zh-Hans-CN、en_US。"""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                 timeout=5).stdout
            return next((w.strip(' ",()\n') for w in out.split("\n") if w.strip(' ",()')), None)
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        return locale.getlocale()[0] or os.environ.get("LANG")
    except ValueError:
        return None


def language_country(lang: str | None) -> str | None:
    tail = (lang or "").split(".")[0].replace("_", "-").rsplit("-", 1)[-1]
    return tail if len(tail) == 2 and tail.isalpha() and tail.isupper() else None


# ---------- 判断 ----------

def check_egress(station: dict, status: dict | None, ipv4: list) -> list[dict]:
    out = []
    if status is None:
        return [item("Tailscale", UNKNOWN, "找不到 tailscale 命令或读不到它的状态；先确认 Tailscale 已安装并打开")]
    if status.get("BackendState") != "Running":
        return [item("Tailscale", FAIL, f"Tailscale 没在运行（{status.get('BackendState') or '状态未知'}）")]
    node = exit_node(status)
    if node is None:
        out.append(item("出口节点", FAIL, "没有选出口：流量从本地宽带直接出去"))
    elif node["id"] != station["node_id"]:
        out.append(item("出口节点", FAIL, f"当前出口是 {node['name']}，不是登记的 {station['node']}"))
    elif not node["online"]:
        out.append(item("出口节点", FAIL, f"登记的出口 {station['node']} 不在线"))
    else:
        out.append(item("出口节点", PASS, f"经 {station['node']}"))
    seen = [ip for ip in ipv4 if ip]
    wrong = sorted({ip for ip in seen if ip != station["public_ipv4"]})
    if wrong:
        out.append(item("公网 IPv4", FAIL, f"网站看到的是 {'、'.join(wrong)}，不是登记的 {station['public_ipv4']}"))
    elif len(seen) < len(ipv4):
        out.append(item("公网 IPv4", UNKNOWN, f"{len(ipv4)} 个来源只读到 {len(seen)} 个"))
    else:
        out.append(item("公网 IPv4", PASS, f"{len(seen)} 个来源都看到 {station['public_ipv4']}"))
    return out


def check_exit(ip: str, info: dict | None, risk: dict | None, lists: dict) -> tuple[list[dict], str | None]:
    """出口 IP 在网站眼里的样子；同时返回出口所在国（以 IPinfo 为准，它没给才用 ip.net.coffee 的）。"""
    out = []
    opinions = {k: v for k, v in (("ipinfo", (info or {}).get("country")),
                                  ("ip.net.coffee", (risk or {}).get("country"))) if v}
    where = set(opinions.values())
    if len(where) > 1:
        out.append(item("出口位置", WARN, "定位不一致：" + "、".join(f"{k} {v}" for k, v in opinions.items())))
    elif len(opinions) < 2:
        out.append(item("出口位置", UNKNOWN, "只有 " + ("、".join(opinions) or "0 个来源") + " 给出了定位"))
    else:
        out.append(item("出口位置", PASS, f"两个来源都定位在 {next(iter(where))}；网络归属 {(info or {}).get('network') or '?'}"))
    if not risk:
        out.append(item("信任分", UNKNOWN, "ip.net.coffee 没有返回"))
        out.append(item("VPN/代理标记", UNKNOWN, "ip.net.coffee 没有返回"))
        out.append(item("滥用记录", UNKNOWN, "ip.net.coffee 没有返回"))
    else:
        t = risk["trust"]
        dc = "；机房 IP（云服务器都是，不等于 VPN）" if risk.get("datacenter") else ""
        out.append(item("信任分", PASS if t >= TRUST_OK else WARN, f"{t}/100，{TRUST_OK} 以下有风险{dc}"))
        flags = (("VPN", "vpn"), ("代理", "proxy"), ("Tor", "tor"))
        marked = [n for n, k in flags if risk.get(k) is True]
        unread = [n for n, k in flags if not isinstance(risk.get(k), bool)]
        if marked:
            out.append(item("VPN/代理标记", WARN, "被标为" + "、".join(marked)))
        elif unread:
            out.append(item("VPN/代理标记", UNKNOWN, "没有给出 " + "、".join(unread) + " 标记"))
        else:
            out.append(item("VPN/代理标记", PASS, "没有被标为 VPN、代理或 Tor"))
        abuser = risk.get("abuser")
        out.append(item("滥用记录", WARN if abuser is True else PASS if abuser is False else UNKNOWN,
                        {True: "有滥用记录", False: "没有滥用记录"}.get(abuser, "没有给出滥用标记")))
    listed = [k for k, v in lists.items() if v == "listed"]
    if listed:
        out.append(item("黑名单", FAIL, "上榜：" + "、".join(listed) + "；换一台云主机或换 IP"))
    elif not lists or UNKNOWN in lists.values():
        out.append(item("黑名单", UNKNOWN, "名单没有查全"))
    else:
        out.append(item("黑名单", PASS, f"{len(lists)} 个名单都没有"))
    return out, opinions.get("ipinfo") or opinions.get("ip.net.coffee")


def check_client(exit_ip: str, country: str | None, v6: str | None, v6_country: str | None, v6_path: str | None,
                 udp: str | None, dns_country: str | None, zone_cc: str | None, offset: float,
                 lang: str | None) -> list[dict]:
    """本机有没有绕开出口露出去的路，以及时区、语言与出口国是否一致。"""
    out = []
    if v6:
        if not country or not v6_country:
            out.append(item("IPv6", UNKNOWN, f"本机 IPv6 {v6} 或出口没有定位结果"))
        elif v6_country == country:
            out.append(item("IPv6", PASS, f"IPv6 也在 {country}"))
        else:
            out.append(item("IPv6", FAIL, f"IPv6 从 {v6_country} 出去（{v6}），绕开了出口；关掉本机 IPv6 或确认出口支持 IPv6"))
    elif v6_path == "none":
        out.append(item("IPv6", PASS, "本机没有 IPv6 出路"))
    elif v6_path == "tunnel":
        out.append(item("IPv6", PASS, "IPv6 进了出口通道，出口没有 IPv6，等于不通，不会绕开出口"))
    else:
        out.append(item("IPv6", UNKNOWN, "读不到 IPv6 地址，也不能确认本机没有 IPv6 出路"))
    if not udp:
        out.append(item("UDP（WebRTC）", UNKNOWN, "STUN 服务器没有回应"))
    elif udp == exit_ip:
        out.append(item("UDP（WebRTC）", PASS, "UDP 也经出口"))
    else:
        out.append(item("UDP（WebRTC）", FAIL, f"UDP 露出 {udp}，网页能经 WebRTC 看到它"))
    if not dns_country or not country:
        out.append(item("DNS 定位", UNKNOWN, "没有读到解析服务器的定位"))
    else:
        out.append(item("DNS 定位", PASS if dns_country == country else WARN,
                        f"解析服务器在 {dns_country}" + ("，与出口同国" if dns_country == country else f"，出口在 {country}")))
    if not country:
        out.append(item("时区", UNKNOWN, "出口国家没定下来"))
    elif zone_cc:
        out.append(item("时区", PASS if zone_cc == country else WARN,
                        f"系统时区属于 {zone_cc}" + ("" if zone_cc == country else f"，出口在 {country}；网站能看到时区")))
    elif country in UTC_OFFSETS:
        lo, hi = UTC_OFFSETS[country]
        ok = lo <= offset <= hi
        out.append(item("时区", PASS if ok else WARN,
                        f"系统时区 UTC{offset:+g}" + ("" if ok else f"，不在 {country} 的时差范围；网站能看到时区")))
    else:
        out.append(item("时区", UNKNOWN, f"认不出系统时区属于哪国（UTC{offset:+g}）"))
    lang_cc = language_country(lang)
    if not lang_cc or not country:
        out.append(item("系统语言", UNKNOWN, f"认不出系统语言 {lang or '空'} 的地区"))
    else:
        out.append(item("系统语言", PASS if lang_cc == country else WARN,
                        f"系统语言 {lang}" + ("" if lang_cc == country else f"，地区不是 {country}；网站能看到语言")))
    return out


# ---------- 命令 ----------

def load(path: Path) -> dict:
    try:
        station = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"读不到登记文件 {path}：{e}。先跑 station.py init")
    missing = [k for k in ("node", "node_id", "public_ipv4") if not station.get(k)]
    if missing:
        raise SystemExit(f"登记文件 {path} 缺 {'、'.join(missing)}")
    return station


def report(title: str, items: list[dict]) -> int:
    print(title)
    for it in items:
        print(f"  {MARK[it['state']]}  {it['label']}：{it['detail']}")
    states = {it["state"] for it in items}
    return 1 if FAIL in states else 0 if states == {PASS} else 2


def cmd_init(args) -> int:
    ip = ip_or_none(args.ip)
    if not ip or ipaddress.ip_address(ip).version != 4:
        raise SystemExit("--ip 要填云主机的公网 IPv4（在云主机上跑 curl -4 https://api.ipify.org 得到）")
    if args.file.exists() and not args.force:
        raise SystemExit(f"{args.file} 已存在；确认要换出口再加 --force")
    status = tailscale_status()
    node = exit_node(status) if status else None
    if not node or not node["online"]:
        raise SystemExit("Tailscale 没有在线的出口：先在 Tailscale 里选这台云主机作出口，再登记")
    seen = [x for x in public_ipv4() if x]
    if not seen or any(x != ip for x in seen):
        raise SystemExit(f"本机现在从 {'、'.join(sorted(set(seen))) or '（没读到）'} 出去，与 --ip {ip} 不符；先核对出口")
    station = {"node": node["name"], "node_id": node["id"], "public_ipv4": ip, "label": args.label or "",
               "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    args.file.parent.mkdir(parents=True, exist_ok=True)
    args.file.write_text(json.dumps(station, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已登记：出口 {node['name']}，公网 IPv4 {ip}，写入 {args.file}")
    return 0


def cmd_egress(args) -> int:
    station = load(args.file)
    code = report(f"出口自查（登记：{station['node']} / {station['public_ipv4']}）",
                  check_egress(station, tailscale_status(), public_ipv4()))
    print("结论：" + ("出口正确，可以登录。" if code == 0 else "先别登录 AI 账号，按上面的提示处理后重跑。"))
    return code


def cmd_clean(args) -> int:
    station = load(args.file)
    first = check_egress(station, tailscale_status(), public_ipv4())
    if {it["state"] for it in first} != {PASS}:
        report("出口自查没通过，先修出口：", first)
        return 1
    ip = station["public_ipv4"]
    info, risk = ipinfo(ip), iprisk(ip)
    exit_items, country = check_exit(ip, info, risk, blocklists(ip))
    v6 = read_ip(IPV6_SOURCE)
    v6_info = ipinfo(v6) if v6 else None
    try:
        dns_ip = socket.gethostbyname("whoami.akamai.net")
    except OSError:
        dns_ip = None
    dns_info = ipinfo(dns_ip) if dns_ip else None
    client_items = check_client(ip, country, v6, (v6_info or {}).get("country"), ipv6_path(), stun(),
                                (dns_info or {}).get("country"), zone_country(), utc_offset_hours(), language())
    code = report(f"出口干净度自查（{station['node']} / {ip}，{time.strftime('%Y-%m-%d %H:%M')}）",
                  first + exit_items + client_items)
    print("结论：" + {0: "全部通过。", 1: "有不通过的项，先处理再登录。",
                      2: "没有不通过；需注意的项看说明自行决定，没读到的稍后重跑。"}[code])
    print("定位数据来自 IPinfo（ipinfo.io）与 ip.net.coffee。")
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help="登记文件，默认 user/station.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="登记出口")
    p.add_argument("--ip", required=True, help="云主机公网 IPv4")
    p.add_argument("--label", help="这个出口给哪个账号用，只写标签，不写账号密码")
    p.add_argument("--force", action="store_true", help="覆盖已有登记")
    sub.add_parser("egress", help="出口对不对")
    sub.add_parser("clean", help="出口干不干净")
    args = ap.parse_args(argv)
    return {"init": cmd_init, "egress": cmd_egress, "clean": cmd_clean}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
