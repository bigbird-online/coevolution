"""station.py 的判断逻辑（不联网）。运行：python3 -m unittest discover tests"""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("station", Path(__file__).resolve().parents[1] / "scripts" / "station.py")
station = importlib.util.module_from_spec(spec)
spec.loader.exec_module(station)

STATION = {"node": "us-exit", "node_id": "nABC", "public_ipv4": "203.0.113.7"}


def status(exit_id="nABC", online=True, backend="Running"):
    peer = {"ID": exit_id, "DNSName": "us-exit.tail1234.ts.net.", "HostName": "us-exit"}
    return {"BackendState": backend, "Peer": {"k": peer},
            "ExitNodeStatus": {"ID": exit_id, "Online": online} if exit_id else None}


def states(items):
    return {i["label"]: i["state"] for i in items}


class Egress(unittest.TestCase):
    def test_all_pass(self):
        got = states(station.check_egress(STATION, status(), ["203.0.113.7", "203.0.113.7"]))
        self.assertEqual(set(got.values()), {"pass"})

    def test_no_exit_fails(self):
        self.assertEqual(states(station.check_egress(STATION, status(exit_id=None), ["198.51.100.1"] * 2))["出口节点"], "fail")

    def test_other_exit_fails(self):
        self.assertEqual(states(station.check_egress(STATION, status(exit_id="nOTHER"), ["203.0.113.7"] * 2))["出口节点"], "fail")

    def test_offline_exit_fails(self):
        self.assertEqual(states(station.check_egress(STATION, status(online=False), ["203.0.113.7"] * 2))["出口节点"], "fail")

    def test_wrong_ip_fails_even_if_one_source_right(self):
        got = states(station.check_egress(STATION, status(), ["203.0.113.7", "198.51.100.1"]))
        self.assertEqual(got["公网 IPv4"], "fail")

    def test_unread_ip_is_unknown_not_pass(self):
        self.assertEqual(states(station.check_egress(STATION, status(), ["203.0.113.7", None]))["公网 IPv4"], "unknown")

    def test_no_tailscale_is_unknown(self):
        self.assertEqual([i["state"] for i in station.check_egress(STATION, None, [None, None])], ["unknown"])

    def test_stopped_tailscale_fails(self):
        self.assertEqual([i["state"] for i in station.check_egress(STATION, status(backend="Stopped"), [])], ["fail"])


class Exit(unittest.TestCase):
    CLEAN = {"bl.spamcop.net": "clean", "b.barracudacentral.org": "clean"}

    def test_us_exit_passes(self):
        items, country = station.check_exit({"country": "US", "network": "AS1 X"}, self.CLEAN)
        self.assertEqual(set(states(items).values()), {"pass"})
        self.assertEqual(country, "US")

    def test_non_us_location_fails(self):
        items, country = station.check_exit({"country": "SG"}, self.CLEAN)
        self.assertEqual(states(items)["出口位置"], "fail")
        self.assertEqual(country, "SG")

    def test_blocklisted_fails(self):
        items, _ = station.check_exit({"country": "US"}, dict(self.CLEAN, **{"bl.spamcop.net": "listed"}))
        self.assertEqual(states(items)["黑名单"], "fail")

    def test_unread_is_unknown(self):
        items, country = station.check_exit(None, {"bl.spamcop.net": "unknown"})
        self.assertEqual(set(states(items).values()), {"unknown"})
        self.assertIsNone(country)

    def test_manual_check_names_the_page_for_this_ip(self):
        self.assertIn("https://ip.net.coffee/ip/203.0.113.7", station.manual_check("203.0.113.7"))

    def test_no_ip_net_coffee_api_call(self):
        source = Path(station.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ip.net.coffee/api", source)


class Client(unittest.TestCase):
    def run_client(self, **kw):
        args = dict(exit_ip="203.0.113.7", country="US", v6=None, v6_country=None, v6_path="none",
                    udp="203.0.113.7", dns_country="US", zone_cc="US", offset=-8, lang="en-US")
        args.update(kw)
        return states(station.check_client(**args))

    def test_all_pass(self):
        self.assertEqual(set(self.run_client().values()), {"pass"})

    def test_home_ipv6_leak_fails(self):
        self.assertEqual(self.run_client(v6="240e::1", v6_country="CN", v6_path="direct")["IPv6"], "fail")

    def test_ipv6_into_tunnel_without_exit_ipv6_passes(self):
        self.assertEqual(self.run_client(v6_path="tunnel")["IPv6"], "pass")

    def test_unread_direct_ipv6_is_unknown(self):
        self.assertEqual(self.run_client(v6_path="direct")["IPv6"], "unknown")

    def test_udp_leak_fails(self):
        self.assertEqual(self.run_client(udp="198.51.100.1")["UDP（WebRTC）"], "fail")

    def test_china_locale_warns(self):
        got = self.run_client(zone_cc="CN", lang="zh-Hans-CN")
        self.assertEqual((got["时区"], got["系统语言"]), ("warn", "warn"))

    def test_offset_used_without_zone_table(self):
        self.assertEqual(self.run_client(zone_cc=None, offset=8)["时区"], "warn")
        self.assertEqual(self.run_client(zone_cc=None, offset=-5)["时区"], "pass")


class Parsing(unittest.TestCase):
    def test_language_country(self):
        self.assertEqual(station.language_country("zh-Hans-CN"), "CN")
        self.assertEqual(station.language_country("en_US.UTF-8"), "US")
        self.assertIsNone(station.language_country("Chinese (Simplified)_China"))
        self.assertIsNone(station.language_country(None))

    def test_exit_node_name(self):
        self.assertEqual(station.exit_node(status())["name"], "us-exit")
        self.assertIsNone(station.exit_node(status(exit_id=None)))


if __name__ == "__main__":
    unittest.main()
