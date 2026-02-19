# MindSpider

AI-powered sentiment monitoring system for Chinese social media platforms.

## Architecture

MindSpider adopts a capability-based, event-driven architecture with five core capabilities:

- **Surface Collection** — Crawl hot lists, media articles, and aggregators from 53+ data sources into MongoDB
- **Deep Collection** — Crawl detailed content (posts, comments) from 7 social platforms
- **Signal Detection** — Hard-coded algorithms detecting anomalies (velocity spikes, cross-platform resonance, etc.)
- **Topic Analysis** — LLM-powered deep analysis, clustering, and trend prediction
- **Client Filtering** — Personalized relevance scoring for different client types (brand/region/industry)

These capabilities are independently triggered (scheduled, event-driven, client-initiated) and interconnected through feedback loops.

## Supported Platforms

- Xiaohongshu (小红书)
- Douyin (抖音)
- Kuaishou (快手)
- Bilibili (哔哩哔哩)
- Weibo (微博)
- Tieba (贴吧)
- Zhihu (知乎)

## Installation

```bash
pip install -e .
playwright install chromium
```

## Configuration

Copy `.env.example` to `.env` and fill in your settings.

## Usage

```bash
python main.py
```

## License

This project is based on [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) and is subject to dual licensing:

- **Non-Commercial Learning License 1.1** - For learning and research purposes only
- See [LICENSE](LICENSE) for full details

**Note:** Commercial use requires written permission from the original author.
