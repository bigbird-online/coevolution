#!/usr/bin/env python3
"""
Article Collector - 文章采集器

功能：
- 从 sources.yaml 读取 Active 信息源
- 解析 RSS feed，提取最近 N 天的文章
- 使用 newspaper3k 提取全文
- 生成标准化 Markdown 文件（frontmatter + 内容 + 笔记区）
- 按领域分类存储到本 skill 的 user/article-pool/（自足，不依赖 skill 目录外的任何数据）
- 支持文章状态流转：fetched → candidate → translated → rated
- 生成摄取现状报告（intake-status）

使用方法：
    python article_collector.py collect --days 7
    python article_collector.py collect --dry-run
    python article_collector.py status
    python article_collector.py rate FILE --score 4
    python article_collector.py intake-status

版本：0.2.0
"""

import sys as _sys, os as _os
from pathlib import Path as _Path

# 强制 stdout/stderr 无缓冲，避免在非 TTY 环境（agent 调用）下长时间无输出
_os.environ["PYTHONUNBUFFERED"] = "1"
_SKILL_ROOT = _Path(__file__).resolve().parents[1]
_VENV_PY = _SKILL_ROOT / ".venv" / "bin" / "python3"
if _os.path.realpath(_sys.executable) != _os.path.realpath(str(_VENV_PY)):
    if not _VENV_PY.exists():
        print(f"ERROR: skill venv not found: {_VENV_PY}\n运行: cd {_SKILL_ROOT} && uv sync", file=_sys.stderr)
        _sys.exit(1)
    _os.execv(str(_VENV_PY), [str(_VENV_PY)] + _sys.argv)

import sys
import json
import yaml
import argparse
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
import urllib.request
import xml.etree.ElementTree as ET
import time
import random
import ssl
import signal
import concurrent.futures
import threading

# 可选依赖：newspaper3k
try:
    from newspaper import Article, ArticleException, Config as NewspaperConfig

    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False


# ============================================================================
# 配置常量
# ============================================================================


class Config:
    """配置常量"""

    # 路径配置 —— 全部相对 skill 根目录，指向 skill 自有 user/ 目录（完全自足，
    # 不依赖任何 skill 目录外的绝对路径 / 他人数据目录）
    BASE_DIR = Path(__file__).resolve().parent
    _SKILL_ROOT = BASE_DIR.parent                       # companion skill 根目录
    # 配置数据目录 = 本 skill 用户自有数据（init 创建），不读任何外部共享配置
    CONFIG_DIR = _SKILL_ROOT / "user"
    SOURCES_FILE = CONFIG_DIR / "sources.yaml"           # 用户自己的信源池
    PROFILE_FILE = CONFIG_DIR / "profile.yaml"           # 用户自己的画像（可覆盖下列示例默认）
    QUARANTINE_FILE = CONFIG_DIR / "quarantine.yaml"
    ARCHIVE_FILE = CONFIG_DIR / "archive.yaml"
    TEMPLATE_FILE = BASE_DIR / "templates" / "article_template.md"
    ARTICLE_POOL_DIR = CONFIG_DIR / "article-pool"       # 采集落到用户自有池
    COLLECTION_LOG = CONFIG_DIR / "collection-log.jsonl"
    FEEDBACK_LOG = CONFIG_DIR / "feedback-log.jsonl"

    # 状态常量
    STATUS_FETCHED = "fetched"
    STATUS_CANDIDATE = "candidate"
    STATUS_TRANSLATED = "translated"

    # 采集配置
    WINDOW_DAYS = 7
    MIN_WORD_COUNT = 500  # 最小字数门槛（降低以适应更多文章）
    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    REQUEST_TIMEOUT = 15
    DELAY_MIN = 0.3  # 请求间隔（秒）- 降低以提高速度
    DELAY_MAX = 0.8
    MAX_ARTICLES_PER_SOURCE = 10  # 每源缺省上限（防高频源耗时）；可被源级 filter_rules.max_per_batch 覆盖
    EXTRACT_TIMEOUT_SEC = 25  # 单篇文章提取超时（秒）

    # VIP 作者（高优先级）——**这是示例默认值，不是产品判断**
    #
    # 下面这份名单只是一个能用的起点（都是公开写作者，署名文章质量长期稳定）。
    # 它反映的是某个人的阅读口味，不是"客观上值得读的人"。
    # **请换成你自己的**：在 user/profile.yaml 的 vip_authors 里列出你信任的作者，
    # 配了就完全覆盖下面这份，不做合并。留空则用下面的默认。
    VIP_AUTHORS = [
        "Paul Graham",
        "Lilian Weng",
        "Ben Thompson",
        "Kevin Kelly",
        "Chip Huyen",
        "Tyler Cowen",
        "Andrej Karpathy",
        "Matt Levine",
        "Cory Doctorow",
        "Simon Willison",
        "Nassim Taleb",
    ]

    # 作者权威分（0-40）——同上，示例默认值，可被 user/profile.yaml 的 author_scores 覆盖。
    # 未列出的作者一律拿 DEFAULT_AUTHOR_SCORE。
    AUTHOR_SCORES = {
        "Paul Graham": 40,
        "Lilian Weng": 40,
        "Ben Thompson": 40,
        "Kevin Kelly": 38,
        "Chip Huyen": 38,
        "Tyler Cowen": 35,
        "Andrej Karpathy": 40,
        "Matt Levine": 35,
        "Cory Doctorow": 35,
        "Simon Willison": 35,
        "Nassim Taleb": 38,
    }
    DEFAULT_AUTHOR_SCORE = 20

    _profile_cache = None

    @classmethod
    def _profile(cls):
        """读 user/profile.yaml（缺文件/解析失败都退回空 dict，不让采集因配置问题崩）。"""
        if cls._profile_cache is None:
            try:
                with open(cls.PROFILE_FILE, "r", encoding="utf-8") as fh:
                    cls._profile_cache = yaml.safe_load(fh) or {}
            except Exception:
                cls._profile_cache = {}
        return cls._profile_cache

    @classmethod
    def vip_authors(cls):
        """用户配了就整份替换，不与示例默认合并——避免"删不掉别人的口味"。"""
        v = cls._profile().get("vip_authors")
        return list(v) if v else cls.VIP_AUTHORS

    @classmethod
    def author_scores(cls):
        """同上：配了就整份替换。"""
        v = cls._profile().get("author_scores")
        return dict(v) if v else cls.AUTHOR_SCORES

    # 高价值关键词
    HIGH_VALUE_KEYWORDS = [
        "principle",
        "law",
        "paradox",
        "framework",
        "theory",
        "model",
        "strategy",
        "pattern",
        "how to",
        "guide",
        "lessons",
    ]


# ============================================================================
# RSS 解析模块（复用 fetch_latest.py 逻辑）
# ============================================================================


def load_sources() -> List[Dict[str, Any]]:
    """加载 sources.yaml"""
    with Config.SOURCES_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return [s for s in sources if isinstance(s, dict)]


def fetch_feed(url: str) -> Optional[bytes]:
    """获取 RSS feed 内容"""
    try:
        # 创建 SSL 上下文（禁用证书验证以解决 macOS 证书问题）
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        proxy_handler = urllib.request.ProxyHandler()
        https_handler = urllib.request.HTTPSHandler(context=ssl_context)
        opener = urllib.request.build_opener(proxy_handler, https_handler)
        req = Request(url, headers={"User-Agent": Config.USER_AGENT})
        with opener.open(req, timeout=Config.REQUEST_TIMEOUT) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"[warn] fetch failed {url}: {exc}")
        return None


def parse_date(text: str) -> Optional[datetime]:
    """解析日期字符串"""
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except Exception:
        # 尝试 ISO 格式
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None

def _strip_invalid_xml_chars(content: bytes) -> bytes:
    """剔除 XML 1.0 非法控制字符（保留 \\t \\n \\r）。

    背景：0629 hero 盘点发现 Jeremy Howard（answer.ai）feed 新鲜有 20 篇，
    但正文块里嵌了终端控制字符（\\x08 等，技术博主把含日志/代码的内容贴进
    feed 时常见），ET.fromstring 整篇抛 ParseError → parse_feed 返回空 →
    源被当僵尸。这不是源死，是解析太脆。预清洗后救回，且防未来所有同型漂移。
    合法范围：\\x09 \\x0a \\x0d、\\x20-\\xd7ff 等；这里只剔最常见的 C0 非法段。
    """
    return re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", content)


def parse_feed(content: bytes) -> List[Dict[str, Any]]:
    """解析 RSS/Atom feed"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # 降级：剔除非法控制字符后重试（0629 Jeremy Howard 类，见 _strip_invalid_xml_chars）
        try:
            root = ET.fromstring(_strip_invalid_xml_chars(content))
        except ET.ParseError as exc:
            print(f"[warn] parse error (even after sanitize): {exc}")
            return []

    items: List[Dict[str, Any]] = []

    # RSS 2.0
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_text = item.findtext("pubDate") or item.findtext("date") or ""
        description = item.findtext("description") or ""
        author = (
            item.findtext("author")
            or item.findtext("{http://purl.org/dc/elements/1.1/}creator")
            or ""
        )
        published = parse_date(pub_text)
        items.append(
            {
                "title": title.strip(),
                "link": link.strip(),
                "published": published,
                "description": description.strip(),
                "author": author.strip(),
            }
        )

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        pub_text = entry.findtext(
            "atom:updated", default="", namespaces=ns
        ) or entry.findtext("atom:published", default="", namespaces=ns)
        description = (
            entry.findtext("atom:summary", default="", namespaces=ns)
            or entry.findtext("atom:content", default="", namespaces=ns)
            or ""
        )
        author_el = entry.find("atom:author", ns)
        author = (
            author_el.findtext("atom:name", default="", namespaces=ns)
            if author_el is not None
            else ""
        )
        published = parse_date(pub_text)
        items.append(
            {
                "title": title.strip(),
                "link": link.strip(),
                "published": published,
                "description": description[:500].strip(),  # 截断摘要
                "author": author.strip(),
            }
        )

    return items


# ============================================================================
# Scraper 列表页解析模块（无 RSS 源专用）
# ============================================================================


def fetch_html(url: str) -> Optional[str]:
    """抓取列表页 HTML（复用 fetch_feed 的 SSL/代理处理，返回解码后的文本）。

    与 fetch_feed 区别：feed 返回 bytes 交给 XML 解析；列表页要正则扫 <a>，
    这里直接按声明编码解码成 str。
    """
    raw = fetch_feed(url)
    if raw is None:
        return None
    # fetch_feed 已 read() 出 bytes，按常见编码尝试解码
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")


# 列表页里需要排除的"非文章"链接特征（导航/页脚/社交/订阅/法务）
_LISTING_SKIP_SEGMENTS = (
    "/about", "/contact", "/privacy", "/terms", "/login", "/signin", "/sign-in",
    "/subscribe", "/newsletter", "/rss", "/feed", "/tag/", "/tags/", "/category/",
    "/categories/", "/author/", "/authors/", "/search", "/careers", "/jobs",
    "/legal", "/cookie", "/sitemap", "/donate", "/press", "/team", "/events",
    "/account", "/settings", "/help", "/disclaimer", "/advanced",
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "youtube.com",
    "instagram.com", "mailto:", "javascript:", "#",
)

# per-host 启发：host 子串 → 文章 URL 必含的路径段（强过滤，命中则只收含该段的链接）
_LISTING_HOST_HINTS = {
    "carnegieendowment.org": "/china-financial-markets/",
    # CFR 博客（Follow the Money）单篇现以 /articles/<slug> 发布，列表页里的 /blog/ 仅是栏目入口
    "cfr.org": "/articles/",
    "phillipslabmcmaster.ca": "/publication",
    "anthropic.com": None,  # Anthropic 用通用启发（见下）即可
    "pubmed.ncbi.nlm.nih.gov": None,
}


def _looks_like_article_url(href: str, base_host: str) -> bool:
    """通用判定：这个链接像不像一篇文章（而非导航/页脚/外站）。"""
    if not href:
        return False
    low = href.lower()
    for seg in _LISTING_SKIP_SEGMENTS:
        if seg in low:
            return False
    parsed = urlparse(href)
    # 外站链接（host 不同）一律不收——列表页文章通常同站
    if parsed.netloc and base_host and parsed.netloc.replace("www.", "") != base_host.replace("www.", ""):
        return False
    path = parsed.path.rstrip("/")
    if not path or path == "":
        return False  # 指向首页
    # 文章路径通常较深或带 slug（含连字符的末段，或纯数字 id）
    last = path.rsplit("/", 1)[-1]
    if "-" in last and len(last) > 6:
        return True
    if last.isdigit() and len(last) >= 5:  # pubmed 这类纯数字 id
        return True
    # 路径有 2 段以上也可能是文章（如 /china-financial-markets/2026/...）
    if path.count("/") >= 2:
        return True
    return False


# 日期正则：在锚点附近文本里抓发布日期（ISO / "June 12, 2026" / "2026-06-12"）
_DATE_PATTERNS = [
    (re.compile(r"(\d{4}-\d{2}-\d{2})"), "%Y-%m-%d"),
    (re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})"), "%B %d, %Y"),
    (re.compile(r"(\d{1,2} [A-Z][a-z]+ \d{4})"), "%d %B %Y"),
]


def _extract_nearby_date(text: str) -> Optional[datetime]:
    """从锚点附近文本里尽力抓一个发布日期，抓不到返回 None。"""
    if not text:
        return None
    for pat, fmt in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def parse_listing_page(html: str, base_url: str) -> List[Dict[str, Any]]:
    """从列表页 HTML 解析出文章链接列表（标题 + URL + 尽力的发布日期）。

    通用稳健策略（不为每个站硬编码）：
      1. 正则扫所有 <a href=...>...</a>，取 href + 锚文本
      2. 用 _looks_like_article_url 过滤掉导航/页脚/外站
      3. per-host 启发（_LISTING_HOST_HINTS）：若该 host 配了"文章必含路径段"，
         只保留含该段的链接（强过滤，去掉同站的非文章页）
      4. 锚文本太短（<15 字）视为非标题（如 "Read more" / 图标链接），丢弃
      5. 同 URL 去重，保留首个出现（通常是主标题链接而非缩略图链接）
      6. 尽力从锚点后方一小段文本里抓发布日期

    返回 [{"title", "link", "published"}...]，按出现顺序（列表页通常已按时间倒序）。
    """
    if not html:
        return []
    base_host = urlparse(base_url).netloc
    host_hint = None
    for host_sub, hint in _LISTING_HOST_HINTS.items():
        if host_sub in base_host:
            host_hint = hint
            break

    # 抓 <a ...>...</a>，同时保留 href 与锚文本
    anchor_re = re.compile(r'(?is)<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*?>(.*?)</a>')
    seen = set()
    results: List[Dict[str, Any]] = []
    for m in anchor_re.finditer(html):
        href_raw = m.group(1).strip()
        anchor_html = m.group(2)
        # 绝对化
        href = urljoin(base_url, href_raw)
        href = href.split("#")[0].rstrip("/")
        if not href or href in seen:
            continue
        # per-host 强启发优先
        if host_hint is not None:
            if host_hint not in href.lower():
                continue
        else:
            if not _looks_like_article_url(href, base_host):
                continue
        title = clean_html_text(re.sub(r"(?is)<[^>]+>", " ", anchor_html))
        if len(title) < 15:
            continue  # 太短不像标题
        # 抓锚点之后一小段文本里的日期（很多站把日期放在标题相邻处）
        tail = html[m.end(): m.end() + 400]
        published = _extract_nearby_date(clean_html_text(re.sub(r"(?is)<[^>]+>", " ", tail)))
        seen.add(href)
        results.append({
            "title": title,
            "link": href,
            "published": published,
            "description": "",
            "author": "",
        })
    return results


# ============================================================================
# 全文提取模块
# ============================================================================


def clean_html_text(text: str) -> str:
    """Convert rough HTML/text fragments to readable plain text."""
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_non_full_text(url: str, full_text: Optional[str]) -> Optional[str]:
    """判定采到的是否"非正文本体"（abstract / 截断 / defer 链接 / 付费墙）。

    返回 None = 是正文本体；返回字符串 = 是非正文，字符串为原因码。

    背景：0608 HTL erosion 事故——cron 给"采到的 642 字 arXiv abstract"打 8.0
    通过推送。根因不是评分宽容，是 scoring 评的是"这篇文章的理念"不是
    "采到的素材本身"。修法：素材层就拒掉非正文，根本不让它进 candidate 池，
    scoring 自然评不到。

    规则（命中即返回非空原因，按优先级）：
      1. URL 路径硬识别：ssrn.com/abstract=*、jstor.org/stable/* 等已知"摘要页"路径。
         arxiv.org/abs/* 单独处理：HTML 页面可能含全文，按字数 + section 标记区分。
      2. 付费墙截断：短正文（< 300 词）+ 含付费/登录提示 → paywall_truncation。
         0609/0612 Stratechery 付费截断同源教训，status=fetched 漏判修复。
      3. 正文内容硬识别：full_text 含"defer 链接"句式（中英双语）
      4. 正文长度对深度文体过短：arXiv / ICML / NeurIPS / position paper 等
         深度论文路径 + word_count < 2000 → 摘要嫌疑
    """
    if not full_text:
        return None  # 没采到正文 → 后续 markdown=None 走废稿路径，不在这里判
    text = full_text.lower()
    url_l = url.lower()
    word_count = len(full_text.split())

    # 规则 1: 已知摘要页 URL（arxiv.org/abs/ 单独处理见下）
    abstract_url_patterns = [
        ("ssrn.com/abstract=", "ssrn_abstract_page"),
        ("jstor.org/stable/", "jstor_landing_page"),
        ("papers.nips.cc/paper/", "nips_landing_page"),
        ("openreview.net/forum?", "openreview_landing"),
        # 2026-07-06 audit 反传：PubMed 记录页 (pubmed.ncbi.nlm.nih.gov/<pmid>) 只有单段
        # abstract + NCBI chrome，非全文。本日 CGM 综述残文漏过入库到 scoring 层（audit_caveat）。
        ("pubmed.ncbi.nlm.nih.gov/", "pubmed_abstract_page"),
    ]
    for pat, code in abstract_url_patterns:
        if pat in url_l:
            return code

    # 规则 1b: arxiv.org/abs/ 特殊处理——HTML 页面可能含全文
    # 0612 "From Confident Closing to Silent Failure" (abs/2606.09863, 9052字) 误杀教训
    if "arxiv.org/abs/" in url_l:
        full_text_section_markers = ["conclusion", "appendix", "references"]
        has_full_sections = sum(1 for m in full_text_section_markers if m in text) >= 2
        if has_full_sections and word_count >= 3000:
            pass  # 有完整 section 结构且字数充足 → 视为全文，放行
        else:
            return "arxiv_abstract_page"

    # 规则 2: 付费墙截断
    # 0609/0612 Stratechery / 0615 interconnects AGI governance 同源
    # 教训：旧版 word_count < 300 阈值偏低，Substack 免费预览段常 800-1500 字
    # （0615 这篇 1222 字漏判，priority=high 推送但正文实际被付费墙截断）
    # 修法：分强/弱信号——强信号（付费墙独有句式）即标，不靠字数；
    #        弱信号（subscribe 等正文常见词）保留 word_count < 300 兜底
    paywall_strong_signals = [
        "this post is for paid subscribers",
        "already a paid subscriber",
        "upgrade to paid to",
        "subscribe to keep reading",
        "subscribe to continue reading",
        "this article is for subscribers",
        "this is a preview",
        "members-only content",
        # Foreign Affairs / 学术期刊系强信号（0616 FA《How to Beat an Autocrat》+
        # FA《False Promise US-China Stability》两文 word_count 18761/25147 均含
        # 但弱信号 word_count<300 兜底不触发；FA 这些字面是付费墙独有的，正文不会出现）
        "subscribe to unlock this feature",
        "this is a subscriber-only feature",
        "subscribe now or sign in",
        # Peter Attia MD (peterattiamd.com) Premium 截断专属句式（0626/0627 连续
        # 漏判：Lung cancer screening 等前置健康正文 ~1000+ 字预览后截断，旧词表未覆盖
        # 且 word_count>300 弱信号兜底不触发。这两句是 premium 截断专属，免费全文不出现）
        "this content is an exclusive benefit to premium members",
        "already a subscriber? login here",
        # Substack "分享解锁" 截断变体（0627 漏判：Noah Smith《China is winning
        # the other tech race》640字预览后截断含此句，status=fetched 放行混进候选池
        # 被日推分身打分时抓出。word_count>300 弱信号兜底不触发。"continue reading
        # this post for free, courtesy of <作者>" 是 Substack 分享链解锁专属句式，
        # 免费全文不出现；配 "or purchase a paid subscription" 双保险）
        "continue reading this post for free",
        "or purchase a paid subscription",
        "登录后继续阅读",
        "订阅后继续阅读",
        "付费会员可见",
        "付费订阅",
    ]
    # 0617 修：FA 'subscribe now or sign in' 在 FA 4539 字完整免费文头部 UI 按钮区
    # (Listen/Save/Subscribe 按钮)误判事故 — 真因不是位置而是该字面在 FA 文章通用 UI
    # 区域出现，不是 paywall 截断信号；类似 'subscribe to unlock this feature' 也是 UI
    # 字符。FA 真截断标志是 'You are reading a free article' 在文中（这恰说明是免费可
    # 读全文应放行）+ 'subscribe to foreign affairs to get unlimited access' 文末
    # （会同时出现于免费文，不能据此判定截断）。
    # 修法：'subscribe now or sign in' / 'subscribe to unlock this feature' /
    # 'this is a subscriber-only feature' 从强信号降级（FA 通用 UI 字符不是付费墙截
    # 断专属）；保留真截断专属字面(Substack 系)
    fa_ui_signals = {
        "subscribe now or sign in",
        "subscribe to unlock this feature",
        "this is a subscriber-only feature",
    }
    for signal in paywall_strong_signals:
        if signal.lower() in fa_ui_signals:
            # FA 通用 UI 字符 + 长文（>3000 字）→ 默认是免费文带 UI 按钮放行；
            # 短文（<3000）仍按截断处理（保 0615 interconnects 案有效）
            if signal in text and word_count <= 3000:
                return "paywall_truncation"
            continue
        if signal in text:
            return "paywall_truncation"
    paywall_weak_signals = [
        "subscribe", "paywall", "for subscribers", "log in to read",
        "login to read", "sign in to read", "free trial", "become a member",
        "members only", "premium content",
        "付费内容", "订阅后阅读", "登录后阅读",
    ]
    if word_count < 300:
        for signal in paywall_weak_signals:
            if signal in text:
                return "paywall_truncation"

    # 规则 3: defer 链接句式（采到的正文承认"去别处读"）
    defer_phrases_en = [
        "view pdf",
        "read the full paper",
        "see full text",
        "full text available at",
        "for the complete paper",
        "read more at",
    ]
    defer_phrases_zh = ["见原文", "见 pdf", "见完整论文", "完整论文为 pdf", "详见原文链接"]
    for phrase in defer_phrases_en + defer_phrases_zh:
        if phrase in text:
            # 排除尾注/参考文献区域的合理出现：只在正文前 30% 内命中才算 defer
            idx = text.find(phrase)
            if idx < len(text) * 0.3:
                return f"defer_link:{phrase[:30]}"

    # 规则 4: 深度文体 + 过短 = 摘要嫌疑
    deep_paper_indicators = [
        "arxiv.org",
        "icml",
        "neurips",
        "iclr",
        "position paper",
        "abstract:",
    ]
    if word_count < 2000:
        for ind in deep_paper_indicators:
            if ind in url_l or ind in text[:500]:
                return f"deep_paper_too_short:{word_count}w"

    return None


def normalize_source_url(url: str) -> str:
    """改写已知"摘要页/索引页"类 URL 到全文 URL。

    背景：0608 HTL erosion 事故——arXiv abstract 页只有 642 字摘要，
    论文正文在 PDF/HTML 路径上，采到 abstract 直接进推送 = 给链接让会员
    自己去原文读，违反"伴读卡正文自足"原则。

    arXiv 改写规则：
      https://arxiv.org/abs/<id>      → https://arxiv.org/html/<id>
      https://arxiv.org/abs/<id>v<N>  → https://arxiv.org/html/<id>v<N>
      arXiv HTML 实验版（arxiv.org/html/<id>）有完整正文，newspaper3k 可解析。
      若 HTML 路径不存在（部分老论文未渲染 HTML），上层 _simple_extract/_do_download_parse
      会返回 None，候选自然出局——配合 scoring 关⓪.5 完整性门兜底。
    """
    m = re.match(r"^https?://arxiv\.org/abs/(\d+\.\d+(?:v\d+)?)(?:/|$|\?|#)", url)
    if m:
        return f"https://arxiv.org/html/{m.group(1)}"
    return url


class FullTextExtractor:
    """全文提取器"""

    @staticmethod
    def _do_download_parse(url: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """在独立线程中执行 newspaper3k 下载和解析（隔离阻塞调用）"""
        config = NewspaperConfig()
        config.browser_user_agent = Config.USER_AGENT
        config.request_timeout = Config.REQUEST_TIMEOUT
        article = Article(url, config=config)
        article.download()
        article.parse()
        text = article.text
        if not text or len(text.split()) < 50:
            return None, {}
        metadata = {
            "authors": article.authors,
            "publish_date": article.publish_date,
            "top_image": article.top_image,
        }
        return text, metadata

    @staticmethod
    def _simple_extract(url: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """Stdlib fallback extractor so the skill does not require external scripts."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            req = Request(url, headers={"User-Agent": Config.USER_AGENT})
            with urlopen(req, timeout=Config.REQUEST_TIMEOUT, context=ssl_context) as resp:
                raw = resp.read(2_000_000)
                charset = resp.headers.get_content_charset() or "utf-8"

            html = raw.decode(charset, errors="ignore")
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
            title = clean_html_text(title_match.group(1)) if title_match else ""

            html = re.sub(r"(?is)<!--.*?-->", " ", html)
            html = re.sub(r"(?is)<(script|style|noscript|svg|form|nav|footer|header)[^>]*>.*?</\1>", " ", html)
            html = re.sub(r"(?is)<br\s*/?>", "\n", html)
            html = re.sub(r"(?is)</(p|div|section|article|li|h[1-6])>", "\n", html)
            text = clean_html_text(re.sub(r"(?is)<[^>]+>", " ", html))
            if len(text.split()) < 80:
                return None, {}
            return text, {"authors": [], "publish_date": None, "top_image": "", "title": title}
        except Exception as exc:
            print(f"[warn] fallback 提取失败: {url}: {exc}", flush=True)
            return None, {}

    def extract(self, url: str, timeout: int = None) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        提取文章全文，带硬超时保护。

        使用 ThreadPoolExecutor + future.result(timeout) 实现超时，
        避免 signal.alarm 在非主线程或 C 扩展中失效的问题。

        Returns:
            (full_text, metadata)
            metadata 包含: authors, publish_date, top_image
        """
        url = normalize_source_url(url)
        if not HAS_NEWSPAPER:
            return self._simple_extract(url)

        if timeout is None:
            timeout = Config.EXTRACT_TIMEOUT_SEC

        # 短暂延时避免被反爬封禁
        time.sleep(random.uniform(Config.DELAY_MIN, Config.DELAY_MAX))

        try:
            # 不用 with 语句，避免 __exit__ 等待 thread 结束（wait=True 是默认行为）
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self._do_download_parse, url)
            try:
                result = future.result(timeout=timeout)
                executor.shutdown(wait=False)
                return result
            except concurrent.futures.TimeoutError:
                print(f"[warn] 提取超时({timeout}s): {url}", flush=True)
                executor.shutdown(wait=False)  # 不等线程结束，继续下一篇
                return None, {}
        except ArticleException as e:
            print(f"[warn] 提取失败: {url}: {e}", flush=True)
            return self._simple_extract(url)
        except Exception as e:
            print(f"[warn] 提取异常: {url}: {e}", flush=True)
            return self._simple_extract(url)


# ============================================================================
# 分类与过滤模块
# ============================================================================


class ArticleClassifier:
    """文章分类器"""

    # 分类映射规则（目录名使用英文小写）
    CATEGORY_RULES = {
        "ict": [
            "ai",
            "ml",
            "systems",
            "startup",
            "engineering",
            "programming",
            "software",
            "technology",
            "deep-learning",
            "machine-learning",
        ],
        "economics": [
            "economics",
            "business",
            "strategy",
            "finance",
            "markets",
            "investing",
            "venture-capital",
            "management",
        ],
        "health": [
            "health",
            "medicine",
            "nutrition",
            "fitness",
            "biology",
            "neuroscience",
            "psychology",
            "longevity",
        ],
        "law": [
            "law",
            "policy",
            "privacy",
            "regulation",
            "legal",
            "copyright",
            "intellectual-property",
        ],
    }

    # 源名称直接映射
    SOURCE_CATEGORY_MAP = {
        # ict
        "Paul Graham": "ict",
        "Lilian Weng": "ict",
        "Andrej Karpathy": "ict",
        "Chip Huyen": "ict",
        "Simon Willison": "ict",
        "The Morning Paper": "ict",
        "Hacker News (Best)": "ict",
        "Distill.pub": "ict",
        "OpenAI Blog": "ict",
        "Google AI Blog": "ict",
        "DeepMind Blog": "ict",
        # economics
        "Ben Thompson (Stratechery)": "economics",
        "Matt Levine (Money Stuff)": "economics",
        "Tyler Cowen (Marginal Revolution)": "economics",
        "The Economist": "economics",
        "Financial Times": "economics",
        "a]": "economics",
        # health
        "NIH News Releases": "health",
        "CDC Newsroom": "health",
        "Peter Attia": "health",
        "Huberman Lab": "health",
        # law
        "SCOTUSblog": "law",
        "The Verge (Policy)": "law",
        "EFF Updates": "law",
        "Cory Doctorow": "law",
    }

    def classify(self, source: Dict[str, Any]) -> str:
        """基于源信息分类文章"""
        source_name = source.get("name", "")

        # 规则1：源名称直接映射
        if source_name in self.SOURCE_CATEGORY_MAP:
            return self.SOURCE_CATEGORY_MAP[source_name]

        # 规则2：基于 tags 分类
        tags = source.get("tags", [])
        if isinstance(tags, list):
            tags_str = " ".join(tags).lower()
            for category, keywords in self.CATEGORY_RULES.items():
                if any(kw in tags_str for kw in keywords):
                    return category

        # 规则3：基于源类型推断
        source_type = source.get("type", "")
        if source_type == "institution":
            name_lower = source_name.lower()
            if any(h in name_lower for h in ["nih", "cdc", "who", "fda"]):
                return "health"
            if any(e in name_lower for e in ["fed", "imf", "world bank"]):
                return "economics"

        return "general"


class ArticleFilter:
    """文章过滤器"""

    def should_collect(
        self,
        article: Dict[str, Any],
        source: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        判断是否采集文章

        Returns:
            (should_collect, reason)
        """
        word_count = article.get("word_count", 0)
        author = article.get("author", source.get("name", ""))
        title = article.get("title", "").lower()
        filter_rules = source.get("filter_rules", {})

        # Gate 0: 源级别排除模式（exclude_title_patterns）
        exclude_patterns = filter_rules.get("exclude_title_patterns", [])
        for pattern in exclude_patterns:
            if pattern.lower() in title:
                return False, f"标题匹配排除模式: {pattern}"

        # Gate 0.5: 源级别包含模式（include_topic_keywords）
        include_keywords = filter_rules.get("include_topic_keywords", [])
        if include_keywords:
            # 如果定义了包含关键词，则必须匹配至少一个
            matched = any(kw.lower() in title for kw in include_keywords)
            if not matched:
                return False, "标题不含任何指定topic关键词"

        # Gate 1: 字数门槛（如果有全文）
        if word_count > 0 and word_count < Config.MIN_WORD_COUNT:
            return False, f"字数不足（{word_count} < {Config.MIN_WORD_COUNT}）"

        # Gate 2: 作者白名单（高优先级）
        if author in Config.vip_authors():
            return True, "作者在白名单"

        # Gate 3: 关键词匹配
        if any(kw in title for kw in Config.HIGH_VALUE_KEYWORDS):
            return True, "标题包含高价值关键词"

        # Gate 4: 默认采集
        return True, "默认采集"


# ============================================================================
# Markdown 生成模块
# ============================================================================


def slugify(text: str, max_length: int = 50) -> str:
    """将文本转换为 URL 友好的 slug"""
    # 移除非 ASCII 字符
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    # 转小写，替换空格和特殊字符
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:max_length]

def calculate_reading_time(word_count: int, wpm: int = 250) -> str:
    """计算阅读时间"""
    minutes = max(1, round(word_count / wpm))
    return f"{minutes}分钟"

def calculate_priority(
    article: Dict[str, Any],
    source: Dict[str, Any],
) -> Tuple[str, int]:
    """计算优先级"""
    score = 0
    author = article.get("author", source.get("name", ""))
    title = article.get("title", "").lower()
    published = article.get("published")

    # 维度1：作者权威性（0-40分）——名单与分值见 Config.AUTHOR_SCORES，可由 profile 覆盖
    score += Config.author_scores().get(author, Config.DEFAULT_AUTHOR_SCORE)

    # 维度2：新鲜度（0-30分）
    if published and isinstance(published, datetime):
        days_old = (datetime.now(timezone.utc) - published).days
        if days_old <= 7:
            score += 30
        elif days_old <= 30:
            score += 20
        elif days_old <= 90:
            score += 10

    # 维度3：高观点潜力（0-30分）
    if any(kw in title for kw in ["principle", "law", "paradox"]):
        score += 30
    elif any(kw in title for kw in ["framework", "theory", "model"]):
        score += 25
    elif any(kw in title for kw in ["strategy", "pattern", "how to"]):
        score += 20
    else:
        score += 10

    # 转换为标签
    if score >= 70:
        priority = "high"
    elif score >= 50:
        priority = "medium"
    else:
        priority = "low"

    return priority, score

def generate_markdown(
    article: Dict[str, Any],
    source: Dict[str, Any],
    category: str,
) -> Optional[str]:
    """生成 Markdown 内容，如果全文提取失败则返回 None"""
    now = datetime.now().strftime("%Y-%m-%d")
    full_text = article.get("full_text") or ""  # 确保不是 None

    # 全文提取失败检查：必须有实质内容
    if not full_text or len(full_text.strip()) < Config.MIN_WORD_COUNT:
        return None

    # 完整性判定：采到的是否"非正文本体"（abstract / defer 链接 / 过短深度文体）
    # 命中 → 标 status: non_full_text，scoring 跳过这类候选；不返回 None（保留素材便于审计）
    non_full_text_reason = detect_non_full_text(article.get("link", ""), full_text)

    word_count = article.get("word_count") or len(full_text.split())
    reading_time = calculate_reading_time(word_count)
    priority, score_100 = calculate_priority(article, source)

    # 处理 tags
    tags = source.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags_str = json.dumps(tags, ensure_ascii=False)

    # 处理作者
    author = article.get("author", "")
    if not author:
        author = source.get("name", "Unknown")

    # 处理发布日期
    published = article.get("published")
    if isinstance(published, datetime):
        published_str = published.strftime("%Y-%m-%d")
    else:
        published_str = "Unknown"

    # 使用全文内容（已在函数开头验证过）
    content = full_text

    markdown = f"""---
title: "{article.get('title', 'Untitled')}"
author: "{author}"
source: "{source.get('name', 'Unknown')}"
source_url: "{source.get('url', '')}"
published: "{published_str}"
fetched: "{now}"
article_url: "{article.get('link', '')}"
category: "{category}"
tags: {tags_str}
word_count: {word_count}
reading_time: "{reading_time}"
status: "{'non_full_text' if non_full_text_reason else 'fetched'}"
{f'non_full_text_reason: "{non_full_text_reason}"' if non_full_text_reason else ''}
priority: "{priority}"
score_100: {score_100}
---

# {article.get('title', 'Untitled')}

{content}

---

## 📝 Personal Notes（个人笔记）
<!-- 执行020规则（SQ3R阅读）后填写 -->

### Step 0.5: Background Research（背景研究）
**作者背景**：
-

**文章在领域中的地位**：
-

### Step 1: Survey（快速浏览）
**文章结构**：
-

**核心论点**：
-

### Step 2: Question（提出问题）
1.
2.
3.

### Step 3: Read（精读）
**关键段落摘录**：
-

**个人理解**：
-

### Step 4: Recite（复述）
**用自己的话复述核心观点**：
-

### Step 5: Review（回顾）
**验证理解**：
- [ ] 能用自己的话解释核心概念
- [ ] 能举出实际应用场景
- [ ] 能识别作者的潜在偏见

---

## 💡 Key Insights（高观点提炼）
<!-- 读完后填写。分层用 DIKW 模型：只提取 Knowledge 与 Wisdom 层 -->

### DIKW过滤器
**这是什么层级？**
- [ ] Data（事实）→ 不提取
- [ ] Information（结构化事实）→ 不提取
- [ ] Knowledge（可复用模式）→ 可提取
- [ ] Wisdom（可指导决策的原理）→ 必须提取

**判断理由**：
-

### 核心洞察

#### 洞察1：[标题]
**原文段落**：
> [摘录原文]

**个人提炼**：
-

**DIKW层级**：Knowledge / Wisdom

**关联原理/大师**：
- 科学原理：
- 思想大师：
- 最佳实践：

**决策指导**：
- **Filter（读什么）**：
- **Action（做什么）**：

**0.85分标准自检**：
- [ ] 一句话概括（≤50字）
- [ ] 关联权威背书
- [ ] 明确决策指导
- [ ] 能改变具体行为
"""

    return markdown


# ============================================================================
# 主采集器
# ============================================================================


class ArticleCollector:
    """文章采集器"""

    def __init__(self, skip_resume: bool = False):
        self.sources = load_sources()
        self.extractor = FullTextExtractor()
        self.classifier = ArticleClassifier()
        self.filter = ArticleFilter()
        self.collected_urls: set = set()  # 去重
        self.collected_keys: set = set()  # epub 归一化去重 key（剥 scheme，不受前缀变更影响）

        # 断点续传：从日志加载已采集的 URL
        if not skip_resume:
            self._load_collected_urls()
        else:
            print("[info] 已跳过断点续传（--force 模式）")

    def _load_collected_urls(self):
        """从采集日志加载已采集的 URL（支持断点续传）"""
        if not Config.COLLECTION_LOG.exists():
            return

        try:
            with open(Config.COLLECTION_LOG, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        # 支持新旧两种格式
                        # 新格式: {"url": "...", ...}
                        # 旧格式: {"articles": [{"url": "..."}, ...]}}
                        if "url" in entry:
                            self.collected_urls.add(entry["url"])
                            self.collected_keys.add(
                                self._epub_dedup_key(entry["url"])
                            )
                        elif "articles" in entry:
                            for article in entry.get("articles", []):
                                url = article.get("url") or article.get("link")
                                if url:
                                    self.collected_urls.add(url)
                                    self.collected_keys.add(
                                        self._epub_dedup_key(url)
                                    )
                    except json.JSONDecodeError:
                        continue

            if self.collected_urls:
                print(
                    f"[info] 已加载 {len(self.collected_urls)} 个已采集 URL（断点续传）"
                )
        except Exception as e:
            print(f"[warn] 加载采集日志失败: {e}")

    def collect(self, days: int = 7, dry_run: bool = False) -> List[Dict[str, Any]]:
        """
        采集最近 N 天的文章

        Args:
            days: 采集时间窗口
            dry_run: 干跑模式（不保存文件）

        Returns:
            采集结果列表
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = []
        stats = {"total": 0, "collected": 0, "filtered": 0, "failed": 0}
        collect_start = time.time()

        print(f"\n{'='*60}", flush=True)
        print(f"文章采集器 - 采集最近 {days} 天的文章", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"源数量: {len(self.sources)}", flush=True)
        print(f"时间窗口: {cutoff.strftime('%Y-%m-%d')} 到今天", flush=True)
        print(f"干跑模式: {'是' if dry_run else '否'}", flush=True)
        print(f"单源文章上限: {Config.MAX_ARTICLES_PER_SOURCE} 篇", flush=True)
        print(f"{'='*60}\n", flush=True)

        for i, source in enumerate(self.sources, 1):
            source_name = source.get("name", "Unknown")

            # 源暂时不可用 → 跳过拉取（不删源，身份/名册 tag 不动）。
            # 设计决定：hero 仍是 hero，源暂时不可用就标记跳过，别每天空拉死链。
            # 命中 availability: unavailable 即跳过；日后恢复改回 available 即回归。
            if str(source.get("availability", "available")).lower() == "unavailable":
                reason = source.get("unavailable_reason", "")
                since = source.get("unavailable_since", "")
                meta = " ".join(x for x in (f"since {since}" if since else "", f"— {reason}" if reason else "") if x)
                print(f"[{i}/{len(self.sources)}] ⏸️  {source_name} - 源暂不可用，跳过 {meta}", flush=True)
                stats["skipped_unavailable"] = stats.get("skipped_unavailable", 0) + 1
                continue

            rss_url = source.get("rss_url", "")
            sub_method = str(source.get("subscription_method", "") or "").lower()

            # 无 RSS 但配了 scraper：走列表页抓取分支（见 _collect_scraper_entries）
            if not rss_url:
                if sub_method == "scraper" and (source.get("scraper_target") or source.get("url")):
                    source_start = time.time()
                    print(f"[{i}/{len(self.sources)}] 🕸️  {source_name} (scraper)", flush=True)
                    try:
                        category = self.classifier.classify(source)
                        recent_entries = self._collect_scraper_entries(source, cutoff)
                        for entry in recent_entries:
                            self._ingest_article(
                                entry, source, category, source_name,
                                stats, results, dry_run, source_start,
                            )
                        source_elapsed = time.time() - source_start
                        print(f"           ✔ 源完成，耗时 {source_elapsed:.1f}s", flush=True)
                    except Exception as e:
                        source_elapsed = time.time() - source_start
                        print(
                            f"           ❌ 源处理异常，跳过 ({source_elapsed:.1f}s): {e}",
                            flush=True,
                        )
                    continue
                # 无 RSS 配了 epub：走外刊整期拆单篇分支（见 _collect_epub_entries）
                if sub_method == "epub" and source.get("epub_api_dir"):
                    source_start = time.time()
                    print(f"[{i}/{len(self.sources)}] 📚 {source_name} (epub)", flush=True)
                    try:
                        epub_entries = self._collect_epub_entries(source, cutoff)
                        for entry in epub_entries:
                            # epub 每篇栏目自带域，逐篇用自己的 category（非整源一域）
                            category = entry.get("_epub_domain") or "law"
                            self._ingest_article(
                                entry, source, category, source_name,
                                stats, results, dry_run, source_start,
                            )
                        source_elapsed = time.time() - source_start
                        print(f"           ✔ 源完成，耗时 {source_elapsed:.1f}s", flush=True)
                    except Exception as e:
                        source_elapsed = time.time() - source_start
                        print(
                            f"           ❌ 源处理异常，跳过 ({source_elapsed:.1f}s): {e}",
                            flush=True,
                        )
                    continue
                print(f"[{i}/{len(self.sources)}] ⏭️  {source_name} - 无 RSS URL", flush=True)
                continue

            source_start = time.time()
            print(f"[{i}/{len(self.sources)}] 📡 {source_name}", flush=True)

            try:
                # 获取 feed
                content = fetch_feed(rss_url)
                if not content:
                    print(f"           ❌ 获取 feed 失败", flush=True)
                    continue

                # 解析文章
                entries = parse_feed(content)

                # 分类
                category = self.classifier.classify(source)

                # 过滤时间窗口内的文章（最新优先）
                recent_entries = []
                for entry in entries:
                    published = entry.get("published")
                    if not isinstance(published, datetime):
                        continue
                    if published < cutoff:
                        continue
                    url = entry.get("link", "")
                    if not url:
                        continue
                    if url in self.collected_urls:
                        continue
                    recent_entries.append(entry)

                # 按发布时间排序（最新优先），并限制单源文章数
                recent_entries.sort(
                    key=lambda e: e.get("published") or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                total_in_window = len(recent_entries)
                # 每源配额优先于全局：filter_rules.max_per_batch 有值就用它，
                # 否则回落全局 MAX_ARTICLES_PER_SOURCE。用于给 law 刷屏源（过 8.0=0）限流，
                # 止住采集空转（2026-07-06 产能盘点：law 两周采 962 篇产 0 合格文）。
                per_source_cap = source.get("filter_rules", {}).get(
                    "max_per_batch", Config.MAX_ARTICLES_PER_SOURCE
                )
                recent_entries = recent_entries[:per_source_cap]
                skipped_by_limit = total_in_window - len(recent_entries)

                limit_note = (
                    f"（限额截断 {skipped_by_limit} 篇）" if skipped_by_limit > 0 else ""
                )
                print(
                    f"           📄 时间窗口内 {total_in_window} 篇，处理 {len(recent_entries)} 篇{limit_note}",
                    flush=True,
                )

                for entry in recent_entries:
                    self._ingest_article(
                        entry, source, category, source_name,
                        stats, results, dry_run, source_start,
                    )

                source_elapsed = time.time() - source_start
                print(f"           ✔ 源完成，耗时 {source_elapsed:.1f}s", flush=True)

            except Exception as e:
                source_elapsed = time.time() - source_start
                print(
                    f"           ❌ 源处理异常，跳过 ({source_elapsed:.1f}s): {e}",
                    flush=True,
                )
                continue

        # 打印统计
        total_elapsed = time.time() - collect_start
        print(f"\n{'='*60}", flush=True)
        print(f"采集完成！总耗时 {total_elapsed:.0f}s", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"检查文章数: {stats['total']}", flush=True)
        print(f"采集成功数: {stats['collected']}", flush=True)
        print(f"过滤数量:   {stats['filtered']}", flush=True)
        print(f"提取失败数: {stats['failed']}", flush=True)
        if stats.get("skipped_unavailable"):
            print(f"暂不可用跳过: {stats['skipped_unavailable']} 源（availability: unavailable）", flush=True)
        if stats["collected"] > 0:
            print(f"\n按领域分布:", flush=True)
            for cat in ["ict", "economics", "health", "law", "general"]:
                count = len([r for r in results if r.get("category") == cat])
                if count > 0:
                    print(f"  - {cat}: {count}篇", flush=True)
        print(f"{'='*60}\n", flush=True)

        # 保存日志 (bulk save disabled, using append)
        # if not dry_run and results:
        #     self._save_log(results)

        return results

    def _collect_scraper_entries(
        self, source: Dict[str, Any], cutoff: datetime
    ) -> List[Dict[str, Any]]:
        """scraper 源专用：抓列表页 → 解析文章链接 → 去重/时间窗过滤 → 返回待采 entry 列表。

        与 RSS 分支语义对齐（同样做 collected_urls 去重 + cutoff 过滤 + 限额截断），
        差别只在 entry 来源是列表页解析而非 RSS。无发布日期的链接默认放行（很多
        列表页不带日期），交给下游 _ingest_article 的全文提取 + detect_non_full_text 兜底。
        """
        listing_url = source.get("scraper_target") or source.get("url", "")
        html = fetch_html(listing_url)
        if not html:
            print(f"           ❌ 获取列表页失败: {listing_url}", flush=True)
            return []

        entries = parse_listing_page(html, listing_url)
        print(f"           🔗 列表页解析出 {len(entries)} 个候选链接", flush=True)

        recent_entries = []
        for entry in entries:
            url = entry.get("link", "")
            if not url:
                continue
            if url in self.collected_urls:
                continue
            published = entry.get("published")
            # 有日期的按时间窗过滤；无日期的放行（列表页常不带日期，下游兜底）
            if isinstance(published, datetime) and published < cutoff:
                continue
            recent_entries.append(entry)

        # 有日期的最新优先；无日期的排后（None 当极小值）
        recent_entries.sort(
            key=lambda e: e.get("published") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        total_in_window = len(recent_entries)
        # 每源配额优先于全局（与 RSS 分支对齐）：filter_rules.max_per_batch 有值就用它。
        per_source_cap = source.get("filter_rules", {}).get(
            "max_per_batch", Config.MAX_ARTICLES_PER_SOURCE
        )
        recent_entries = recent_entries[:per_source_cap]
        skipped_by_limit = total_in_window - len(recent_entries)
        limit_note = f"（限额截断 {skipped_by_limit} 篇）" if skipped_by_limit > 0 else ""
        print(
            f"           📄 时间窗口/去重后 {total_in_window} 篇，处理 {len(recent_entries)} 篇{limit_note}",
            flush=True,
        )
        return recent_entries

    # epub 栏目名 → companion 四域（栏目落域，非整源一域；一期含多域文章）
    _EPUB_SECTION_DOMAIN = {
        "leaders": None,          # 社论=机构立场体，8.0 门天然挡，标 None 走内容兜底但基本卡门
        "business": "economics",
        "finance": "economics",
        "finance & economics": "economics",
        "economics": "economics",
        "science": "ict",
        "science & technology": "ict",
        "technology quarterly": "ict",
        "united states": "law",
        "china": "law",           # 中国栏含科技/经济/秩序，落域看内容，默认 law(秩序观察)
        "asia": "law",
        "middle east & africa": "law",
        "europe": "law",
        "britain": "law",
        "international": "law",
        "the americas": "law",
        "briefing": None,         # 深度调查，落域看内容
    }

    def _collect_epub_entries(
        self, source: Dict[str, Any], cutoff: datetime
    ) -> List[Dict[str, Any]]:
        """epub 源专用（经济学人等外刊整期）：GitHub API 定位最新期 → 下 epub →
        拆单篇 HTML（正文自带，url 是付费墙官网）→ 按栏目落域 → 返回带 full_text 的 entry。

        与 scraper 分支同构：同做 collected_urls 去重 + 限额截断；差别在 entry 来源
        是 epub zip 内拆包、且 full_text 预填（下游 _ingest_article 跳过在线 extract）。
        """
        import zipfile
        import io as _io

        api_dir = source.get("epub_api_dir", "")   # 如 01_economist
        repo = source.get("epub_repo", "hehonghui/awesome-english-ebooks")
        if not api_dir:
            print("           ❌ epub 源缺 epub_api_dir 配置", flush=True)
            return []

        # 1. GitHub API 列目录（名倒序），从最新期起找第一个真有 epub 的期。
        #    月刊(Wired/Atlantic)最新期常只上传 README、epub 滞后 → 回退到最近有 epub 的期，
        #    不空跑。回退上限 6 期，避免整刊无 epub 时无限翻。
        try:
            api = f"https://api.github.com/repos/{repo}/contents/{api_dir}"
            raw = fetch_feed(api)
            listing = json.loads(raw.decode("utf-8", "ignore")) if raw else []
            dirs = sorted(
                [x["name"] for x in listing if x.get("type") == "dir"], reverse=True
            )
            if not dirs:
                print("           ❌ epub 目录为空", flush=True)
                return []
        except Exception as e:
            print(f"           ❌ epub 目录列取失败: {e}", flush=True)
            return []

        # 2. 逐期找 epub 并下载（最新→回退）
        latest, data = None, None
        for cand_issue in dirs[:6]:
            try:
                api_issue = (
                    f"https://api.github.com/repos/{repo}/contents/{api_dir}/{cand_issue}"
                )
                raw2 = fetch_feed(api_issue)
                files = json.loads(raw2.decode("utf-8", "ignore")) if raw2 else []
                epub_file = next(
                    (f for f in files if f.get("name", "").endswith(".epub")), None
                )
                if not epub_file:
                    print(
                        f"           ⏭️  {cand_issue} 无 epub（未上传/README-only），回退上一期",
                        flush=True,
                    )
                    continue
                d = fetch_feed(epub_file.get("download_url"))
                if not d:
                    print(f"           ⏭️  {cand_issue} epub 下载失败，回退", flush=True)
                    continue
                latest, data = cand_issue, d
                print(
                    f"           📦 {latest} epub {len(data)//1024//1024}MB，拆包中",
                    flush=True,
                )
                break
            except Exception as e:
                print(f"           ⏭️  {cand_issue} 处理异常，回退: {e}", flush=True)
                continue
        if data is None:
            print("           ❌ 最近 6 期均无可用 epub，跳过", flush=True)
            return []

        # 3. 拆单篇：spine 顺序遍历，栏目标题（<h..>或首行）落域，正文提取
        entries = []
        try:
            z = zipfile.ZipFile(_io.BytesIO(data))
            names = z.namelist()
            opf = next((n for n in names if n.endswith(".opf")), None)
            order = []
            if opf:
                o = z.read(opf).decode("utf-8", "ignore")
                ids = re.findall(r'<itemref idref="([^"]+)"', o)
                hrefs = dict(re.findall(r'<item id="([^"]+)"[^>]*href="([^"]+)"', o))
                order = [hrefs.get(i) for i in ids if hrefs.get(i)]

            # toc.ncx 的 navMap = 权威「文件→标题」映射（epub 标准，三刊通用）。
            # 优先用它取标题——New Yorker 正文无 h1/title 标题（<title>全是刊名），
            # 靠 body 猜标题不可靠；toc 里每篇真标题干净。取每文件首个 navPoint 标题。
            ncx_title = {}   # basename(文件) → 标题
            ncx = next((n for n in names if n.endswith(".ncx")), None)
            if ncx:
                try:
                    toc = z.read(ncx).decode("utf-8", "ignore")
                    for txt, src in re.findall(
                        r"<navPoint[^>]*>.*?<text>(.*?)</text>.*?<content src=\"([^\"]+)\"",
                        toc, re.S,
                    ):
                        key = src.split("#")[0].rsplit("/", 1)[-1]
                        t = unescape(re.sub(r"<[^>]+>", "", txt)).strip()
                        if key and t and key not in ncx_title:
                            ncx_title[key] = t[:200]
                except Exception:
                    ncx_title = {}
            # 无 opf 顺序则退化为全部 html
            html_files = order or [
                n for n in names if n.endswith((".html", ".xhtml"))
            ]

            current_section = None  # 栏目在独立 section 页里，顺读时记住上一个栏目
            issue_date = self._parse_epub_date(latest)
            base = ""
            if opf:
                base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
            for href in html_files:
                zpath = (base + href) if base and (base + href) in names else href
                if zpath not in names:
                    # 尝试无 base 直配
                    cand = next((n for n in names if n.endswith(href)), None)
                    if not cand:
                        continue
                    zpath = cand
                raw_html = z.read(zpath).decode("utf-8", "ignore")
                text = self._epub_clean_html(raw_html)
                # 标题优先取 toc 映射（权威、三刊通用），无则回退 h1/正文
                bn = zpath.rsplit("/", 1)[-1]
                title = ncx_title.get(bn) or self._epub_extract_title(raw_html, text)
                wc = len(text.split())

                # 栏目页（标题即栏目名）→ 更新 current_section，不当文章
                # 不限字数：经济学人"world this week"摘要页(Politics/Business)标题=栏目名
                # 但正文数百字，字数门漏过，靠标题=栏目名一律挡（防摘要页当文章入库）
                low_title = (title or "").strip().lower()
                if low_title in self._EPUB_SECTION_DOMAIN:
                    if wc < 60:
                        current_section = low_title
                    continue
                # 封面页/期刊名页/固定栏目摘要子页跳过（标题=刊名或固定栏目名、非文章）
                # 刊名页通用：TheEconomist.日期 / NewYorker.日期 / WIRED / The Atlantic 等
                bare = low_title.replace(" ", "").replace(".", "").rstrip("0123456789")
                if bare in ("theeconomist", "newyorker", "wired", "theatlantic",
                            "theneworker") \
                        or low_title in ("the economist", "the new yorker", "wired",
                                         "the atlantic", "contents", "magazine articles",
                                         "the world this week", "politics", "business",
                                         "kal's cartoon", "this week", "the talk of the town",
                                         # New Yorker 栏目汇总页（非单篇文章，标题=栏目名）
                                         "the critics", "briefly noted", "fiction",
                                         "letters", "obituary", "culture", "by invitation",
                                         "reporting & essays", "reporting &amp; essays",
                                         "shouts & murmurs", "shouts &amp; murmurs",
                                         "1843", "goings on", "the mail", "poems"):
                    continue
                # 正文太短（导航/图注/目录页）跳过
                if wc < 250:
                    continue

                domain = self._EPUB_SECTION_DOMAIN.get(current_section or "", None)
                # 栏目落 None（Leaders/Briefing/中国栏看内容）→ 关键词兜底落域
                if domain is None:
                    domain = self._epub_content_domain(text)

                # 伪 url：期次+slug，用于去重（官网真 url 付费墙不取）
                slug = slugify(title) or f"art-{len(entries)}"
                # 伪 url：刊名进路径（防跨刊同题误去重），scheme 恒为 epub://。
                # 去重按 _epub_dedup_key 归一化比对（剥 scheme），不受前缀变更影响——
                # 旧 economist://<issue>/<slug> 存量与新 epub://the-economist/... 归一到同 key，
                # 不需迁移存量（前缀改会破坏去重，修法＝把去重 key 与前缀解耦）。
                mag_name = re.split(r"[（(]", source.get("name", "epub"))[0].strip()
                mag_slug = slugify(mag_name) or "epub"
                pseudo_url = f"epub://{mag_slug}/{latest}/{slug}"
                if self._epub_dedup_key(pseudo_url) in self.collected_keys:
                    continue

                entries.append(
                    {
                        "title": title,
                        "link": pseudo_url,
                        "published": issue_date,
                        "author": mag_name,
                        "full_text": text,
                        "word_count": wc,
                        "_epub_domain": domain,
                        "_epub_section": current_section or "",
                    }
                )
        except Exception as e:
            print(f"           ❌ epub 拆包失败: {e}", flush=True)
            return []

        # 4. cutoff 过滤（整期一个日期）+ 限额截断
        if isinstance(issue_date, datetime) and issue_date < cutoff:
            print(
                f"           ⏭️  最新期 {latest} 早于时间窗，跳过", flush=True
            )
            return []
        total = len(entries)
        per_cap = source.get("filter_rules", {}).get(
            "max_per_batch", Config.MAX_ARTICLES_PER_SOURCE
        )
        entries = entries[:per_cap]
        note = f"（限额截断 {total - len(entries)} 篇）" if total > len(entries) else ""
        print(
            f"           📄 拆出 {total} 篇，处理 {len(entries)} 篇{note}", flush=True
        )
        return entries

    # ⚠️ 复盘沉淀的硬约束（改本段前必读）：
    # 1) 改 pseudo_url 前缀方案前必核 collection-log.jsonl 存量前缀——改 scheme=去重 key 变更，
    #    历史存量前缀不迁移则去重与存量断裂→重复入库。改前 grep 旧前缀计数，改后必须迁移存量
    #    URL 前缀（或去重按"期次+slug"与前缀解耦，如本方法）。去重 key 稳定性=采集器正确性硬约束。
    # 2) 存量字段批量修正后必跑一致性核验：改 author/url 派生逻辑时，已入库文的 article_url 前缀
    #    + collection-log.jsonl 的 url 前缀须同批迁移，grep 确认旧前缀归零。
    @staticmethod
    def _epub_dedup_key(url: str) -> str:
        """epub 伪 url 归一化去重 key：剥 scheme + 刊名段，取「期次/文章slug」尾部。
        旧 `economist://2026.07.11/foo` 与新 `epub://the-economist/2026.07.11/foo`
        都归一到 `2026.07.11/foo`——前缀变更不打断去重，存量无需迁移。
        非 epub 伪 url（真 http RSS/scraper url）原样返回，不影响其去重。
        """
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if scheme not in ("epub", "economist") and not scheme.startswith("the-"):
            return url  # 真实 http(s) url，原样
        parts = rest.split("/")
        # 取末两段「期次/slug」；epub:// 新格式首段是刊名，旧格式无刊名段——末两段一致
        return "/".join(parts[-2:]) if len(parts) >= 2 else rest

    @staticmethod
    def _parse_epub_date(dir_name: str) -> Optional[datetime]:
        """te_2026.07.11 → datetime(2026,7,11, tz=utc)"""
        m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", dir_name)
        if not m:
            return None
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    @staticmethod
    def _epub_clean_html(html: str) -> str:
        t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
        t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
        t = re.sub(r"<[^>]+>", " ", t)
        t = unescape(t)
        return re.sub(r"\s+", " ", t).strip()

    @staticmethod
    def _epub_extract_title(html: str, text: str) -> str:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S | re.I)
        if not m:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
        if m:
            return re.sub(r"<[^>]+>", "", unescape(m.group(1))).strip()[:200]
        return text[:80]

    @staticmethod
    def _epub_content_domain(text: str) -> str:
        """栏目落 None 时按内容关键词兜底落域（粗判，最终靠 8.0 评分）"""
        t = text.lower()
        ict = sum(t.count(k) for k in ["ai ", "chip", "semiconductor", "software",
                                       "model", "compute", "algorithm", "quantum"])
        econ = sum(t.count(k) for k in ["market", "economy", "trade", "gdp", "inflation",
                                        "investor", "bank", "currency", "fiscal"])
        health = sum(t.count(k) for k in ["disease", "patient", "drug", "vaccine",
                                          "dementia", "cancer", "clinical"])
        scores = {"ict": ict, "economics": econ, "health": health}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "law"

    def _ingest_article(
        self,
        entry: Dict[str, Any],
        source: Dict[str, Any],
        category: str,
        source_name: str,
        stats: Dict[str, int],
        results: List[Dict[str, Any]],
        dry_run: bool,
        source_start: float,
    ) -> None:
        """单篇文章入库（RSS 与 scraper 两分支共用：提取→过滤→生成 md→存盘→去重→记日志）。

        从 collect() 内联循环抽出，避免 RSS/scraper 两份入库代码漂移。
        detect_non_full_text 通过 generate_markdown 内部调用兜底（0608 HTL erosion 硬防线）。
        """
        stats["total"] += 1
        url = entry.get("link", "")
        published = entry.get("published")

        # 提取全文（dry-run 模式跳过，节省时间）
        # epub 等源：entry 已自带 full_text（正文从 epub 内拆出，url 是付费墙官网抓不到），
        # 直接用，跳过在线 extract。RSS/scraper 分支不填 full_text，行为不变。
        if entry.get("full_text"):
            full_text = entry.get("full_text")
            metadata = {"authors": [entry["author"]] if entry.get("author") else []}
        elif dry_run:
            full_text = None
            metadata = {}
        else:
            print(f"           ⏳ 提取: {url[:70]}...", flush=True)
            full_text, metadata = self.extractor.extract(url)
            if full_text is None:
                stats["failed"] += 1

        word_count = len(full_text.split()) if full_text else 0

        # 构建文章对象
        article = {
            "title": entry.get("title", ""),
            "link": url,
            "published": published,
            "description": entry.get("description", ""),
            "author": entry.get("author")
            or (
                metadata.get("authors", [None])[0]
                if metadata.get("authors")
                else ""
            ),
            "full_text": full_text,
            "word_count": word_count,
        }

        # 过滤
        should_collect, reason = self.filter.should_collect(article, source)
        if not should_collect:
            stats["filtered"] += 1
            print(f"           ⏭️  过滤: {article['title'][:40]} ({reason})", flush=True)
            return

        # 生成 Markdown
        if not dry_run:
            markdown = generate_markdown(article, source, category)

            # 全文提取失败，跳过保存
            if markdown is None:
                print(f"           ⚠️  内容过短/提取失败: {url}", flush=True)
                stats["failed"] += 1
                return

            filename = slugify(article["title"]) or f"article-{stats['collected']+1}"
            month_dir = datetime.now().strftime("%Y-%m")

            # 保存文件
            output_dir = Config.ARTICLE_POOL_DIR / category / month_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"{filename}.md"

            # 避免覆盖
            if output_file.exists():
                output_file = output_dir / f"{filename}-{int(time.time())}.md"

            output_file.write_text(markdown, encoding="utf-8")
            article["file_path"] = str(output_file)

            # 成功保存后，立即记录到日志（支持断点续传）
            self._append_log_entry(article, source_name, category)
            self.collected_urls.add(url)

        stats["collected"] += 1
        article["category"] = category
        article["source_name"] = source_name
        results.append(article)

        priority, score = calculate_priority(article, source)
        status_icon = "✅" if full_text else "⚠️"
        elapsed = time.time() - source_start
        print(
            f"           {status_icon} [{priority}:{score}] {article['title'][:40]}... ({elapsed:.0f}s)",
            flush=True,
        )

    def _append_log_entry(self, article: Dict[str, Any], source_name: str, category: str):
        """追加单条采集日志"""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "title": article.get("title"),
            "source": source_name,
            "category": category,
            "url": article.get("link"),
            "file_path": article.get("file_path", ""),
        }

        try:
            with open(Config.COLLECTION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[warn] 写入日志失败: {e}")

    def _save_log(self, results: List[Dict[str, Any]]):
        """
        已弃用: 日志现在通过 _append_log_entry 实时写入
        保留此方法仅为了兼容旧代码调用（如有），或做后续汇总处理
        """
        pass

    def status(self):
        """显示采集状态"""
        print(f"\n{'='*60}")
        print("文章采集器状态")
        print(f"{ '='*60}")

        # 源统计
        print(f"\n📊 信息源统计:")
        print(f"   总源数: {len(self.sources)}")

        # 按类型统计
        type_counts = {}
        for s in self.sources:
            t = s.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"   - {t}: {c}")

        # 文章池统计
        print(f"\n📁 文章池统计:")
        if Config.ARTICLE_POOL_DIR.exists():
            for cat in ["ict", "economics", "health", "law", "general"]:
                cat_dir = Config.ARTICLE_POOL_DIR / cat
                if cat_dir.exists():
                    count = sum(1 for f in cat_dir.rglob("*.md"))
                    print(f"   - {cat}: {count}篇")
        else:
            print("   ⚠️ 文章池目录不存在")

        # 日志统计
        print(f"\n📋 采集日志:")
        if Config.COLLECTION_LOG.exists():
            with open(Config.COLLECTION_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()
            print(f"   采集记录数: {len(lines)}")
            if lines:
                try:
                    last = json.loads(lines[-1])
                    print(f"   最后采集时间: {last.get('timestamp', 'N/A')[:19]}")
                    if "articles" in last:
                        print(f"   最后采集数量: {last.get('collected_count', 0)}篇")
                    else:
                        print(f"   最后采集文章: {last.get('title', 'Unknown')}")
                except:
                    print("   无法解析最后一条日志")
        else:
            print("   暂无采集记录")

        print(f"{ '='*60}\n")


# ============================================================================
# 评分与状态报告模块
# ============================================================================


def parse_yaml_header(file_path: Path) -> Tuple[Dict[str, Any], str]:
    """
    解析 Markdown 文件的 YAML 头部

    Returns:
        (yaml_dict, body_content)
    """
    content = file_path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return {}, content

    # 查找第二个 ---
    second_dash = content.find("---", 3)
    if second_dash == -1:
        return {}, content

    yaml_str = content[3:second_dash].strip()
    body = content[second_dash + 3:].strip()

    try:
        yaml_dict = yaml.safe_load(yaml_str)
        if not isinstance(yaml_dict, dict):
            yaml_dict = {}
    except yaml.YAMLError:
        yaml_dict = {}

    return yaml_dict, body


def write_yaml_header(file_path: Path, yaml_dict: Dict[str, Any], body: str):
    """将 YAML 头部写回 Markdown 文件"""
    yaml_str = yaml.dump(yaml_dict, allow_unicode=True, default_flow_style=False, sort_keys=False)
    content = f"---\n{yaml_str}---\n\n{body}"
    file_path.write_text(content, encoding="utf-8")


def rate_article(file_path: str, score: int, reason: Optional[str] = None):
    """
    为文章添加用户评分

    Args:
        file_path: 文章文件路径（相对或绝对）
        score: 评分 1-5
        reason: 评分理由（可选）
    """
    # 解析路径
    path = Path(file_path)
    if not path.is_absolute():
        # 尝试在 translated/ 目录查找
        translated_path = Config.ARTICLE_POOL_DIR / "translated" / file_path
        if translated_path.exists():
            path = translated_path
        else:
            # 尝试在 article-pool 下查找
            for p in Config.ARTICLE_POOL_DIR.rglob(file_path):
                path = p
                break

    if not path.exists():
        print(f"[error] 文件不存在: {file_path}")
        return False

    # 解析 YAML 头部
    yaml_dict, body = parse_yaml_header(path)
    if not yaml_dict:
        print(f"[error] 无法解析 YAML 头部: {file_path}")
        return False

    # 更新评分字段
    today = datetime.now().strftime("%Y-%m-%d")
    yaml_dict["user_rating"] = score
    yaml_dict["rated_date"] = today
    yaml_dict["status"] = "rated"  # 更新状态
    if reason:
        yaml_dict["rating_reason"] = reason

    # 写回文件
    write_yaml_header(path, yaml_dict, body)

    # 记录到反馈日志
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file": str(path),
        "title": yaml_dict.get("title", ""),
        "score": score,
        "reason": reason,
    }

    try:
        with open(Config.FEEDBACK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[warn] 写入反馈日志失败: {e}")

    print(f"[ok] 已记录评分: {yaml_dict.get('title', file_path)} → {score}分")
    return True


def generate_intake_status() -> str:
    """
    生成摄取现状报告（按需生成，不缓存）

    Returns:
        Markdown 格式的报告内容
    """
    now = datetime.now()
    cutoff_30d = now - timedelta(days=30)

    # 统计各状态文章数
    stats = {
        "fetched": 0,
        "candidate": 0,
        "translated": 0,
        "rated": 0,
    }

    # 收集评分数据
    ratings = []

    # 扫描所有文章
    for md_file in Config.ARTICLE_POOL_DIR.rglob("*.md"):
        yaml_dict, _ = parse_yaml_header(md_file)
        if not yaml_dict:
            continue

        status = yaml_dict.get("status", "fetched")

        # 统计状态
        if status in stats:
            stats[status] += 1

        # 统计评分（近30天）
        if "user_rating" in yaml_dict:
            rated_date_str = yaml_dict.get("rated_date", "")
            try:
                rated_date = datetime.strptime(rated_date_str, "%Y-%m-%d")
                if rated_date >= cutoff_30d:
                    ratings.append({
                        "score": yaml_dict["user_rating"],
                        "source": yaml_dict.get("source", "Unknown"),
                    })
            except (ValueError, TypeError):
                # 无法解析日期，跳过
                pass

    # 计算产出质量指标
    total_rated = len(ratings)
    avg_rating = sum(r["score"] for r in ratings) / total_rated if total_rated > 0 else 0
    satisfied_count = len([r for r in ratings if r["score"] >= 4])
    satisfaction_rate = (satisfied_count / total_rated * 100) if total_rated > 0 else 0

    # 统计已翻译文章（用于计算覆盖率）
    translated_count = stats["translated"] + stats.get("rated", 0)  # rated 也算翻译过的
    coverage_rate = (total_rated / translated_count * 100) if translated_count > 0 else 0

    # 源健康状态统计
    sources = load_sources()
    source_stats = {"Active": len(sources), "Quarantine": 0, "Archive": 0}

    # 读取 quarantine.yaml 计数
    if Config.QUARANTINE_FILE.exists():
        try:
            with Config.QUARANTINE_FILE.open("r", encoding="utf-8") as f:
                q_data = yaml.safe_load(f)
            q_sources = q_data.get("quarantined_sources", []) if isinstance(q_data, dict) else []
            source_stats["Quarantine"] = len([s for s in q_sources if isinstance(s, dict)])
        except Exception:
            pass

    # 读取 archive.yaml 计数
    if Config.ARCHIVE_FILE.exists():
        try:
            with Config.ARCHIVE_FILE.open("r", encoding="utf-8") as f:
                a_data = yaml.safe_load(f)
            a_sources = a_data.get("archived_sources", []) if isinstance(a_data, dict) else []
            source_stats["Archive"] = len([s for s in a_sources if isinstance(s, dict)])
        except Exception:
            pass

    # 生成报告
    report = f"""# 信息摄取现状

> 生成时间：{now.strftime("%Y-%m-%d %H:%M")}

## 管道状态

| 阶段 | 数量 | 说明 |
|------|------|------|
| 待筛选 (fetched) | {stats['fetched']} | 原始采集 |
| 待翻译 (candidate) | {stats['candidate']} | Agent 精选 |
| 已翻译 (translated) | {stats['translated']} | 可供阅读 |
| 已评分 (rated) | {stats['rated']} | 有用户反馈 |

## 产出质量（近30天）

- 已翻译文章：{translated_count} 篇
- 用户评分覆盖：{coverage_rate:.0f}%
- 平均满意度：{avg_rating:.1f}/5
- 满意率（≥4分）：{satisfaction_rate:.0f}%

## 源健康

- Active: {source_stats['Active']} | Quarantine: {source_stats['Quarantine']} | Archive: {source_stats['Archive']}
"""

    # 添加优化建议（如果有足够数据）
    if total_rated >= 5:
        # 分析源级别满意度
        source_ratings = {}
        for r in ratings:
            src = r["source"]
            if src not in source_ratings:
                source_ratings[src] = []
            source_ratings[src].append(r["score"])

        suggestions = []
        for src, scores in source_ratings.items():
            src_avg = sum(scores) / len(scores)
            if src_avg < avg_rating - 0.5 and len(scores) >= 2:
                suggestions.append(f"- 源 \"{src}\" 满意度 {src_avg:.1f} 低于均值 {avg_rating:.1f}，建议观察")

        if suggestions:
            report += "\n## 优化建议\n\n" + "\n".join(suggestions) + "\n"

    return report


def analyze_feedback(days: int = 30) -> str:
    """
    分析反馈数据，输出优化建议

    Args:
        days: 分析最近 N 天的数据

    Returns:
        分析报告（Markdown 格式）
    """
    now = datetime.now()
    cutoff = now - timedelta(days=days)

    # 收集评分数据
    ratings_by_source = {}  # source -> [(score, title, category)]
    ratings_by_category = {}  # category -> [score]
    all_ratings = []

    for md_file in Config.ARTICLE_POOL_DIR.rglob("*.md"):
        yaml_dict, _ = parse_yaml_header(md_file)
        if not yaml_dict:
            continue

        if "user_rating" not in yaml_dict:
            continue

        rated_date_str = yaml_dict.get("rated_date", "")
        try:
            rated_date = datetime.strptime(rated_date_str, "%Y-%m-%d")
            if rated_date < cutoff:
                continue
        except (ValueError, TypeError):
            continue

        score = yaml_dict["user_rating"]
        source = yaml_dict.get("source", "Unknown")
        category = yaml_dict.get("category", "general")
        title = yaml_dict.get("title", "Untitled")

        all_ratings.append(score)

        if source not in ratings_by_source:
            ratings_by_source[source] = []
        ratings_by_source[source].append((score, title, category))

        if category not in ratings_by_category:
            ratings_by_category[category] = []
        ratings_by_category[category].append(score)

    # 生成报告
    report = f"""# 反馈分析报告

> 分析周期：最近 {days} 天（{cutoff.strftime("%Y-%m-%d")} 至今）
> 生成时间：{now.strftime("%Y-%m-%d %H:%M")}

"""

    if not all_ratings:
        report += "## 暂无评分数据\n\n请先使用 `rate` 命令为文章评分。\n"
        return report

    # 总体统计
    total = len(all_ratings)
    avg = sum(all_ratings) / total
    satisfied = len([r for r in all_ratings if r >= 4])
    satisfaction_rate = satisfied / total * 100

    report += f"""## 总体统计

| 指标 | 数值 | 目标 |
|------|------|------|
| 评分文章数 | {total} | - |
| 平均满意度 | {avg:.1f}/5 | ≥3.5 |
| 满意率（≥4分） | {satisfaction_rate:.0f}% | ≥60% |

"""

    # 按源分析
    report += "## 源级别分析\n\n"
    report += "| 源 | 评分数 | 平均分 | 满意率 | 趋势 |\n"
    report += "|-----|-------|-------|-------|------|\n"

    source_stats = []
    for source, items in ratings_by_source.items():
        scores = [item[0] for item in items]
        src_avg = sum(scores) / len(scores)
        src_satisfied = len([s for s in scores if s >= 4])
        src_rate = src_satisfied / len(scores) * 100
        trend = "🟢" if src_avg >= avg else "🔴" if src_avg < avg - 0.5 else "🟡"
        source_stats.append((source, len(scores), src_avg, src_rate, trend))
        report += f"| {source} | {len(scores)} | {src_avg:.1f} | {src_rate:.0f}% | {trend} |\n"

    report += "\n"

    # 按领域分析
    report += "## 领域分析\n\n"
    report += "| 领域 | 评分数 | 平均分 | 满意率 |\n"
    report += "|------|-------|-------|-------|\n"

    for category, scores in sorted(ratings_by_category.items(), key=lambda x: -len(x[1])):
        cat_avg = sum(scores) / len(scores)
        cat_satisfied = len([s for s in scores if s >= 4])
        cat_rate = cat_satisfied / len(scores) * 100
        report += f"| {category} | {len(scores)} | {cat_avg:.1f} | {cat_rate:.0f}% |\n"

    report += "\n"

    # 优化建议
    report += "## 优化建议\n\n"

    suggestions = []

    # 识别低满意度源
    for source, count, src_avg, src_rate, _ in source_stats:
        if count >= 2 and src_avg < avg - 0.5:
            suggestions.append(f"- ⚠️ 源 **{source}** 满意度 {src_avg:.1f} 显著低于均值 {avg:.1f}，建议：")
            suggestions.append(f"  - 检查该源的内容质量和相关性")
            suggestions.append(f"  - 考虑调整过滤规则或降低采集频率")

    # 识别高满意度源
    high_performers = [(s, c, a) for s, c, a, _, _ in source_stats if c >= 2 and a >= 4.0]
    if high_performers:
        suggestions.append(f"- ✅ 高满意度源（可增加权重）：")
        for source, count, src_avg in high_performers:
            suggestions.append(f"  - **{source}**：{src_avg:.1f} 分（{count} 篇）")

    # 满意率警告
    if satisfaction_rate < 60:
        suggestions.append(f"- ⚠️ 整体满意率 {satisfaction_rate:.0f}% 低于目标 60%，建议：")
        suggestions.append(f"  - 加强 Agent 精选环节的质量把控")
        suggestions.append(f"  - 审查低分文章的共同特征")

    if suggestions:
        report += "\n".join(suggestions) + "\n"
    else:
        report += "暂无优化建议，当前表现良好。\n"

    # 低分文章列表
    low_rated = []
    for source, items in ratings_by_source.items():
        for score, title, category in items:
            if score <= 2:
                low_rated.append((score, title, source, category))

    if low_rated:
        report += "\n## 低分文章（≤2分）\n\n"
        report += "| 评分 | 标题 | 来源 | 领域 |\n"
        report += "|-----|------|------|------|\n"
        for score, title, source, category in sorted(low_rated, key=lambda x: x[0]):
            report += f"| {score} | {title[:30]}... | {source} | {category} |\n"

    return report


# ============================================================================
# CLI 入口
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="文章采集器 - 从信息源采集文章并生成 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # collect 命令
    collect_parser = subparsers.add_parser("collect", help="采集文章")
    collect_parser.add_argument(
        "--days", type=int, default=7, help="采集最近N天的文章（默认7天）"
    )
    collect_parser.add_argument(
        "--dry-run", action="store_true", help="干跑模式（不保存文件）"
    )
    collect_parser.add_argument(
        "--force", action="store_true", help="强制重新采集（忽略断点续传）"
    )

    # status 命令
    status_parser = subparsers.add_parser("status", help="显示采集状态")

    # rate 命令
    rate_parser = subparsers.add_parser("rate", help="为文章添加评分")
    rate_parser.add_argument("file", help="文章文件路径")
    rate_parser.add_argument(
        "--score", type=int, required=True, choices=[1, 2, 3, 4, 5],
        help="评分 1-5（1=很差, 5=很好）"
    )
    rate_parser.add_argument(
        "--reason", type=str, help="评分理由（可选）"
    )

    # intake-status 命令
    intake_parser = subparsers.add_parser("intake-status", help="生成摄取现状报告")
    intake_parser.add_argument(
        "--output", "-o", type=str, help="输出到文件（默认输出到 stdout）"
    )

    # analyze-feedback 命令
    analyze_parser = subparsers.add_parser("analyze-feedback", help="分析反馈数据")
    analyze_parser.add_argument(
        "--days", type=int, default=30, help="分析最近N天的数据（默认30天）"
    )
    analyze_parser.add_argument(
        "--output", "-o", type=str, help="输出到文件（默认输出到 stdout）"
    )

    args = parser.parse_args()

    # 如果没有命令，打印帮助
    if not args.command:
        parser.print_help()
        return

    if args.command == "collect":
        collector = ArticleCollector(skip_resume=args.force)
        collector.collect(days=args.days, dry_run=args.dry_run)
    elif args.command == "status":
        collector = ArticleCollector(skip_resume=True)
        collector.status()
    elif args.command == "rate":
        rate_article(args.file, args.score, args.reason)
    elif args.command == "intake-status":
        report = generate_intake_status()
        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"[ok] 报告已写入: {args.output}")
        else:
            print(report)
    elif args.command == "analyze-feedback":
        report = analyze_feedback(days=args.days)
        if args.output:
            Path(args.output).write_text(report, encoding="utf-8")
            print(f"[ok] 报告已写入: {args.output}")
        else:
            print(report)


if __name__ == "__main__":
    main()
