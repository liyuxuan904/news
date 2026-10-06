# 部署为公开可访问的 Agent 链接

## 方案一：Streamlit Community Cloud（约3分钟）
1. 新建 GitHub 仓库，放入以下文件：
   - `app.py`
   - `run_theory_agent.py`
   - `news_principles_kb.json`
   - `agent_config.json`
   - `system_prompt.txt`
   - `requirements.txt`
2. 打开 https://share.streamlit.io → New app → 选择该仓库 → Main file 填 `app.py` → Deploy。
3. 部署完成后平台自动生成公开链接：`https://<你的应用名>.streamlit.app`
4. 更新：git push 后自动重新部署。

## 方案二：Hugging Face Spaces（约3分钟）
1. 打开 https://huggingface.co/spaces → New Space → SDK 选 Streamlit。
2. 上传上述 6 个文件（可网页上传或 git clone Space 仓库后 push）。
3. 链接形如 `https://huggingface.co/spaces/<用户名>/<空间名>`。

## 方案三：Kimi 开放平台 Hosted Agents（Beta）
若你的开放平台账号已开通 Hosted Agents Beta，可用官方 `POST /v1/agents` 创建托管智能体，
将 `system_prompt.txt` 内容作为系统提示、`theory_agent_tools.json` 的工具交给模型编排；
请求头需带 `kimi-api-version: 2026-09-01-beta`，API 地址 `https://api.moonshot.cn`。
创建后平台分配可调用的 agent 端点。

## 注意事项
- 离线规则引擎的答案是模板化生成，用于答疑演示；考试、出版、对外发布须核对权威原文。
- 不要把 KIMI_API_KEY 写进代码或提交到公开仓库；用平台的环境变量/Secrets 功能注入。
- 涉敏问题（煽动对立、伪造引语、涉密材料）已内置拒答闸门，可在此基础上扩充。
