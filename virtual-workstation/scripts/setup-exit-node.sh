#!/bin/sh
# 把一台全新的 Ubuntu / Debian 云主机（推荐腾讯云轻量应用服务器·美国硅谷）开成 Tailscale 出口节点。
# 用 root 运行，可重复运行。腾讯云的 Ubuntu 镜像以 ubuntu 用户登录，前面加 sudo：
#
#   sudo sh setup-exit-node.sh           安装并配置，最后打印 Tailscale 登录链接和这台的公网 IPv4
#   sudo sh setup-exit-node.sh --check   只读：逐项报告这台是否已配好，不改任何东西
#
# 做的事：装 Tailscale（官方安装脚本）；打开 IPv4/IPv6 转发；TCP 拥塞控制改 BBR；
# 开机时打开网卡的 UDP GRO 转发（Tailscale 官方建议，提速）并把经隧道的 TCP MSS 夹到路径 MTU（防大包卡死）；
# 以出口节点身份加入你的 Tailscale。不装代理、不开新端口、不改 SSH 和防火墙。
set -eu

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

FORWARD_CONF=/etc/sysctl.d/99-tailscale.conf
BBR_CONF=/etc/sysctl.d/90-tcp-bbr.conf
TUNING=/usr/local/sbin/tailscale-exit-tuning
UNIT=/etc/systemd/system/tailscale-exit-tuning.service

ok()   { echo "  ✅ $1"; }
bad()  { echo "  ❌ $1"; FAILED=1; }
FAILED=0

[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行：sudo sh $0 $*"; exit 1; }
command -v apt-get >/dev/null 2>&1 || { echo "只支持 Ubuntu / Debian（需要 apt-get）"; exit 1; }
IFACE=$(ip -o -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}')
[ -n "$IFACE" ] || { echo "找不到通往公网的网卡"; exit 1; }

check() {
  echo "出口节点自查（网卡 $IFACE）"
  if command -v tailscale >/dev/null 2>&1; then ok "Tailscale 已安装：$(tailscale version | head -1)"; else bad "Tailscale 没装"; fi
  [ "$(sysctl -n net.ipv4.ip_forward)" = 1 ] && ok "IPv4 转发已开" || bad "IPv4 转发没开"
  [ "$(sysctl -n net.ipv6.conf.all.forwarding)" = 1 ] && ok "IPv6 转发已开" || bad "IPv6 转发没开"
  [ "$(sysctl -n net.ipv4.tcp_congestion_control)" = bbr ] && ok "拥塞控制 BBR" || bad "拥塞控制不是 BBR"
  if command -v ethtool >/dev/null 2>&1 && ethtool -k "$IFACE" 2>/dev/null | grep -q '^rx-udp-gro-forwarding: on'; then
    ok "UDP GRO 转发已开"
  else bad "UDP GRO 转发没开"; fi
  if iptables -t mangle -C FORWARD -o tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
    ok "MSS 夹紧已生效"
  else bad "MSS 夹紧没生效"; fi
  if command -v tailscale >/dev/null 2>&1 && tailscale debug prefs 2>/dev/null | grep -q '"0.0.0.0/0"'; then
    ok "已声明为出口节点（还需在 Tailscale 管理后台批准）"
  else bad "没有声明为出口节点"; fi
  echo "  这台的公网 IPv4：$(curl -4 -fsS -m 8 https://api.ipify.org || echo 读不到)"
  [ "$FAILED" -eq 0 ] && echo "结论：配好了。" || echo "结论：有没配好的项，去掉 --check 重跑一次。"
  return "$FAILED"
}

if [ "$CHECK" -eq 1 ]; then check; exit $?; fi

echo "1/5 安装依赖与 Tailscale"
export DEBIAN_FRONTEND=noninteractive
missing=""
for tool in curl ethtool iptables; do command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"; done
if [ -n "$missing" ]; then
  apt-get update -qq
  apt-get install -y -qq $missing >/dev/null
fi
command -v tailscale >/dev/null 2>&1 || curl -fsSL https://tailscale.com/install.sh | sh

echo "2/5 打开转发、改用 BBR"
printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' > "$FORWARD_CONF"
printf 'net.core.default_qdisc = fq\nnet.ipv4.tcp_congestion_control = bbr\n' > "$BBR_CONF"
sysctl -q -p "$FORWARD_CONF"
sysctl -q -p "$BBR_CONF"

echo "3/5 开机调优：UDP GRO 转发与 MSS 夹紧"
cat > "$TUNING" <<EOF
#!/bin/sh
set -eu
/usr/sbin/ethtool -K $IFACE rx-udp-gro-forwarding on rx-gro-list off || true
for direction in -i -o; do
  for tool in /usr/sbin/iptables /usr/sbin/ip6tables; do
    \$tool -t mangle -C FORWARD "\$direction" tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null ||
      \$tool -t mangle -A FORWARD "\$direction" tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
  done
done
EOF
chmod 755 "$TUNING"
cat > "$UNIT" <<EOF
[Unit]
Description=Tailscale exit node tuning (UDP GRO forwarding, MSS clamp)
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
Type=oneshot
ExecStart=$TUNING
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now tailscaled >/dev/null 2>&1
systemctl enable tailscale-exit-tuning >/dev/null 2>&1
systemctl restart tailscale-exit-tuning

echo "4/5 以出口节点身份加入 Tailscale"
if tailscale status >/dev/null 2>&1; then
  tailscale set --advertise-exit-node
else
  echo "    下面会打印一个链接：在浏览器里打开，用你自己的 Tailscale 账号登录。"
  tailscale up --advertise-exit-node
fi

echo "5/5 自查"
check || true
cat <<'EOF'

接下来：
  1. 打开 https://login.tailscale.com/admin/machines ，在这台机器的菜单里选
     「Edit route settings」，勾选「Use as exit node」。
  2. 同一菜单选「Disable key expiry」，免得半年后掉线要重新登录。
  3. 记下上面打印的公网 IPv4，电脑上跑 station.py init --ip <这个 IP> 时要用。
  腾讯云控制台的防火墙不用为 Tailscale 放行端口：它从云主机主动向外建连，实测能直连。
EOF
