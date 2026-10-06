# -*- coding: utf-8 -*-
"""新闻工作党性原则与基本方针 · 理论问答Agent（Web版，含知识库管理页）
部署：与 run_theory_agent.py / news_principles_kb.json / agent_config.json /
      system_prompt.txt / requirements.txt 同仓库部署。
注意：Streamlit Cloud 运行时文件系统只读，网页内的修改仅在本次运行有效；
      请在管理页点击"下载更新后的知识库"，替换仓库中的 JSON 并提交以持久化。
"""
import os, sys, json
import streamlit as st

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from run_theory_agent import TheoryAgent, load_config
from agent_v2 import AgentV2
from agent_v3 import AgentV3

st.set_page_config(page_title="新闻理论问答Agent", page_icon="📰")

config = load_config()


# ---------- 知识库操作（纯函数，便于测试） ----------
KINDS = {"概念": "concepts", "关系": "relations", "误区": "misstatement_bank",
         "文件": "policy_docs", "模板": "formula_bank"}


def key_of(kind, item):
    if kind == "relations":
        return "/".join(item["pair"])
    return item.get({"concepts": "term", "misstatement_bank": "wrong",
                     "policy_docs": "title", "formula_bank": "question_type"}.get(kind, "term"), "?")


def kb_merge(kb, new):
    added = 0
    for kind in KINDS.values():
        existing = {key_of(kind, x) for x in kb.get(kind, [])}
        for item in new.get(kind, []):
            k = key_of(kind, item)
            if k and k not in existing:
                kb[kind].append(item)
                existing.add(k)
                added += 1
    return added


def kb_add_concept(kb, d):
    if any(c["term"] == d["term"] for c in kb["concepts"]):
        return False, f"概念「{d['term']}」已存在"
    kb["concepts"].append({
        "term": d["term"], "canonical_definition": d["definition"],
        "aliases": [x.strip() for x in d["aliases"].split(",") if x.strip()],
        "keywords": [x.strip() for x in d["keywords"].split(",") if x.strip()],
        "dimensions": [], "relations": [],
        "source_level": d["source_level"], "source_refs": [d.get("source_refs", "待核对")],
        "status": d["status"], "notes": d.get("notes", "")})
    return True, "已添加"


def kb_add_mis(kb, d):
    if any(m["wrong"] == d["wrong"] for m in kb["misstatement_bank"]):
        return False, "该错误表述已存在"
    kb["misstatement_bank"].append({
        "wrong": d["wrong"], "correction": d["correction"],
        "severity": d["severity"], "keywords": [x.strip() for x in d["keywords"].split(",") if x.strip()]})
    return True, "已添加"


def kb_delete(kb, kind_cn, key):
    kind = KINDS[kind_cn]
    before = len(kb[kind])
    kb[kind] = [x for x in kb[kind] if key_of(kind, x) != key]
    return len(kb[kind]) != before


# ---------- 状态 ----------
@st.cache_resource
def get_agent():
    v1 = TheoryAgent()
    return AgentV3(v1, AgentV2(v1))  # 统一问答层：有Key走LLM，无Key降级规则引擎


def get_kb():
    if "kb" not in st.session_state:
        with open(os.path.join(BASE, "news_principles_kb.json"), encoding="utf-8") as f:
            st.session_state.kb = json.load(f)
    return st.session_state.kb


def save_kb(kb):
    st.session_state.kb = kb  # 触发 rerun 刷新各页面数据


# ---------- 侧边栏 ----------
st.sidebar.markdown(f"**{config.get('agent_name', '理论问答Agent')}**")
page = st.sidebar.radio("页面", ["问答", "知识库管理"])
st.sidebar.markdown("---")
if page == "问答":
    level = st.sidebar.radio("答案级别", ["标准", "速查", "论述"], index=0)
    st.sidebar.markdown("检索口径库 → LLM理解作答（无Key自动降级规则引擎）")


# ---------- 问答页 ----------
if page == "问答":
    st.title("新闻工作党性原则与基本方针 · 问答Agent")
    st.caption("理论问答与表述校准 · 答案来自本地口径库，正式使用请核对权威原文")
    kb = get_kb()
    st.sidebar.markdown(f"- 概念 {len(kb['concepts'])} 条\n- 关系 {len(kb['relations'])} 组\n- 误区校准 {len(kb['misstatement_bank'])} 条")
    _ag = get_agent()
    st.sidebar.caption(f"当前作答模式：{'Kimi LLM' if _ag.mode == 'llm' else '规则引擎（设KIMI_API_KEY升级）'}")

    if "history" not in st.session_state:
        st.session_state.history = []
    agent = get_agent()
    for q, res in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            st.markdown(res)

    examples = ["什么是新闻工作的党性原则？", "党性和人民性的关系是什么？",
                "辨析：‘正面宣传为主就是只能唱赞歌。’", "为什么要坚持党性原则？"]
    cols = st.columns(4)
    for i, ex in enumerate(examples):
        if cols[i].button(ex[:12] + "…", key=f"ex{i}", use_container_width=True):
            st.session_state.pending = ex

    q = st.chat_input("输入你的问题……")
    if st.session_state.get("pending"):
        q = st.session_state.pending
        del st.session_state.pending

    if q:
        agent = get_agent()
        agent.a.kb = kb   # 同步会话中的最新知识库
        agent.kb = kb
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            res = agent.answer(q, level=level)
            if res["decision"] == "answer":
                md = res["answer_md"]
                rel = res.get("related", [])
                if len(rel) > 1:
                    md += "\n\n**相关条目**：" + " | ".join(f"{x['term']}({x['type']})" for x in rel)
                if res.get("mode") == "llm":
                    st.caption("由 Kimi 基于口径库材料作答")
            else:
                d = "已拒答" if res["decision"] == "refuse" else "转人工复核"
                md = f"**{d}**：{res['reason']}\n\n安全替代：{res.get('safe_alternative') or '请提供正式公开文本或走口径核定流程。'}"
            st.markdown(md)
            st.session_state.history.append((q, md))


# ---------- 知识库管理页 ----------
else:
    st.title("知识库管理")
    kb = get_kb()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("概念", len(kb["concepts"]))
    c2.metric("关系", len(kb["relations"]))
    c3.metric("误区", len(kb["misstatement_bank"]))
    c4.metric("文件", len(kb["policy_docs"]))

    tab_add, tab_del, tab_io, tab_browse = st.tabs(["新增条目", "删除条目", "导入 / 导出", "浏览"])

    with tab_add:
        st.subheader("新增概念")
        with st.form("add_concept"):
            term = st.text_input("术语名（必填，唯一）")
            definition = st.text_area("规范定义（必填）", height=100)
            ca, cb = st.columns(2)
            aliases = ca.text_input("别名（逗号分隔，可空）")
            keywords = cb.text_input("提问关键词（逗号分隔，建议必填，如：四力,脚力）")
            cc, cd = st.columns(2)
            source_level = cc.selectbox("来源等级", ["A", "B", "C"], index=0)
            status = cd.selectbox("口径状态", ["current", "historical", "academic"], index=0)
            source_refs = st.text_input("来源（可空，默认待核对）")
            if st.form_submit_button("添加概念"):
                if not term.strip() or not definition.strip():
                    st.error("术语名和规范定义不能为空")
                else:
                    ok, msg = kb_add_concept(kb, {"term": term.strip(), "definition": definition.strip(),
                                                  "aliases": aliases, "keywords": keywords,
                                                  "source_level": source_level, "status": status,
                                                  "source_refs": source_refs.strip() or "待核对"})
                    (st.success if ok else st.warning)(msg)
                    save_kb(kb)

        st.subheader("新增误区校准")
        with st.form("add_mis"):
            wrong = st.text_input("错误说法（必填，唯一）")
            correction = st.text_area("纠正表述（必填）", height=80)
            ea, eb = st.columns(2)
            severity = ea.selectbox("严重度", ["高", "中", "低"], index=1)
            mkeys = eb.text_input("触发关键词（逗号分隔）")
            if st.form_submit_button("添加误区"):
                if not wrong.strip() or not correction.strip():
                    st.error("错误说法和纠正表述不能为空")
                else:
                    ok, msg = kb_add_mis(kb, {"wrong": wrong.strip(), "correction": correction.strip(),
                                              "severity": severity, "keywords": mkeys})
                    (st.success if ok else st.warning)(msg)
                    save_kb(kb)

    with tab_del:
        st.subheader("删除条目")
        kind_cn = st.selectbox("类型", list(KINDS.keys()))
        kind = KINDS[kind_cn]
        options = [key_of(kind, x) for x in kb[kind]]
        target = st.selectbox("条目", options)
        if st.button("确认删除", type="primary"):
            if kb_delete(kb, kind_cn, target):
                st.success(f"已删除「{target}」")
                save_kb(kb)
            else:
                st.warning("未找到该条目")

    with tab_io:
        st.subheader("导出")
        st.download_button("下载更新后的知识库 JSON",
                           data=json.dumps(kb, ensure_ascii=False, indent=2),
                           file_name="news_principles_kb.json", mime="application/json")
        st.info("云端运行时文件系统只读：下载后请替换 GitHub 仓库中的同名文件并提交，修改才会永久生效；push 后应用自动重新部署。")
        st.subheader("公共源自检")
        if st.button("测试公共知识源连通性"):
            try:
                from public_kb import PublicKB
                try:
                    brave = st.secrets.get("BRAVE_API_KEY", None)
                except Exception:
                    brave = None
                pub = PublicKB(brave_key=brave)
                for name, src in pub.sources.items():
                    try:
                        hits = pub.search("民法典" if name == "flk" else "新闻", sources=[name], top_k=1)
                        if hits:
                            st.success(f"{name}：连通 ✓ 命中《{hits[0]['title']}》")
                        else:
                            st.warning(f"{name}：连通 ✓ 但无命中结果")
                    except Exception as e:
                        st.error(f"{name}：失败 ✗ {type(e).__name__}：{e}")
                if "brave" not in pub.sources:
                    st.info("未配置 BRAVE_API_KEY，Brave 全网搜索未启用（可选增强）")
            except Exception as e:
                st.error(f"自检失败：{e}")
        st.subheader("导入合并")
        up = st.file_uploader("上传 entries.json（支持 concepts / relations / misstatement_bank 等数组，按主键去重合并）",
                              type=["json"])
        if up and st.button("执行合并"):
            try:
                n = kb_merge(kb, json.loads(up.read().decode("utf-8")))
                st.success(f"合并完成，新增 {n} 条")
                save_kb(kb)
            except Exception as e:
                st.error(f"合并失败：{e}")

    with tab_browse:
        st.subheader("浏览")
        bkind_cn = st.selectbox("类型", list(KINDS.keys()), key="browse_kind")
        bkind = KINDS[bkind_cn]
        for x in kb[bkind]:
            with st.expander(key_of(bkind, x)):
                st.json(x)
