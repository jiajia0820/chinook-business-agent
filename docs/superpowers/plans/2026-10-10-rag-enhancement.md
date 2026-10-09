# A 线 RAG Enhancement Implementation Plan

> **For agentic workers:** Execute this plan task by task with tests and a local Git commit after each phase.

**Goal:** Improve the Chinook Agent RAG path using business-aware hybrid retrieval, source authority, multi-document coverage, structured table evidence, and evidence planning while preserving current API contracts.

**Architecture:** Keep the existing lexical index and LangGraph boundaries. Add profile-driven document metadata, query expansion and authority-aware ranking in the knowledge index; add coverage-aware retrieval filters; add a small structured-record layer for target tables; expose evidence requirements through retrieval filters without changing the public response schema.

**Tech Stack:** Python, Pydantic, existing knowledge index, pypdf/PIL/OCR readers, unittest/pytest, existing evaluation runner.

---

### Task 1: Establish baseline and add A1 retrieval regression tests

**Files:**
- Create: `docs/superpowers/plans/2026-10-10-rag-enhancement.md`
- Modify: `services/agent_api/tests/test_knowledge_index.py`
- Modify: `services/agent_api/tests/test_all_documents_rag.py`

- [ ] Run the focused knowledge tests before changes: `python -m pytest services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_all_documents_rag.py -q`.
- [ ] Add failing tests for D03 alias authority, D07/D08 multi-document coverage, and query expansion from configured aliases.
- [ ] Run the new tests and confirm they fail because current retrieval returns the wrong source or misses a required source.
- [ ] Commit the plan and baseline tests only: `git add docs/superpowers/plans/2026-10-10-rag-enhancement.md services/agent_api/tests/test_knowledge_index.py services/agent_api/tests/test_all_documents_rag.py && git commit -m "test: define RAG retrieval quality gates"`.

### Task 2: A1 business-aware hybrid retrieval

**Files:**
- Modify: `services/agent_api/app/profiles/models.py`
- Modify: `services/agent_api/app/profiles/configs/chinook-music.json`
- Modify: `services/agent_api/app/knowledge/index.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/tests/test_knowledge_index.py`
- Modify: `services/agent_api/tests/test_all_documents_rag.py`

- [ ] Add optional profile metadata for document topics, answer types, authority scopes, related documents, and alias source references, with backward-compatible defaults.
- [ ] Add retrieval planning fields inside `RetrievalRequest.filters`: `required_sources`, `preferred_doc`, `answer_type`, and `query_terms`.
- [ ] Expand configured aliases and entity labels into retrieval terms; preserve exact IDs, years, quarters, and months.
- [ ] Rank candidates with lexical score plus entity/time/topic matching, preferred source boosts, answer-type authority, and a coverage reward; cap repeated chunks from one document when required sources exist.
- [ ] Add source-specific retrieval plans for alias/classification, target/review, formula, activity, and multidoc tasks in the graph before calling RAG.
- [ ] Run the focused tests, then the knowledge and integration test modules.
- [ ] Commit A1: `git add services/agent_api/app services/agent_api/tests && git commit -m "feat: add authority-aware hybrid RAG retrieval"`.

### Task 3: A2 structured target and OCR evidence records

**Files:**
- Modify: `services/agent_api/app/knowledge/documents.py`
- Modify: `services/agent_api/app/knowledge/index.py`
- Modify: `services/agent_api/app/contracts/models.py`
- Create: `services/agent_api/tests/test_structured_knowledge.py`

- [ ] Add failing tests for a D06 month/metric structured lookup and preservation of source locator metadata.
- [ ] Extract recognizable table rows/cells from D06 text into an internal structured-record index; keep original chunks unchanged.
- [ ] Add an internal structured retrieval path selected by `answer_type=target_value` or `table_cell`, returning ordinary `DocumentChunk` evidence plus structured metadata in existing internal models.
- [ ] Run structured tests and the full agent API test suite.
- [ ] Commit A2: `git add services/agent_api/app services/agent_api/tests && git commit -m "feat: index structured target evidence"`.

### Task 4: A3 evidence requirements and phased evaluation

**Files:**
- Modify: `services/agent_api/app/agent/parsing.py`
- Modify: `services/agent_api/app/agent/graph.py`
- Modify: `services/agent_api/app/response/composer.py`
- Modify: `services/agent_api/app/contracts/models.py`
- Create: `services/agent_api/tests/test_rag_evidence_plan.py`

- [ ] Add failing tests for required-source plans on multidoc and formula/document-calculation intents.
- [ ] Build bounded evidence requirements from the parsed intent and pass them to RAG; do not create an unbounded agent loop.
- [ ] Preserve current public status/route values while exposing missing-source limitations and evidence coverage in trace/limitations.
- [ ] Run all unit/integration tests.
- [ ] Commit A3: `git add services/agent_api/app services/agent_api/tests && git commit -m "feat: add bounded RAG evidence planning"`.

### Task 5: Evaluation and final verification

**Files:**
- Modify only if evaluation adapters require a compatibility fix.
- Create: `tmp/eval-rag-enhanced-2026-10-10.json` (ignored evaluation artifact).

- [ ] Run the focused A-line suites and record retrieval/source metrics.
- [ ] Run the full live evaluation with the existing command and output path `tmp/eval-rag-enhanced-2026-10-10.json`.
- [ ] Compare baseline and enhanced results by dataset and failure reason.
- [ ] Run `git diff --check`, inspect `git status`, and verify no unrelated PPT changes are staged.
- [ ] Commit any compatibility fixes separately, then report the final score and remaining failures.
