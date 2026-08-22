#!/usr/bin/env python3
"""送达层——把选好的文章交到会员手里。

## 为什么分两层

原版把"送达"直接等同于"调某个内部消息服务的接口"。那样做有两个后果：
一是没有那个服务的人根本跑不通；二是投递失败时，整条链路会停在最后一步，
前面采集、打分、翻译的功夫全白费。

所以这里拆成两层：

- **落盘（缺省，无任何外部依赖）**：文章写进 `user/pushed/`。这一层**总是发生**，
  失败才算真失败。零配置的用户跑完全流程就停在这里——他自己去目录里读，一样是完整的产品。
- **投递（可选）**：把落盘好的东西再送去某个地方（webhook / 邮件 / 你自己写的命令）。
  **失败只降级告警，不阻断**。

## 为什么投递失败不算失败

这是从一次真实事故里学到的：投递环节静默失败过——日志显示"已推送"，
但会员那头什么都没收到，隔了几天才发现。根因是把"调用返回成功"当成了"人收到了"。

现在的处理是：每一层各自留可核验的凭证（落盘留文件路径，投递留返回码与时间戳），
两层分别记账。**不允许用投递成功去推断落盘成功，也不允许反过来。**

## 配置

在 `user/profile.yaml` 里：

```yaml
delivery:
  # 不配 delivery 整段 = 只落盘，这是完全合法的用法
  webhook:
    url: "https://..."           # POST，body 见下
    headers: {}                  # 可选，比如鉴权头
  command:                       # 可选，交给外部命令处理
    - "/usr/local/bin/my-notify"
    - "{file}"                   # {file} 会被替换成落盘的文件路径
```

webhook 的 body 形如：
`{"title": "...", "file": "user/pushed/xxx.md", "score": 8.5, "url": "原文链接"}`
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_DIR = ROOT / "user"
PUSHED_DIR = USER_DIR / "pushed"
PROFILE = USER_DIR / "profile.yaml"
DELIVERY_LOG = USER_DIR / "delivery-log.jsonl"

WEBHOOK_TIMEOUT_SEC = 15
COMMAND_TIMEOUT_SEC = 60


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat()


def _load_profile():
    """读 profile；缺文件或解析失败都退回空 dict——配置问题不该让送达崩。"""
    try:
        with open(PROFILE, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — 配置坏了也要能落盘
        print(f"[warn] profile.yaml 读取失败（{exc}），按无投递配置处理", file=sys.stderr)
        return {}


def _log(record):
    """每次送达都记一行。两层各自的结果分开记，不合并成一个 success 字段。"""
    DELIVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DELIVERY_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def land(title, content, slug=None, meta=None):
    """第一层：落盘。这一层失败 = 真失败。

    返回落盘文件的路径。调用方应当把这个路径当作"送达凭证"——
    文件在，才谈得上送到了。
    """
    PUSHED_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    name = f"{slug or 'article'}-{stamp}.md"
    path = PUSHED_DIR / name

    body = content
    if meta:
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
        body = f"---\n{front}---\n\n{content}"

    path.write_text(body, encoding="utf-8")
    _log({"ts": _now(), "layer": "land", "ok": True, "file": str(path.relative_to(ROOT)), "title": title})
    return path


def _deliver_webhook(cfg, payload):
    url = cfg.get("url")
    if not url:
        return {"ok": False, "reason": "webhook 配了但缺 url"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (cfg.get("headers") or {}).items():
        req.add_header(k, str(v))
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SEC) as resp:
            return {"ok": 200 <= resp.status < 300, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 投递失败不阻断
        return {"ok": False, "reason": str(exc)}


def _deliver_command(cmd_template, file_path):
    cmd = [str(c).replace("{file}", str(file_path)) for c in cmd_template]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SEC, check=False
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "")[:500],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


def deliver(file_path, title, extra=None):
    """第二层：可选投递。失败只告警，返回各渠道结果。

    没配 delivery 就直接返回空——**这是正常状态，不是错误**。
    """
    profile = _load_profile()
    cfg = (profile.get("delivery") or {})
    if not cfg:
        return {}

    rel = str(Path(file_path).relative_to(ROOT)) if Path(file_path).is_absolute() else str(file_path)
    payload = {"title": title, "file": rel}
    if extra:
        payload.update(extra)

    results = {}
    if cfg.get("webhook"):
        results["webhook"] = _deliver_webhook(cfg["webhook"], payload)
    if cfg.get("command"):
        results["command"] = _deliver_command(cfg["command"], file_path)

    for channel, res in results.items():
        if not res.get("ok"):
            # 大声说出来，但不抛异常——文章已经落盘了，会员照样读得到
            print(
                f"[warn] 投递到 {channel} 失败：{res.get('reason') or res.get('status')}。"
                f"文章已落盘 {rel}，不影响阅读。",
                file=sys.stderr,
            )
        _log({"ts": _now(), "layer": "deliver", "channel": channel, **res, "file": rel})

    return results


def send(title, content, slug=None, meta=None, extra=None):
    """完整送达：先落盘（必须成功），再投递（可选、失败不阻断）。"""
    path = land(title, content, slug=slug, meta=meta)
    results = deliver(path, title, extra=extra)
    return {"file": path, "delivery": results}


def _cmd_status():
    """看最近的送达情况——两层分开看，不合并。"""
    if not DELIVERY_LOG.exists():
        print("还没有送达记录。")
        return 0
    lands, delivers = [], []
    with open(DELIVERY_LOG, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            (lands if rec.get("layer") == "land" else delivers).append(rec)

    print(f"落盘：{len(lands)} 篇" + (f"，最近 {lands[-1]['ts']}" if lands else ""))
    if delivers:
        failed = [d for d in delivers if not d.get("ok")]
        print(f"投递：{len(delivers)} 次，失败 {len(failed)} 次")
        for d in failed[-3:]:
            print(f"  - {d['ts']} {d.get('channel')}: {d.get('reason') or d.get('status')}")
    else:
        print("投递：未配置（只落盘）——这是合法用法")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        return _cmd_status()
    print(__doc__)
    print("\n用法：deliver.py status —— 查看送达记录")
    print("作为库使用：from deliver import send; send(title, content)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
