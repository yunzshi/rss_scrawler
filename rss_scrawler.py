#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FACTS_CRAWLER: 纯净事实提取器 (Anti-FOMO)
读取 RSS 源 → 调用 OpenClaw facts_crawler Agent 去情绪化提纯 → 推送结果

前置依赖:
    pip install feedparser requests

用法:
    # 直接运行（自动从 openclaw.json 读取配置）
    python facts_crawler.py

    # 通过环境变量覆盖配置
    OPENCLAW_TOKEN=xxx DELIVERY_WEBHOOK=https://... python facts_crawler.py
"""

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
import re
import sys
import socket
import logging
from datetime import datetime
from html.parser import HTMLParser
from io import StringIO
from urllib.parse import urlparse

# ==========================================
# 0. 日志初始化
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ==========================================
# 1. 路径与配置加载
# ==========================================

# 脚本所在目录的上两级就是 .openclaw 根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OPENCLAW_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OPENCLAW_JSON = os.path.join(OPENCLAW_ROOT, "openclaw.json")
# 默认历史记录路径使用 $HOME 环境变量（可被环境变量或 config.yaml 覆盖）
HISTORY_FILE = os.path.join(os.environ.get("HOME", os.path.expanduser("~")), ".openclaw", ".rss_history.json")
HISTORY_MAX_SIZE = 1500


def load_history():
    """从历史文件中读取已处理过的文章 UID"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        if os.path.getsize(HISTORY_FILE) > 2:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                if len(data) == 0:
                    raise ValueError("文件大小非空但解析出空列表，阻止全量重抓")
                return data
            raise ValueError(f"历史文件格式异常，不是列表: {type(data)}")
        else:
            return []
    except Exception as e:
        logger.error(f"🚨 读取历史记录失败，终止执行防止全量倒灌: {e}")
        sys.exit(1)



def save_history(history_list):
    """保存处理记录，最多保留限定条数以防止文件无限增大 (原子写入)"""
    import tempfile
    try:
        dir_name = os.path.dirname(HISTORY_FILE)
        # 确保历史目录存在，避免 mkstemp 因目录不存在而失败
        os.makedirs(dir_name, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".rss_history_tmp_", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history_list[-HISTORY_MAX_SIZE:], f, ensure_ascii=False)
        os.replace(temp_path, HISTORY_FILE)
    except Exception as e:
        logger.error(f"⚠️ 保存历史记录失败: {e}")



def load_openclaw_config():
    """从 openclaw.json 读取 gateway 配置"""
    if not os.path.exists(OPENCLAW_JSON):
        logger.warning(f"⚠️ 未找到 {OPENCLAW_JSON}，将使用默认值或环境变量")
        return {}
    with open(OPENCLAW_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


_config = load_openclaw_config()
_gateway = _config.get("gateway", {})
_auth = _gateway.get("auth", {})

# -------------------------------
# 配置加载（优先级：环境变量 > .env > config.yaml > openclaw.json > 默认）
# -------------------------------
CONFIG_YAML = os.path.join(SCRIPT_DIR, "config.yaml")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

# 尝试加载 .env（如果 python-dotenv 可用）
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except Exception:
    pass

# 尝试加载 config.yaml（如果 PyYAML 可用且文件存在）
_yaml_config = {}
try:
    import yaml
    if os.path.exists(CONFIG_YAML):
        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            _yaml_config = yaml.safe_load(f) or {}
except Exception as e:
    logger.warning(f"⚠️ 读取 config.yaml 失败或 PyYAML 未安装: {e}")


def _pick_env_yaml(env_key, yaml_path, default=None):
    """优先返回环境变量，其次返回 yaml 中嵌套路径 yaml_path 的值，否则返回 default"""
    val = os.environ.get(env_key)
    if val is not None:
        return val
    cfg = _yaml_config
    for k in yaml_path:
        if isinstance(cfg, dict) and k in cfg:
            cfg = cfg[k]
        else:
            cfg = None
            break
    if cfg is not None:
        return cfg
    return default


def _as_int(v, default):
    try:
        return int(v)
    except Exception:
        return default


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "y")


# Gateway / Token
GATEWAY_PORT = _as_int(_pick_env_yaml("OPENCLAW_PORT", ("gateway", "port"), _gateway.get("port", 18789)), 18789)
OPENCLAW_API_URL = os.environ.get("OPENCLAW_API_URL") or _yaml_config.get("gateway", {}).get("api_url") or f"http://127.0.0.1:{GATEWAY_PORT}/v1/chat/completions"
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_TOKEN") or _yaml_config.get("gateway", {}).get("token") or _auth.get("token", "")

# Agent 与 RSS 列表（保留代码内默认）
AGENT_ID = os.environ.get("AGENT_ID") or _yaml_config.get("agent", {}).get("id") or "facts_crawler"

DEFAULT_RSS_FEEDS = [
    {"name": "Hacker News Top", "url": "https://hnrss.org/frontpage"},
    {"name": "Farnam Street", "url": "https://fs.blog/feed/"},
    {"name": "阮一峰科技周刊", "url": "https://feeds.feedburner.com/ruanyifeng"},
    {"name": "晚点LatePost", "url": "https://rsshub.app/latepost"},
]

RSS_FEEDS = _yaml_config.get("rss", {}).get("feeds") or DEFAULT_RSS_FEEDS

MAX_ITEMS_PER_FEED = _as_int(_pick_env_yaml("MAX_ITEMS_PER_FEED", ("rss", "max_items_per_feed"), 10), 10)

SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY") or _yaml_config.get("network", {}).get("socks5_proxy") or None

deliver_env = os.environ.get("DELIVER_TO_FEISHU")
deliver_yaml = _yaml_config.get("deliver", {}).get("to_feishu") if isinstance(_yaml_config.get("deliver", {}), dict) else None
if deliver_env is not None:
    DELIVER_TO_FEISHU = _as_bool(deliver_env, True)
elif deliver_yaml is not None:
    DELIVER_TO_FEISHU = _as_bool(deliver_yaml, True)
else:
    DELIVER_TO_FEISHU = os.environ.get("DELIVER_TO_FEISHU", "true").lower() == "true"

FEISHU_CHANNEL = os.environ.get("FEISHU_CHANNEL") or _yaml_config.get("deliver", {}).get("feishu_channel") or "feishu"
# 默认 FEISHU_TARGET 使用占位符，避免在仓库中泄露真实 ID
FEISHU_TARGET = os.environ.get("FEISHU_TARGET") or _yaml_config.get("deliver", {}).get("feishu_target") or "user:ou_xxx"

# 历史记录与网络超时
# 从环境变量或 YAML 获取历史文件路径，然后展开环境变量与 ~
_raw_history_file = os.environ.get("HISTORY_FILE") or _yaml_config.get("history", {}).get("file") or HISTORY_FILE
HISTORY_FILE = os.path.expanduser(os.path.expandvars(_raw_history_file))
HISTORY_MAX_SIZE = _as_int(_pick_env_yaml("HISTORY_MAX_SIZE", ("history", "max_size"), HISTORY_MAX_SIZE), HISTORY_MAX_SIZE)

REQUEST_TIMEOUT = _as_int(_pick_env_yaml("REQUEST_TIMEOUT", ("network", "timeout"), 20), 20)


# ==========================================
# 3. 代理初始化
# ==========================================


# 保存原始 socket（备用）
_original_socket = socket.socket


def setup_socks_proxy(proxy_url):
    """验证 SOCKS5 代理配置"""
    parsed = urlparse(proxy_url)
    logger.info(f"代理已配置: {parsed.hostname}:{parsed.port} (SOCKS5)")


def local_request(method, url, **kwargs):
    """发送本地请求（不走代理）"""
    return requests.request(method, url, **kwargs)


if SOCKS5_PROXY:
    setup_socks_proxy(SOCKS5_PROXY)

# ==========================================
# 4. 工具函数
# ==========================================


class _HTMLStripper(HTMLParser):
    """简单的 HTML 标签清理器，不依赖 BeautifulSoup"""

    def __init__(self):
        super().__init__()
        self._text = StringIO()

    def handle_data(self, data):
        self._text.write(data)

    def get_text(self):
        return self._text.getvalue()


def strip_html(html_str):
    """移除 HTML 标签，返回纯文本"""
    if not html_str:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_str)
        return stripper.get_text().strip()
    except Exception:
        # 兜底：正则粗暴清理
        return re.sub(r"<[^>]+>", "", html_str).strip()


def deduplicate(news_list):
    """基于标题的简单去重"""
    seen_titles = set()
    unique = []
    for item in news_list:
        # 提取标题部分用于去重
        title_match = re.search(r"标题: (.+?) \|", item)
        title = title_match.group(1).strip() if title_match else item
        if title not in seen_titles:
            seen_titles.add(title)
            unique.append(item)
    return unique


# ==========================================
# 5. 核心逻辑
# ==========================================


def create_requests_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retries))
    session.mount('https://', HTTPAdapter(max_retries=retries))
    if SOCKS5_PROXY:
        session.proxies = {"http": SOCKS5_PROXY, "https": SOCKS5_PROXY}
    return session


def fetch_rss_feeds():
    """抓取并解析 RSS 源"""
    logger.info("开始抓取 RSS 源...")
    raw_news = []
    new_uids = []
    
    history_list = load_history()
    history_set = set(history_list)

    session = create_requests_session()

    for feed_info in RSS_FEEDS:
        name = feed_info["name"]
        logger.info(f"解析: {name}...")
        try:
            # 先用 requests 下载 RSS 内容，再交给 feedparser 解析
            resp = session.get(feed_info["url"], timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            feed = feedparser.parse(resp.content)
            if feed.bozo and not feed.entries:
                logger.warning(f"⚠️ {name} 解析异常: {feed.bozo_exception}")
                continue

            count = 0
            for entry in feed.entries:
                if count >= MAX_ITEMS_PER_FEED:
                    break

                title = entry.get("title", "").strip()
                if not title:
                    continue

                link = entry.get("link", "")
                
                # 唯一标识符用来记忆已经发过的
                uid = link if link else title
                if uid in history_set:
                    continue  # 这篇发过了，跳过
                
                # 标记为新见到的文章
                history_set.add(uid)
                new_uids.append(uid)

                # 清理 HTML 并截断摘要
                description = strip_html(entry.get("description", ""))
                if len(description) > 80:
                    description = description[:80] + "..."

                raw_news.append(f"【{name}】标题: {title} | 摘要: {description} | 链接: {link}")
                count += 1

            logger.info(f"✓ {name}: 获取 {count} 条")
        except Exception as e:
            logger.error(f"✗ {name} 抓取失败: {e}")

    # 去重
    before = len(raw_news)
    raw_news = deduplicate(raw_news)
    if before > len(raw_news):
        logger.info(f"本批次内部去重: {before} → {len(raw_news)} 条")

    return raw_news, new_uids


def purify_with_openclaw(raw_news_list):
    """调用 OpenClaw API 进行事实提纯"""
    if not raw_news_list:
        return "今日无新闻抓取。"

    logger.info(f"调用 Agent 提纯 ({len(raw_news_list)} 条)...")

    news_text = "\n".join(raw_news_list)

    prompt = (
        "你是一个没有感情的硅基事实提取器。严格执行以下协议：\n"
        "输出格式: [来源] [主体] [动作/事实](链接)\n"
        "其中链接使用 Markdown 格式内嵌到事实描述中，例如：\n"
        "  [财联社] [比亚迪] [3月交付量破30万辆](https://example.com/news/123)\n"
        "1. 纯事实 → 正常输出\n"
        "2. 事实+情绪混合 → 剥离情绪，只保留可验证事实\n"
        "3. 纯观点/预测/情绪 → 保留但标注 [观点]\n"
        "4. 信息源模糊 → 标注 [未经证实]\n"
        "5. 优惠/免费额度信息(free tier、credits、折扣) → 标注 [💰优惠] 并优先保留\n"
        "禁止出现：利好/利空、暴涨/暴跌、重磅、突发\n"
        "重要：必须逐条处理所有输入，每条都必须输出一行结果，不得跳过或合并。\n"
        "不同来源报道同一事件时，每个来源单独列一行。\n\n"
        "以下是今天抓取的原始新闻，请逐条处理：\n\n"
        f"{news_text}"
    )

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENCLAW_TOKEN}"
        }
        payload = {
            "model": AGENT_ID,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        
        # 使用重试的 session 发起 HTTP 请求
        session = create_requests_session()
        resp = session.post(OPENCLAW_API_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "未解析到有效回复")
        return "未解析到有效回复"

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"HTTP 调用 OpenClaw 失败: {e}")
    except ValueError as e:
        raise RuntimeError(f"JSON 解析失败: {e}")
    except Exception as e:
        raise RuntimeError(f"调用提纯失败: {e}")


def push_result(content):
    """推送提纯结果：飞书推送或打印到控制台"""
    # 始终打印到控制台
    print(f"\n{'=' * 40}")
    print("  今日反 FOMO 事实简报")
    print(f"{'=' * 40}\n")
    print(content)
    print(f"\n{'=' * 40}")

    if not DELIVER_TO_FEISHU:
        return

    # 通过 openclaw agent --deliver 推送到飞书
    logger.info("推送至飞书...")

    # 格式化简报内容：确保每条事实独立成行，加上序号
    lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
    # 过滤掉 agent 可能附带的反馈提示
    lines = [l for l in lines if "回复数字" not in l and "对你有帮助" not in l]
    formatted = "\n\n".join(lines)

    deliver_text = (
        f"🛡️ 今日反 FOMO 事实简报\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"{formatted}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 {datetime.now():%Y-%m-%d %H:%M}"
    )
    try:
        import subprocess
        result = subprocess.run(
            [
                "openclaw", "agent",
                "--agent", AGENT_ID,
                "--message", f"请将以下消息原样发送，不要添加任何内容，不要改变格式和换行：\n\n{deliver_text}",
                "--deliver",
                "--channel", FEISHU_CHANNEL,
                "--json",
                "--to", FEISHU_TARGET,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("✓ 飞书推送成功")
        else:
            logger.error(f"✗ 飞书推送失败: {result.stderr.strip()}")
    except Exception as e:
        logger.error(f"✗ 飞书推送失败: {e}")


# ==========================================
# 6. 主程序
# ==========================================

if __name__ == "__main__":
    logger.info("=== Anti-FOMO Facts Crawler 开始运行 ===")

    # 1. 抓取
    raw_news, new_uids = fetch_rss_feeds()
    if not raw_news:
        logger.info("当前没有更新的文章，退出。")
        sys.exit(0)

    try:
        # 2. 提纯
        pure_facts = purify_with_openclaw(raw_news)

        # 3. 推送
        push_result(pure_facts)
    except Exception as e:
        # 如果任何步骤失败，打印错误但继续保存历史
        logger.error(f"处理过程出错: {e}")
        # 仍然会进入finally块保存历史
    finally:
        # 4. 无论上面任何步骤是否成功，都要保存历史记录
        # 这样可以确保已抓取的文章不会因为推送或提纯失败而重复
        history = load_history()
        history.extend(new_uids)
        save_history(history)

    logger.info("=== 完成 ===")