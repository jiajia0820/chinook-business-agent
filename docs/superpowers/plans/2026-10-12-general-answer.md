# 通用回答入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为明确的一般知识问题增加独立的通用回答路径，同时保持现有 SQL、业务文档和计算链路不变。

**Architecture:** 在现有任务中增加 `general` 路由；解析器只把没有项目或业务指向的通用问题标记为 `general_knowledge`。回答合成器使用现有 HTTP JSON 模型适配器调用一次模型，模型只返回结构化答案文本，程序负责格式、长度和路由边界检查。

**Tech Stack:** Python、Pydantic、LangGraph、现有 `HTTPJSONModel` 和 unittest。

---

### Task 1: 固定通用问题的路由契约

**Files:**
- Modify: `services/agent_api/app/agent/state.py`
- Modify: `services/agent_api/app/contracts/models.py`
- Modify: `services/agent_api/app/agent/task_policy.py`
- Modify: `services/agent_api/app/agent/parsing.py`
- Test: `services/agent_api/tests/test_general_answer.py`

- [ ] **Step 1: 写失败测试**

测试以下行为：

```python
def test_general_question_uses_general_route():
    parsed = parse_c_v1("有哪些非结构化数据集", profile, fresh_memory())
    assert parsed.decision == "ready"
    assert parsed.task.route == "general"
    assert parsed.task.intent == "general_knowledge"

def test_definition_of_unsupported_business_metric_is_general():
    parsed = parse_c_v1("利润是什么", profile, fresh_memory())
    assert parsed.task.route == "general"

def test_business_fact_stays_sql():
    parsed = parse_c_v1("2025年第三季度音频销售额是多少", profile, fresh_memory())
    assert parsed.task.route == "sql"
```

- [ ] **Step 2: 运行测试并确认因新路由不存在而失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_general_answer
```

预期：测试失败，原因是 `general` 路由尚未进入任务模型和解析器。

- [ ] **Step 3: 实现最小路由契约**

在 `BusinessTask.route` 和公开 `Route` 枚举中加入 `general`；在任务策略中增加只处理明确通用问题的判断，并在 `parse_c_v1` 的“不支持指标”判断前跳过“是什么、定义、解释”等概念问题。

- [ ] **Step 4: 运行测试并确认通过**

运行同一条 unittest 命令，预期新增测试通过，既有任务策略测试也通过。

- [ ] **Step 5: 提交**

```powershell
git add services/agent_api/app/agent/state.py services/agent_api/app/contracts/models.py services/agent_api/app/agent/task_policy.py services/agent_api/app/agent/parsing.py services/agent_api/tests/test_general_answer.py
git commit -m "feat: add general knowledge route contract"
```

### Task 2: 接入通用模型回答并完成链路测试

**Files:**
- Create: `services/agent_api/app/response/general_answer.py`
- Modify: `services/agent_api/app/response/models.py`
- Modify: `services/agent_api/app/response/composer.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/integrations/sql_d12.py`
- Extend: `services/agent_api/tests/test_general_answer.py`

- [ ] **Step 1: 写失败测试**

增加一个返回固定 JSON 的假模型，验证通用路由会调用它并把答案放入 `AskResponse`；再验证没有模型时不会声称已经回答。

- [ ] **Step 2: 运行测试并确认失败**

预期：当前合成器没有通用路由分支，测试失败。

- [ ] **Step 3: 实现最小通用回答器**

增加通用提示、JSON 输出校验、答案长度限制和安全来源说明；在图的路由入口跳过 SQL/RAG 工具，在合成器准备阶段调用模型；通用答案失败时返回明确的不可用或输出无效状态。

- [ ] **Step 4: 运行新增和相关回归测试**

```powershell
.\.venv\Scripts\python.exe -m unittest services.agent_api.tests.test_general_answer services.agent_api.tests.test_agent_task_policy services.agent_api.tests.test_contract_models
```

- [ ] **Step 5: 提交并推送**

```powershell
git add services/agent_api/app/response/general_answer.py services/agent_api/app/response/models.py services/agent_api/app/response/composer.py services/agent_api/app/agent/graph.py services/agent_api/app/integrations/sql_d12.py services/agent_api/tests/test_general_answer.py
git commit -m "feat: answer general knowledge questions with live model"
git push origin main
```

### Task 3: 运行全量相关回归并记录结果

**Files:**
- Verify: `services/agent_api/tests/`

- [ ] **Step 1: 运行 agent API unittest**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s services/agent_api/tests -p "test_*.py"
```

- [ ] **Step 2: 只在发现由本阶段引入的回归时修正**

重点检查现有 SQL、RAG、证据检查和响应契约，不扩大第一阶段范围。

- [ ] **Step 3: 单独提交必要修正并推送**

```powershell
git add <实际修正文件>
git commit -m "test: preserve business routes after general answer path"
git push origin main
```
