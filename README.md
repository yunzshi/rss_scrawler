# rss_scrawler — 反 FOMO 事实爬虫

一个轻量的 RSS 抓取与事实提纯工具：从多个 RSS 源抓取文章，调用 OpenClaw 的 `facts_crawler` Agent 对条目进行“去情绪化”的事实提纯，然后打印或推送（飞书）。适合定时运行以获得每日/定期的“反 FOMO（反恐慌/去情绪化）”事实简报。

## 主要特性

- 基于 RSS 抓取（`feedparser` + `requests`）。
- 使用本地或远端的 OpenClaw agent 进行事实提纯（通过命令行调用 `openclaw agent`）。
- 支持将结果打印到控制台或推送到飞书（可通过环境变量控制）。
- 带本地历史记录去重，防止重复推送。

## 前置依赖

- Python 3.8+
- Python 包：`feedparser`、`requests`
- 外部工具：`openclaw` CLI（脚本通过子进程调用该命令来执行 Agent）

安装示例：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install feedparser requests
```

（如果你有统一的 `requirements.txt`，也可以使用 `pip install -r requirements.txt`。）

## 配置说明

- 脚本会将 `openclaw.json` 视为位于脚本上两级目录的配置文件（即 `../openclaw.json`），若不存在则会使用环境变量覆盖。
- 主要环境变量：

  - `OPENCLAW_TOKEN`：OpenClaw 的授权 Token（可在 `openclaw.json` 中配置）。
  - `OPENCLAW_PORT`：默认 `18789`，用于构建默认 `OPENCLAW_API_URL`。
  - `OPENCLAW_API_URL`：可直接覆盖 API 地址。
  - `DELIVER_TO_FEISHU`：是否推送到飞书，默认 `true`（小写字符串判定）。
  - `FEISHU_CHANNEL` / `FEISHU_TARGET`：推送目标通道与对象，默认在脚本中有示例值。
  - `SOCKS5_PROXY`：如果需要走 SOCKS5 代理，可在脚本中或环境变量中设置（脚本会尝试使用该值进行请求）。

- 历史记录文件（用于去重）默认为：`/home/yunzhi/.openclaw/.rss_history.json`。如需更改，请编辑脚本顶部的 `HISTORY_FILE` 常量或创建对应目录并保证可写权限。

## 配置示例

下面的示例直接来源于仓库的 `config.yaml`（可版本化）和 `.env.example`（本地开发），你可以把它们放在 `rss_scrawler/` 目录下。

`config.yaml` 示例：

```yaml
gateway:
  port: 18789
  api_url: "http://127.0.0.1:18789/v1/chat/completions"
  # token: "your_openclaw_token"

agent:
  id: "facts_crawler"

rss:
  feeds:
    - name: "晚点LatePost"
      url: "http://192.168.1.53:1200/latepost"
    - name: "联合早报"
      url: "http://192.168.1.53:1200/zaobao/realtime/china"
    - name: "量子位"
      url: "http://192.168.1.53:1200/qbitai/category/资讯"
    - name: "Hacker News Top"
      url: "https://hnrss.org/frontpage"
    - name: "阮一峰科技周刊"
      url: "http://192.168.1.53:1200/github/issue/ruanyf/weekly"
    - name: "少数派-深度文"
      url: "http://192.168.1.53:1200/sspai/matrix"
    - name: "Paul Graham Essays"
      url: "http://192.168.1.53:1200/paulgraham/articles"
    - name: "Farnam Street"
      url: "https://fs.blog/feed/"
  max_items_per_feed: 10

deliver:
  to_feishu: true
  feishu_channel: "feishu"
  feishu_target: "user:ou_xxx"

history:
  file: "$HOME/.openclaw/.rss_history.json"
  max_size: 1500

network:
  socks5_proxy: null
  timeout: 20
```

`.env` 示例（本地）：

```
OPENCLAW_TOKEN=your_openclaw_token_here
SOCKS5_PROXY=
DELIVER_TO_FEISHU=false
FEISHU_CHANNEL=feishu
FEISHU_TARGET=user:ou_xxx
REQUEST_TIMEOUT=20
```

安全提示：不要将 `.env` 或包含 token 的文件提交到公开仓库；把它们加入 `.gitignore`，在 CI 中使用 Secret 注入环境变量。

## 使用方法

进入 `rss_scrawler` 目录后运行：

```bash
cd rss_scrawler
# 使用环境变量覆盖配置并运行
OPENCLAW_TOKEN=xxx DELIVER_TO_FEISHU=false python3 rss_scrawler.py
```

示例：每 30 分钟运行一次（crontab）：

```cron
*/30 * * * * cd /path/to/repo/rss_scrawler && OPENCLAW_TOKEN=xxx /usr/bin/python3 rss_scrawler.py >> /var/log/rss_scrawler.log 2>&1
```

脚本将：

1. 抓取 `RSS_FEEDS` 列表中配置的源（默认包含若干示例源）。
2. 去重并拼接为原始条目列表。若无新条目，则退出。 
3. 使用 `openclaw agent --agent facts_crawler` 将原始条目传给 Agent 进行“事实提纯”。
4. 将 Agent 输出打印并（可选）推送至飞书。

## 自定义与扩展

- 修改订阅源：直接编辑脚本中的 `RSS_FEEDS` 列表，添加/删除你需要的源。
- 修改历史路径或保留策略：编辑 `HISTORY_FILE` 或 `HISTORY_MAX_SIZE`。
- 如果需要更复杂的 HTML 解析或更健壮的去重策略，可替换当前的 `strip_html` 或 `deduplicate` 实现（脚本中有注释）。

## 常见问题与排错

- 如果 `openclaw agent` 调用失败：确认 `openclaw` 可执行程序已安装且在 `PATH` 中，或使用 `openclaw.json` 正确配置 gateway 与 token。
- 如果抓取不到条目或解析报错：检查 RSS 源是否可访问（网络/代理问题），或尝试增加 `requests` 的 `timeout` 值。
- 权限错误：确保 `HISTORY_FILE` 指向的目录可写。

## 贡献
## 贡献

欢迎 issue、PR 与建议。常见贡献流程：fork → 新建分支 → 提交 PR。请在 PR 描述中说明变更目的与测试方式。

## 许可证

请在发布前在仓库根添加 `LICENSE` 文件，明确开源许可。若需要我帮你添加常用许可模板（MIT / Apache-2.0），我可以代为创建。

---

如需我把该 README 添加到仓库（已完成）并帮你初始化 Git 仓库与推送到 GitHub，请告诉我你的 GitHub 仓库名或是否要我用 `gh` CLI 为你创建。 
