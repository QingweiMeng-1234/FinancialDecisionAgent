# Theme Chokepoint Provider 配置

## 已确认边界

- Web Search provider：Tavily。
- Tavily 只发现候选 URL；`content`、answer 和 raw-content 字段不进入 Evidence Card。
- 系统必须独立重新抓取候选 URL。HTML 使用 `trafilatura`，PDF 使用 `pypdf`。
- 只有能够在重新抓取的原文中按字符 offset 复核的连续 quote 才能进入 Claim Ledger。
- 搜索失败、抓取失败、文本过短和 extractor 无法找到逐字 quote 均保持 evidence gap，不形成 0 分。
- `http://localhost`、环回、私网、链路本地、保留地址和非 HTTP(S) URL 不允许抓取。

## 环境变量

复制 `config/theme_chokepoint.env.example` 中需要的变量到 Git 工作区根目录的
`.env`。这里的文件名必须就是 `.env`，与 `requirements.txt` 同级；不要在父目录创建
`Financial Agent.env` 等相似文件：

```text
TAVILY_API_KEY=
DEEPSEEK_API_KEY=
THEME_CHOKEPOINT_LLM_PROVIDER=deepseek
THEME_CHOKEPOINT_LLM_API_KEY=
THEME_CHOKEPOINT_LLM_BASE_URL=https://api.deepseek.com
THEME_CHOKEPOINT_LLM_MODEL=deepseek-chat
```

不要将真实 key 写入本文件、测试 fixture、运行工件或 Git。

Theme evidence extractor 已固定为 DeepSeek：

1. `THEME_CHOKEPOINT_LLM_PROVIDER=deepseek` 明确选择 provider，不先尝试 OpenAI；
2. 若 `THEME_CHOKEPOINT_LLM_API_KEY` 非空则使用专用 key；
3. 若专用 key 为空则读取现有 `DEEPSEEK_API_KEY`；
4. 模型与 base URL 分别固定为 `deepseek-chat` 和 `https://api.deepseek.com`。

无模型名或 DeepSeek API key 时必须在 LLM 请求前失败，不静默切换到 OpenAI 或其他模型。

Provider 类只读取进程环境变量，不自行猜测 `.env` 路径。组合真实 provider 前，运行入口
必须显式加载同一 Git 工作区根目录的 `.env`。

## Stage 3 组合

```python
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")  # 从 Git 工作区根目录启动时
search = TavilySearchProvider()
fetcher = OriginalTextFetcher()
extractor = OpenAICompatibleEvidenceSpanExtractor()
acquirer = TavilyOriginalEvidenceAcquirer(search, fetcher, extractor)
```

`acquirer`实现 Stage 3 的`EvidenceAcquirer`协议，可直接传给`EvidenceChokepointLoop`。评分器仍必须使用冻结的`theme-chokepoint-scoring-v1.4`合同。
