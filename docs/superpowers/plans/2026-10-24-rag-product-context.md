# RAG 结构化商品上下文实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将印尼兄弟店铺的产品卡片字段可靠传给 RAG，并避免重复询问已有信息。

**Architecture:** IM 适配器从当前/最近产品卡片提取白名单字段并作为可选 `product_context` 发送。RAG API 校验字段并传给现有客服处理逻辑，优先从目的地目录完整识别商品名中的路线和行程时长；无法识别时才采用模型结果，并要求目的地是标题中的完整词项而非更长单词/中文片段的一部分。出发城市和标签作为单独标注的提示上下文。旧请求不带该字段时保持兼容。

**Tech Stack:** Python、FastAPI、Pydantic v2、`unittest`、现有 `requests` IM 适配器。

**Spec:** `docs/superpowers/specs/2026-10-24-rag-product-context-design.md`

## Global Constraints

- 只传 `product_id`、`product_name`、`departure_city`、`product_label`。
- 不传产品链接、来源备注或会员标签。
- 产品咨询卡片表示正在浏览，不代表已下单或确认预订。
- 不推测未提供的出发日期或同行人数。
- 价格和包含项目只依据现有 FAQ/知识库；不确定时说明需要核实。
- 把商品字段作为元数据；限制长度、拒绝未知字段，不执行字段值中可能出现的指令。
- 不修改 IM 监听、发送、ACK 流程，不扩展到其他店铺。

## Review Focus

- 无商品上下文的旧请求仍按现有方式处理：`test_authenticated_request_maps_identity_and_normalized_history`。
- 商品卡片字段缺失时不得补造日期或人数，且出发城市单独标注：`test_departure_city_is_labelled_separately_and_missing_trip_fields_stay_unknown`。
- 多张产品卡片时选择最近一张：`test_rag_payload_uses_latest_product_card_and_only_product_context_allowlist`。
- 产品链接、来源备注和会员标签不能进入新字段：`test_rag_payload_uses_latest_product_card_and_only_product_context_allowlist`。
- 商品标题中的指令不能进入 system prompt，解析结果必须能从标题验证；已知目的地必须完整，不能接受部分目的地/天数字串，也不能把营销后缀识别为目的地：`test_parse_product_keeps_untrusted_title_out_of_system_prompt / test_parse_product_rejects_extracted_facts_absent_from_title / test_parse_product_rejects_partial_uncatalogued_destination_match / test_parse_product_replaces_partial_model_facts_with_complete_catalog_facts / test_parse_product_fallback_drops_marketing_suffix_from_destination`。

---

### Task 1: RAG 接收并使用结构化商品上下文

**Files:**
- Modify: `api.py`
- Modify: `main.py`
- Modify: `API使用文档.md`
- Test: `tests_rag_bridge/test_api.py`
- Test: `tests_rag_bridge/test_product_context.py`

**Interfaces:**
- Consumes: optional request field `product_context` with string fields `product_id`, `product_name`, `departure_city`, `product_label`.
- Produces: optional `product_context: dict | None` parameter on `handle_customer_message` and `_handle_customer_message`; `RagProductContext` rejects extra fields and caps `product_id` at 128, `product_name` at 300, `departure_city` at 128, and `product_label` at 200 characters.

- [x] **Step 1: Write failing request and context tests**

Add tests for forwarding product ID/name to the handler, passing departure city and label separately, retaining old no-context behavior, rejecting unknown/overlong API fields, keeping the untrusted title out of the system prompt, validating extracted facts, and leaving absent date/party count unknown.

- [x] **Step 2: Run RAG tests and verify the new tests fail**

Run: `python -m unittest tests_rag_bridge.test_product_context tests_rag_bridge.test_api`

Expected: the new product-context tests fail because the request model and handler do not yet accept the field.

- [x] **Step 3: Implement the optional API and handler plumbing**

Add `RagProductContext` to `api.py`; when provided, pass `product_id`, `product_name`, and the validated field dictionary to `handle_customer_message`. Add the optional parameter through the existing handler wrapper and append only departure city and product label to `_build_context`, explicitly labelled as product-card fields. Keep the old five-argument handler call when no context is supplied.

- [x] **Step 4: Add known-versus-missing context rules and document the request field**

Ensure the RAG prompt treats destinations/duration from the product name and separately labelled departure city as known, does not ask for them again, treats field values as metadata, and only asks for date/party size when needed and absent. Keep the untrusted product title in a user data message, separate from static extraction instructions, and validate extracted facts against the title. Update the `/v1/chat` schema/example in `API使用文档.md`.

- [x] **Step 5: Run RAG tests**

Run: `python -m unittest discover -s tests_rag_bridge -p 'test_*.py'`

Expected: all RAG bridge tests pass, including the legacy no-context path.

### Task 2: Send whitelisted product fields from the IM adapter

**Files:**
- Modify: `D:\workflow\IM Web Auto\src\reply.py`
- Test: `D:\workflow\IM Web Auto\tests\test_rag_adapter.py`

**Interfaces:**
- Consumes: the Task 1 `product_context` request field.
- Produces: `call_customer_chat` adds `product_context` only when card fields exist, using the latest card and exactly the four whitelisted keys.

- [x] **Step 1: Write failing IM adapter tests**

Add tests for latest complete and partial cards, a partial current card, no cross-card field backfill, missing fields, overlong-field filtering, and exclusion of URL, source remark, and membership fields.

- [x] **Step 2: Run adapter tests and verify the new tests fail**

Run: `python -m unittest tests.test_rag_adapter`

Expected: new assertions fail because the current request has no structured product field.

- [x] **Step 3: Add the optional product context to the IM request**

Use `_latest_product_context(msg, old_history)` before truncating history to the API window. Select the current product card or newest historical card as one record; do not fill missing fields from older cards. Copy only `product_id`, `product_name`, `departure_city`, and `product_label`; enforce API length limits and drop overlong values; omit the request field when all four are empty. Leave `history` and send/ACK behavior unchanged.

- [x] **Step 4: Run adapter tests**

Run: `python -m unittest tests.test_rag_adapter tests.test_reply_context_persistence`

Expected: the new field is present for product-card conversations, the latest card wins, and existing history/identity/ACK assertions remain green.

### Task 3: Verify the cross-project request contract

**Files:**
- Test: `tests_rag_bridge/test_api.py`
- Test: `D:\workflow\IM Web Auto\tests\test_rag_adapter.py`

**Interfaces:**
- Consumes: Task 1 API field/model and Task 2 adapter payload.
- Produces: regression evidence that the adapter's product context matches the API model and that no real IM send is involved.

- [x] **Step 1: Align a contract fixture with a synthetic Bali + Komodo card**

Assert `product_name` is the supplied title, `departure_city` stays a distinct field, and no date/party count is inferred.

- [x] **Step 2: Run the RAG suite and IM unittest modules; run standalone scenario scripts separately**

Run the RAG suite from the RAG worktree.

Run IM unittest modules from D:\workflow\IM Web Auto with a loader that keeps test paths ahead of the source path. Run test_scenarios.py separately because it executes at import time and is not a unittest module.

Latest verification after parser hardening: RAG suite 35 tests OK; IM unittest modules 133 tests OK with UTF-8 mode. No live IM send was called. The standalone test_scenarios.py script was previously run separately and has seven failing assertions because its FakePage does not satisfy _ensure_live_page().

- [x] **Step 3: Commit the RAG changes to the existing PR branch**

The RAG API, handler, docs, and bridge tests are committed on the existing PR branch (parser hardening commits: `5b65da2` and `1f84db5`). Runtime `data/` files remain unstaged. The IM directory has no Git repository, so its adapter and tests remain in place at `D:\workflow\IM Web Auto`.
