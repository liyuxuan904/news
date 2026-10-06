# -*- coding: utf-8 -*-
"""公共知识库接入层：本地口径库未覆盖/低置信时的权威来源兜底
内置源：
  - wikipedia   中文维基百科 API（免费、无需 Key、稳定）
  - template    通用模板源（自定义搜索接口，如政府网站内搜索）
  - bing        微软 Bing Web Search（需 BING_SEARCH_KEY，可选）
用法：
  from public_kb import PublicKB
  kb = PublicKB()
  hits = kb.search("陆定一 新闻定义", sources=["wikipedia"])
注意：抓取公共内容须遵守目标站 robots 协议与使用条款；权威表述仍以官方发布文本为准。
"""
import json, re, urllib.parse, urllib.request

DEFAULT_HEADERS = {"User-Agent": "TheoryAgent/1.0 (educational demo)"}
TIMEOUT = 8


class WikipediaSource:
    name = "wikipedia"
    api = "https://zh.wikipedia.org/w/api.php"

    def search(self, query, top_k=3):
        params = urllib.parse.urlencode({
            "action": "opensearch", "format": "json", "search": query,
            "limit": top_k, "namespace": 0, "redirects": "resolve"})
        req = urllib.request.Request(f"{self.api}?{params}", headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        titles, urls, descs = data[1], data[3], (data[2] if len(data) > 2 else [""] * len(data[1]))
        return [{"source": self.name, "title": t, "url": u, "snippet": d}
                for t, u, d in zip(titles, urls, descs)][:top_k]


class TemplateSource:
    """通用模板源：把站内搜索接口按 {q} 占位符配置，返回 JSON 列表路径可配。"""
    name = "template"

    def __init__(self, search_url, title_path="title", url_path="url", snippet_path="snippet"):
        self.search_url = search_url   # 例: "https://example.gov.cn/search?q={q}&format=json"
        self.paths = (title_path, url_path, snippet_path)

    def _dig(self, obj, path):
        for p in path.split("."):
            if isinstance(obj, list):
                obj = obj[0] if obj else {}
            obj = obj.get(p, {}) if isinstance(obj, dict) else {}
        return obj if isinstance(obj, str) else ""

    def search(self, query, top_k=3):
        url = self.search_url.format(q=urllib.parse.quote(query))
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = data if isinstance(data, list) else data.get("results", data.get("data", []))
        out = []
        for it in items[:top_k]:
            out.append({"source": self.name,
                        "title": self._dig(it, self.paths[0]) or it.get("title", ""),
                        "url": self._dig(it, self.paths[1]) or it.get("url", ""),
                        "snippet": self._dig(it, self.paths[2]) or it.get("snippet", "")})
        return out


class BingSource:
    name = "bing"

    def __init__(self, api_key):
        self.api_key = api_key

    def search(self, query, top_k=3):
        url = ("https://api.bing.microsoft.com/v7.0/search?"
               + urllib.parse.urlencode({"q": query, "count": top_k, "mkt": "zh-CN"}))
        req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": self.api_key})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [{"source": self.name, "title": p.get("name", ""),
                 "url": p.get("url", ""), "snippet": p.get("snippet", "")}
                for p in data.get("webPages", {}).get("value", [])][:top_k]


class PublicKB:
    def __init__(self, bing_key=None, templates=None):
        self.sources = {"wikipedia": WikipediaSource()}
        if bing_key:
            self.sources["bing"] = BingSource(bing_key)
        for i, tpl in enumerate(templates or []):
            self.sources[f"template{i}"] = (TemplateSource(*tpl) if isinstance(tpl, tuple) else TemplateSource(**tpl))

    def search(self, query, sources=None, top_k=5):
        names = sources or ["wikipedia"]
        out, seen = [], set()
        for name in names:
            src = self.sources.get(name)
            if not src:
                continue
            try:
                for h in src.search(query, top_k=top_k):
                    if h["title"] and h["title"] not in seen:
                        out.append(h)
                        seen.add(h["title"])
            except Exception:
                continue  # 单源失败不影响整体
        return out[:top_k]

    def format(self, hits):
        return "\n".join(f"【公共来源】{h['title']}：{h['snippet']}（{h['url']}）" for h in hits)
