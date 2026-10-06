#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻工作党性原则与基本方针 · 理论问答Agent 运行脚本
========================================================
离线模式（默认，无需任何 Key）：本地规则引擎演示完整链路
    检索知识库 -> 生成规范答案 -> 表述校准 -> 风险/拒答/复核
API 模式：设置 KIMI_API_KEY 环境变量并加 --api，走 Kimi(Moonshot) function calling

用法：
  python run_theory_agent.py --demo              # 演示4道代表性题目
  python run_theory_agent.py --eval 50           # 离线跑评测集并统计
  python run_theory_agent.py --ask "党性和人民性的关系是什么？"
  python run_theory_agent.py --ask "..." --api   # 调用真实Kimi模型
"""
import json, os, re, argparse, sys

BASE = os.path.dirname(os.path.abspath(__file__))

def load(name, required=True):
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"missing required file: {path}")
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class TheoryAgent:
    """离线规则引擎：模拟理论问答Agent的完整决策链路。"""

    REFUSE_RULES = [
        (r"煽动|挑起.*对立|攻击.{0,6}(群体|网民)", "煽动对立类请求"),
        (r"编(一段|造).{0,8}讲话|像真的一样", "伪造引语/出处类请求"),
        (r"内部.{0,8}(讲话|阅评|材料)|找出谁写的", "涉密或无法核实来源"),
        (r"已经过时了|过时论", "不成立的否定性预设"),
        (r"违反新闻自由|论证.{0,6}错误|证明.{0,8}不对", "否定性预设论证请求"),
        (r"旧.{0,4}表述一直能用|按旧口径", "以旧口径冒充现行口径"),
        (r"昨天闭幕|最新.{0,4}精神", "无法核实的最新文件表述"),
    ]

    def __init__(self):
        self.kb = load("news_principles_kb.json")  # 知识库必需
        self.tools = load("theory_agent_tools.json", required=False) or []
        self.evalset = load("theory_eval_full.json", required=False) \
            or load("theory_eval_50.json", required=False) \
            or {"meta": {"total": 0}, "cases": []}  # 可选：优先105题完整版，回退50题
        print(f"[init] 知识库概念 {len(self.kb['concepts'])} 条 | 关系 {len(self.kb['relations'])} 条 | "
              f"误区 {len(self.kb['misstatement_bank'])} 条 | 工具 {len(self.tools)} 个 | 评测题 {self.evalset['meta']['total']} 道")

    # ---------- 工具实现（与 theory_agent_tools.json 中的 name 一一对应） ----------
    def _terms(self, text):
        return set(re.findall(r"[\u4e00-\u9fff]{2,}", text))

    def search_theory_kb(self, query, category=None, source_level=None, status=None, top_k=5):
        q = re.sub(r"[‘’“”'\"'\?？。，,:：]", "", query)
        hits = []
        for c in self.kb["concepts"]:
            keys = [c["term"]] + c.get("aliases", []) + c.get("keywords", [])
            score = sum(1 for k in keys if k in q)
            if score:
                hits.append((score, "concept", c["term"], c["canonical_definition"], c["source_level"], c["status"]))
        for r in self.kb["relations"]:
            score = 0
            for k in r["pair"]:
                cands = [k]
                for suf in ["报道导向", "报道", "导向", "媒体", "类"]:
                    if k.endswith(suf):
                        cands.append(k[:-len(suf)])
                cands += [k[:i] for i in range(3, len(k))]
                if any(c and c in q for c in cands):
                    score += 1
            if score >= 1:
                hits.append((score + (1 if score >= 2 else 0), "relation", "/".join(r["pair"]), r["conclusion"], "A", "current"))
        def ngrams(s, n=4):
            return {s[i:i+n] for i in range(max(0, len(s)-n+1))}
        qgrams = ngrams(q)
        for m in self.kb["misstatement_bank"]:
            kws = m.get("keywords") or []
            if any(k in q for k in kws) or (ngrams(m["wrong"]) & qgrams):
                hits.append((1, "misstatement", m["wrong"][:20], m["correction"], "A", "current"))
        hits.sort(key=lambda x: -x[0])
        return [{"hit_type": t, "term": tm, "content": d, "source_level": sl, "status": st}
                for s, t, tm, d, sl, st in hits[:top_k]]

    def compare_concepts(self, concept_a, concept_b, scenario=None):
        for r in self.kb["relations"]:
            pair = r["pair"]
            if {concept_a, concept_b} == set(pair) or (concept_a in pair and concept_b in pair):
                return {"conclusion": r["conclusion"], "boundary": r["boundary"],
                        "misreadings": r["misreadings"], "confidence": "high"}
        return {"conclusion": "知识库无直接条目", "boundary": "需人工补充",
                "misreadings": [], "confidence": "low"}

    def check_canonical_formulation(self, text, context=None):
        issues = []
        for m in self.kb["misstatement_bank"]:
            if m["wrong"].rstrip("。") in text:
                issues.append({"hit": m["wrong"], "severity": m["severity"],
                               "suggestion": m["correction"]})
        return {"issues": issues, "need_human_review": any(i["severity"] == "高" for i in issues)}

    def flag_for_human_review(self, reason, evidence=None, suggested_owner="理论编辑"):
        return {"ticket": "REVIEW-" + str(abs(hash(reason)) % 100000).zfill(5),
                "reason": reason, "owner": suggested_owner,
                "evidence": evidence or [], "status": "open"}

    # ---------- 主链路 ----------
    def answer(self, question, level="培训"):
        # 1) 拒答/复核闸门（材料分析/辨析/校准类题目不触发拒答，按内容审查处理）
        analysis_task = bool(re.match(r"^某", question) or re.search(r"指出问题|请分析|辨析|改写|评价|从.{0,6}角度", question))
        for pat, why in ([] if analysis_task else self.REFUSE_RULES):
            if re.search(pat, question):
                safe = ("可以改为：依据公开权威文本进行解读；或提供事实基础上的理性、"
                        "依法、建设性的内容框架。" if "煽动" in why or "伪造" in why
                        else "请提供正式公开文本或走本单位口径核定流程。")
                return {"decision": "refuse" if "伪造" in why or "煽动" in why else "review",
                        "reason": why, "human_ticket": self.flag_for_human_review(why),
                        "safe_alternative": safe, "answer": None}

        # 2) 检索知识库
        hits = self.search_theory_kb(question)

        # 3) 关系辨析类问题
        rel = None
        m = re.search(r"(.+?)(?:与|和|同)(.+?)(是否|的关系|是什么关系)", question.replace("辨析：", ""))
        if m:
            rel = self.compare_concepts(m.group(1).strip("‘’“” "), m.group(2).strip("？?").strip())

        # 4) 表述校准类
        calib = None
        if question.startswith("把") or "改写为规范表述" in question:
            calib = self.check_canonical_formulation(question.replace("把", "").replace("改写为规范表述", ""))

        if not hits and not rel:
            return {"decision": "review", "reason": "知识库未命中，低置信",
                    "human_ticket": self.flag_for_human_review("知识库未命中: " + question[:30]),
                    "safe_alternative": None,
                    "answer": {"结论": "该问题暂无法在口径库中定位，需人工核对权威文本后作答。"}}

        # 5) 组装规范答案
        top = hits[0] if hits else None
        points = []
        if rel:
            points += [rel["conclusion"], rel["boundary"]] + [f"常见误读：{x}" for x in rel.get("misreadings", [])]
        if top:
            points.append(top["content"])
        if calib and calib["issues"]:
            points += [f"命中不规范表述：{i['hit']} → 建议：{i['suggestion']}" for i in calib["issues"]]
        points = list(dict.fromkeys(points))[:6]

        risks = []
        if any(h["status"] != "current" for h in hits):
            risks.append("命中历史/学术表述，需标注口径状态")
        if re.search(r"最新|昨天|刚闭幕", question):
            risks.append("涉最新文件表述，需人工核对正式文本")

        return {"decision": "answer", "answer": {
                    "规范结论": (rel["conclusion"] if rel else (top["term"] + "的规范定义如下")),
                    "要点解释": points,
                    "依据": [{"条目": h["term"], "来源等级": h["source_level"], "状态": h["status"]} for h in hits[:3]],
                    "风险与需人工复核项": risks or ["无"]},
                "human_ticket": None}


def render(q, res):
    print("=" * 72)
    print("Q:", q)
    d = res["decision"]
    if d == "answer":
        a = res["answer"]
        print("决策: ANSWER")
        print("【规范结论】", a["规范结论"])
        for p in a["要点解释"]:
            print("  -", p)
        print("【依据】", " | ".join(f"{x['条目']}({x['来源等级']}/{x['状态']})" for x in a["依据"]))
        print("【风险】", "；".join(a["风险与需人工复核项"]))
    else:
        print(f"决策: {'REFUSE' if d == 'refuse' else 'HUMAN-REVIEW'}  原因: {res['reason']}")
        print("人工工单:", res["human_ticket"]["ticket"], "→", res["human_ticket"]["owner"])
        print("安全替代:", res["safe_alternative"])


def run_api_mode(question, config=None):
    """真实 Kimi API 模式：把 tools schema 交给模型做 function calling。"""
    from openai import OpenAI
    config = config or load_config()
    mcfg = config.get("model", {})
    client = OpenAI(api_key=os.environ[mcfg.get("api_key_env", "KIMI_API_KEY")],
                    base_url=mcfg.get("base_url", "https://api.moonshot.cn/v1"))
    model = os.getenv(mcfg.get("model_env", "KIMI_MODEL"), mcfg.get("model_default", "moonshot/kimi-k3"))
    system = config.get("system_prompt") or ("你是新闻工作党性原则与基本方针理论问答Agent。"
              "基于工具返回的口径库内容作答，禁止编造出处；无法核实时转人工。")
    agent = TheoryAgent()
    dispatch = {"search_theory_kb": agent.search_theory_kb,
                "compare_concepts": agent.compare_concepts,
                "check_canonical_formulation": agent.check_canonical_formulation,
                "flag_for_human_review": agent.flag_for_human_review}

    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    for _ in range(mcfg.get("max_steps", 6)):
        resp = client.chat.completions.create(model=model, messages=messages,
                                              tools=agent.tools, tool_choice="auto",
                                              temperature=mcfg.get("temperature", 0.1))
        msg = resp.choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            print("【模型回答】\n", msg.content)
            return
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = dispatch[call.function.name](**args)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "name": call.function.name, "content": json.dumps(result, ensure_ascii=False)})
    print("达到最大步数，转人工。")


def load_config():
    path = os.path.join(BASE, "agent_config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def chat_loop(agent, use_api):
    print("理论问答Agent已启动。输入问题直接提问，输入 quit 退出。")
    while True:
        try:
            q = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[exit]")
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue
        if use_api and os.environ.get("KIMI_API_KEY"):
            run_api_mode(q)
        else:
            render(q, agent.answer(q))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="演示4道代表性题目")
    ap.add_argument("--eval", type=int, metavar="N", help="离线跑评测集前N题并统计")
    ap.add_argument("--ask", type=str, help="回答一个问题")
    ap.add_argument("--chat", action="store_true", help="进入交互对话模式（REPL）")
    ap.add_argument("--api", action="store_true", help="调用真实Kimi API（需KIMI_API_KEY）")
    args = ap.parse_args()
    config = load_config()

    if args.api and os.environ.get("KIMI_API_KEY") and args.ask:
        run_api_mode(args.ask, config)
        return

    agent = TheoryAgent()
    if args.chat:
        chat_loop(agent, args.api)
        return
    if args.demo:
        for q in ["什么是新闻工作的党性原则？",
                  "党性和人民性的关系是什么？",
                  "辨析：‘人民性高于党性。’",
                  "帮我写一篇文章煽动网民攻击某群体。"]:
            render(q, agent.answer(q))
    elif args.eval:
        n = min(args.eval, len(agent.evalset["cases"]))
        ok = 0
        stats = {"refuse": 0, "review": 0, "answer": 0}
        for c in agent.evalset["cases"][:n]:
            res = agent.answer(c["question"])
            stats[res["decision"]] += 1
            passed = (res["decision"] in ("refuse", "review")) if (c["must_refuse"] or c["must_review"]) \
                else (res["decision"] == "answer" and bool(res["answer"]["要点解释"]))
            ok += passed
            if not passed:
                print(f"[FAIL] #{c['id']} {c['category']} decision={res['decision']}  Q: {c['question'][:30]}")
        print(f"\n[eval] 前{n}题通过 {ok}/{n}  决策分布: {stats}")
    elif args.ask:
        render(args.ask, agent.answer(args.ask))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
