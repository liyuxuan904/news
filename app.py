# -*- coding: utf-8 -*-
"""新闻工作党性原则与基本方针 · 理论问答Agent（Web版）
部署：将本文件与 news_principles_kb.json / agent_config.json / system_prompt.txt
      放在同一仓库，部署到 Streamlit Community Cloud 或 Hugging Face Spaces。
"""
import os, sys, json
import streamlit as st

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from run_theory_agent import TheoryAgent, load_config

st.set_page_config(page_title="新闻理论问答Agent", page_icon="📰")
st.title("新闻工作党性原则与基本方针 · 问答Agent")
st.caption("理论问答与表述校准 · 答案来自本地口径库，正式使用请核对权威原文")

config = load_config()
st.sidebar.markdown(f"**{config.get('agent_name', '理论问答Agent')}** v{config.get('version', '1.0')}")
kb_info = json.load(open(os.path.join(BASE, "news_principles_kb.json"), encoding="utf-8"))
st.sidebar.markdown(
    f"- 概念 {len(kb_info['concepts'])} 条\n"
    f"- 关系 {len(kb_info['relations'])} 组\n"
    f"- 误区校准 {len(kb_info['misstatement_bank'])} 条"
)
mode = st.sidebar.radio("运行模式", ["离线规则引擎", "Kimi API（需Key，预留）"])
st.sidebar.markdown("---")
st.sidebar.markdown("**决策链路**：检索口径库 → 组装规范答案 → 表述校准 → 拒答/人工复核闸门")

if "history" not in st.session_state:
    st.session_state.history = []

agent = TheoryAgent()
for q, res in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(q)
    with st.chat_message("assistant"):
        st.markdown(res)

examples = ["什么是新闻工作的党性原则？", "党性和人民性的关系是什么？",
            "辨析：‘正面宣传为主就是只能唱赞歌。’", "什么是‘三贴近’和‘时度效’？"]
cols = st.columns(4)
for i, ex in enumerate(examples):
    if cols[i].button(ex[:12] + "…", key=f"ex{i}", use_container_width=True):
        st.session_state.pending = ex

q = st.chat_input("输入你的问题，例如：真实性和舆论导向是什么关系？")
if st.session_state.get("pending"):
    q = st.session_state.pending
    del st.session_state.pending

if q:
    with st.chat_message("user"):
        st.markdown(q)
    with st.chat_message("assistant"):
        if mode.startswith("Kimi"):
            st.info("Web 版当前使用离线规则引擎；API 模式请在命令行使用 run_theory_agent.py --chat --api")
        res = agent.answer(q)
        if res["decision"] == "answer":
            a = res["answer"]
            md = f"**规范结论**：{a['规范结论']}\n\n"
            md += "\n".join(f"- {p}" for p in a["要点解释"])
            md += "\n\n**依据**：" + " | ".join(f"{x['条目']}（{x['来源等级']}/{x['状态']}）" for x in a["依据"])
            md += "\n\n**风险**：" + "；".join(a["风险与需人工复核项"])
        else:
            d = "已拒答" if res["decision"] == "refuse" else "转人工复核"
            md = f"**{d}**：{res['reason']}\n\n安全替代：{res.get('safe_alternative') or '请提供正式公开文本或走口径核定流程。'}"
        st.markdown(md)
        st.session_state.history.append((q, md))
