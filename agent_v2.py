# -*- coding: utf-8 -*-
"""理论问答Agent v2 · 题型路由 + 分层取料 + 分级答案 + 测验模式
在 v1 引擎（检索/关系/拒答闸门）之上增加：
  1. 题型路由器：名词解释/简答/辨析/关系/材料分析/综合论述
  2. 原因—要求—方法分层取料，回答"为什么/是什么/怎么办"自动组织层次
  3. 三级答案详略：速查版/标准版/论述版
  4. quiz：从评测集抽题，用户作答后按要点自评 + 显示参考答案
"""
import json, os, re, random

BASE = os.path.dirname(os.path.abspath(__file__))


class AgentV2:
    QTYPES = [
        ("名词解释", ["什么是", "什么是新闻", "名词解释", "指的是什么", "是指什么"]),
        ("辨析",     ["辨析", "是否矛盾", "对不对", "如何看待", "怎么看", "是不是"]),
        ("关系",     ["关系", "统一吗", "有什么区别", "如何理解.*关系"]),
        ("材料分析", ["指出问题", "请分析", "评价", "从.*角度分析", "^某"]),
        ("综合论述", ["如何坚持", "如何落实", "如何加强", "论述", "谈谈", "试述", "为什么", "怎么办", "如何做"]),
        ("简答",     ["是什么", "有哪些", "包括", "要求", "原则"]),
    ]
    LAYERS = ["原因层", "要求层", "方法层", "扩展层"]

    def __init__(self, v1_agent):
        self.a = v1_agent                      # v1 引擎：检索/关系/拒答/校准
        self.kb = v1_agent.kb
        self.evalset = v1_agent.evalset

    # ---------- 1. 题型路由 ----------
    def classify(self, q):
        for name, pats in self.QTYPES:
            if any(re.search(p, q) for p in pats):
                return name
        return "简答"

    # ---------- 2. 分层取料 ----------
    def route_layers(self, q):
        """判断问题指向哪些层：为什么→原因层；是什么/要求→要求层；如何/怎么办→方法层"""
        wants = set()
        if re.search(r"为什么|原因|依据|为何|何以", q):
            wants.add("原因层")
        if re.search(r"如何|怎么|怎样|怎么办|路径|方法|举措|落实|加强|坚持.*原则", q):
            wants.add("方法层")
        if re.search(r"是什么|什么是|包括|内容|要求|内涵|是什么关系", q):
            wants.add("要求层")
        return wants or {"要求层"}

    def layer_terms(self, layer):
        return [c["term"] for c in self.kb["concepts"] if c.get("layer") == layer]

    # ---------- 3. 分级答案 ----------
    def generate(self, question, level="标准"):
        """level: 速查 | 标准 | 论述"""
        res = self.a.answer(question)          # 安全闸门 + 检索在 v1 完成
        if res["decision"] != "answer":
            return res
        a = res["answer"]
        qtype = self.classify(question)
        layers = self.route_layers(question)
        if qtype == "综合论述":              # 综合论述默认覆盖全三层
            layers |= {"原因层", "要求层", "方法层"}
        pts = list(a["要点解释"])

        out = {
            "decision": "answer",
            "qtype": qtype,
            "layers": sorted(layers),
            "规范结论": a["规范结论"],
            "要点解释": pts,
            "依据": a["依据"],
            "风险与需人工复核项": a["风险与需人工复核项"],
        }

        if level == "速查":
            out["要点解释"] = pts[:3]
            return out

        # 标准版：补易混点
        out["易混点"] = self._misreadings(pts)
        if level == "标准":
            return out

        # 论述版：补分层提纲 + 答题模板提示
        outline = []
        if "原因层" in layers:
            outline.append("一、原因与定位：" + "；".join(self._layer_defs("原因层", question)[:3]))
        if "要求层" in layers:
            outline.append("二、基本要求：" + "；".join(self._layer_defs("要求层", question)[:4]))
        if "方法层" in layers:
            outline.append("三、方法路径：" + "；".join(self._layer_defs("方法层", question)[:4]))
        tpl = next((t for t in self.kb.get("formula_bank", [])
                    if qtype in t.get("question_type", "")), None)
        out["论述提纲"] = outline
        if tpl:
            out["答题模板"] = tpl["template"]
            out["评分要点"] = tpl.get("rubric", [])
        return out

    def _layer_defs(self, layer, q, limit=4):
        """从指定层取与问题相关的定义片段；命中不足时取该层前3条默认素材"""
        qclean = re.sub(r"[‘’“”'\"'?？。，,:：！!]", "", q)
        hits = []
        for c in self.kb["concepts"]:
            if c.get("layer") != layer:
                continue
            keys = [c["term"]] + c.get("aliases", []) + c.get("keywords", [])
            if any(k and k in qclean for k in keys):
                hits.append(f"{c['term']}：{c['canonical_definition'][:60]}…")
        if len(hits) < 2:
            pool = [c for c in self.kb["concepts"] if c.get("layer") == layer]
            seen = {h.split("：")[0] for h in hits}
            for c in pool:
                if c["term"] not in seen:
                    hits.append(f"{c['term']}：{c['canonical_definition'][:60]}…")
                if len(hits) >= 3:
                    break
        return hits[:limit]

    def _misreadings(self, pts):
        hits = []
        for m in self.kb["misstatement_bank"]:
            for p in pts:
                if m["correction"][:12] in p or m["wrong"][:8] in p:
                    hits.append(f"避免误区：{m['wrong']}")
                    break
        return list(dict.fromkeys(hits))[:3]

    # ---------- 4. 测验模式 ----------
    def quiz_make(self, n=5, category=None, seed=None):
        cases = [c for c in self.evalset.get("cases", [])
                 if not c.get("must_refuse") and not c.get("must_review")
                 and (category is None or c["category"] == category)]
        if seed is not None:
            random.seed(seed)
        return random.sample(cases, min(n, len(cases)))

    def quiz_score(self, case, user_answer):
        """按要点覆盖率粗略自评：返回命中要点数和参考答案要点"""
        def core(p):
            p = re.sub(r"^(定义|判断|定性|处理|依据|要求|标准|目标|措施|原因|内容|作用|意义|关键|核心|边界)[:：]", "", p)
            return re.sub(r"[、，,。；;：:\s]", "", p)[:8]
        ua = re.sub(r"[、，,。；;：:\s]", "", user_answer)
        hits = [p for p in case["expected_points"] if core(p) and core(p) in ua]
        return {"hit": len(hits), "total": len(case["expected_points"]),
                "hit_points": hits, "reference": case["expected_points"]}
