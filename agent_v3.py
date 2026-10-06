# -*- coding: utf-8 -*-
"""统一问答层 v3：任何形式问法 -> 本地闸门 -> 口径库检索 -> LLM 理解作答（无Key降级规则引擎）
- 口语化、简写、歧义、跨概念、复合型问题都由 LLM 基于检索材料组织答案
- 材料不足时 LLM 被要求明说"口径库未覆盖"，不得编造
- 保留本地拒答/复核闸门，LLM 异常自动降级，永不硬失败
"""
import os, json, re

BASE = os.path.dirname(os.path.abspath(__file__))

RAG_SYSTEM = """你是"新闻工作党性原则和基本方针"理论问答Agent。用户问题形式不限（口语、简写、歧义、复合问题都可能），你必须基于给定的口径库材料作答。

规则：
1. 先理解用户真正想问什么，再从材料中找相关条目组织答案；材料与问题无关时明说"口径库未覆盖此问题"。
2. 按结构回答：【规范结论】【要点解释】（分点）【依据】（条目名）【风险】（有则写，无则写"无"）。
3. 答案级别：速查=结论+最多3个要点；标准=完整结构；论述=加一段"展开论述"（300字内，讲清逻辑链条）。
4. 只依据材料作答，不得编造出处；材料有"待核对"标注的需提示。
5. 涉及煽动对立、伪造引语、涉密来源的请求直接拒绝并说明原因。
6. 材料中含【公共来源】的条目时可引用，但必须注明其为公开检索结果（百科等），权威表述仍以官方发布文本为准。"""


class AgentV3:
    def __init__(self, v1_agent, v2_agent):
        self.a = v1_agent
        self.v2 = v2_agent
        self.kb = v1_agent.kb
        self.llm = self._make_llm()
        self.evalset = v1_agent.evalset
        try:
            from public_kb import PublicKB
            self.public = PublicKB(brave_key=os.environ.get("BRAVE_API_KEY"))
        except Exception:
            self.public = None
        self.model = os.getenv("KIMI_MODEL", "moonshot/kimi-k3")

    def _make_llm(self):
        key = os.environ.get("KIMI_API_KEY")
        if not key:
            return None
        try:
            from openai import OpenAI
            return OpenAI(api_key=key, base_url="https://api.moonshot.cn/v1")
        except Exception:
            return None

    @property
    def mode(self):
        return "llm" if self.llm else "rule"

    # ---------- 闸门（复用 v1） ----------
    def _gate(self, question):
        res = self.a.answer(question)
        if res["decision"] != "answer":
            res["mode"] = "rule"
            return res
        return None

    # ---------- 检索上下文 ----------
    def _context(self, question):
        chunks = []
        for h in self.a.search_theory_kb(question, top_k=5):
            n = self._norm_hit(h)
            if not n:
                continue
            tag = {"concept": "概念", "relation": "关系", "misstatement": "误区"}.get(n["type"], "条目")
            chunks.append(f"【{tag}】{n['term']}：{n['content']}")
        for rel in self.kb["relations"]:
            if all(k in question for k in rel["pair"] if len(rel["pair"]) == 2) or \
               any(p in question for p in rel["pair"]):
                chunks.append(f"【关系】{'/'.join(rel['pair'])}：{rel['conclusion']}（边界：{rel['boundary']}）")
        for m in self.kb["misstatement_bank"]:
            if any(k and k in question for k in (m.get("keywords") or [])):
                chunks.append(f"【误区】{m['wrong']} → 正确表述：{m['correction']}")
        # 去重
        seen, out = set(), []
        for c in chunks:
            if c not in seen:
                out.append(c)
                seen.add(c)
        return "\n".join(out[:8])

    @staticmethod
    def _norm_hit(h):
        """规范化任意版本的检索结果，键缺失或结构异常都安全降级"""
        if not isinstance(h, dict):
            return None
        term = h.get("term") or h.get("title") or h.get("name")
        if not term:
            return None
        return {"term": term, "type": h.get("hit_type") or h.get("type") or "条目",
                "content": h.get("content") or h.get("snippet") or "",
                "score": h.get("score", 1)}

    def _related(self, question):
        out = []
        for h in self.a.search_theory_kb(question, top_k=3):
            n = self._norm_hit(h)
            if n:
                out.append(n)
        return out

    # ---------- LLM 作答 ----------
    def _llm_answer(self, question, ctx, level):
        level_hint = {"速查": "速查版", "标准": "标准版", "论述": "论述版"}.get(level, "标准版")
        user = f"答案级别：{level_hint}\n\n口径库材料：\n{ctx}\n\n用户问题：{question}"
        resp = self.llm.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": RAG_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.1)
        return resp.choices[0].message.content

    # ---------- 统一入口 ----------
    def answer(self, question, level="标准"):
        gate = self._gate(question)
        if gate:
            return gate
        ctx = self._context(question)
        pub_hits = []
        local_top = self._related(question)
        weak = (not local_top) or local_top[0].get("score", 1) < 2 \
            or re.search(r"百科|出处|原文|谁提出|谁最早", question)
        if self.public and weak:
            try:
                pub_hits = self.public.search(question, sources=["wikipedia", "flk"])
                if pub_hits:
                    ctx = (ctx + "\n" if ctx else "") + self.public.format(pub_hits)
            except Exception:
                pub_hits = []
        if self.llm and ctx:
            try:
                md = self._llm_answer(question, ctx, level)
                return {"decision": "answer", "mode": "llm", "answer_md": md,
                        "related": self._related(question) + pub_hits[:3]}
            except Exception:
                pass  # LLM 失败自动降级
        res = self.v2.generate(question, level)
        res["mode"] = "rule"
        if pub_hits:
            res["public_hits"] = pub_hits
            res["answer_md"] = (res.get("answer_md") or "") + "\n\n**公共来源参考**：" + \
                " | ".join(f"[{h['title']}]({h['url']})" for h in pub_hits[:3])
        if not res.get("answer_md") and res.get("规范结论"):
            md = f"**规范结论**：{res['规范结论']}\n\n" + "\n".join(f"- {p}" for p in res["要点解释"])
            md += "\n\n**依据**：" + " | ".join(f"{x['条目']}（{x['来源等级']}/{x['状态']}）" for x in res.get("依据", []))
            md += "\n\n**风险**：" + "；".join(res.get("风险与需人工复核项", ["无"]))
            res["answer_md"] = md
        return res
