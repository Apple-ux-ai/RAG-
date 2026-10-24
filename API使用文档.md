# 旅行管家客服 API 使用文档

> 基于本地 FAQ 知识库 + 通义千问大模型的度假旅行 RAG 客服接口。版本 `1.0.0`

---

## 1. 服务地址

- **公网地址（调用方使用）**：`http://8.163.65.89:8000`
- 服务本机启动监听 `0.0.0.0:8000`（监听所有网卡，`0.0.0.0` 不是可访问的目标地址）。
- 若与 API 同在**同一台服务器**上调试，可填 `http://127.0.0.1:8000` 或 `http://localhost:8000`。

> ⚠️ 调用方一律填上面的**公网地址**或 `127.0.0.1`，**不要**填 `http://0.0.0.0:8000`（连不通）。

接口使用 `HTTP + JSON`，请求头带 `Content-Type: application/json`。旧客服接口的客户状态仍按请求历史推导；本机 IM 兼容接口另用 SQLite 持久化发送确认状态，不保存客户状态 JSON 文件。

---

## 2. 通用说明（调用前必读）

| 事项 | 说明 |
|------|------|
| 鉴权 | 旧接口当前 **Token 鉴权已关闭**；`/v1/chat` 和 `/v1/delivery/ack` 使用独立 `RAG_CHAT_TOKEN` Bearer 鉴权，未配置时拒绝服务。 |
| 限流 | 每个 IP **每分钟最多 30 次**，超出返回 `429`。 |
| 跨域 | 默认放行所有来源（`allow_origins="*"`），不带凭证。指定来源用环境变量 `ALLOWED_ORIGINS`（逗号分隔）。 |
| 超时 | 单次处理超时 `60` 秒；`/chat` 超时走保底话术，`/ask` 超时返回 `504`。 |
| 错误码 | `400` 参数错误 · `429` 限流 · `500` 内部异常 · `504` 抽取超时 |

失败响应统一为：`{ "detail": "错误描述" }`

---

## 3. 接口一览

| 方法 + 路径 | 用途 | 核心传入 | 核心返回 |
|------|------|----------|----------|
| `GET /` | 健康检查 | 无 | `status`, `faq_count` |
| `POST /chat` | 客服对话（FAQ+RAG） | `question`, `session_id`, `context` | `details[]`, `closing` |
| `POST /ask` | 通用问答/抽取 | `system_prompt`, `user_message` | `reply` |
| `POST /customer_chat` | **客户消息统一处理（推荐）** | `username`, `product_id`, `product_name`, `message`, `history` | `reply`, `analysis`, `side_effects` |
| `POST /v1/chat` | 本机 IM 兼容回复（印尼兄弟试点） | 稳定消息/客户/会话 ID、`text`, `history` | `action`, `reply`, `send_reply`, `trace_id`, `duplicate` |
| `POST /v1/delivery/ack` | 确认 IM 已成功发送 | 稳定消息/客户/会话 ID | `status` |

下文对每个接口明确列出 **「传入参数」** 与 **「收到参数」**。

---

## 4. 接口详情

### 4.1 GET `/` 健康检查

**传入参数**：无。

**收到参数**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 固定 `"ok"` 表示服务正常 |
| `faq_count` | int | 已加载的 FAQ 条数 |

**示例**
```bash
curl http://8.163.65.89:8000/
# 收到：{"status":"ok","faq_count":40}
```

---

### 4.2 POST `/chat` 客服对话

**传入参数**（`ChatRequest`）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 用户问题（≤2000 字，不可为空） |
| `session_id` | string | 是 | 会话 ID，调用方自行拟定；多次带同一 ID 可延续上下文 |
| `history` | array | 否，建议传 | 本轮之前的客户与客服对话；格式与 `/customer_chat` 相同，显式传入优先，`[]` 表示无历史 |
| `context` | string | 否 | 产品/目的地上下文，用于"XX定制师"称呼（非客户原话） |

**收到参数**（`ChatResponse`）：

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 回传的会话 ID |
| `question` | string | 用户原始问题 |
| `details` | array | 子问题与答案列表，每项含 `sub_question` / `answer` / `matched_faq` / `source`；内部规则回复对外仍用旧值 `ai`，不新增 `rule` 枚举要求 |
| `closing` | string | 按需收尾语，可能为空；非空时仅拼接一次 |

**示例**
```bash
curl -X POST http://8.163.65.89:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"斐济最佳季节？","session_id":"sess_abc123","context":"斐济+大溪地8日7晚私家团"}'
```
```json
{
  "session_id": "sess_abc123",
  "question": "斐济最佳季节？",
  "details": [{"sub_question":"斐济最佳季节？","answer":"斐济旱季为5-10月……","matched_faq":[],"source":"ai"}],
  "closing": "方便留一个耳关系方式☎️吗？更方便为您服务哦~"
}
```

---

### 4.3 POST `/ask` 通用问答 / 信息抽取

**传入参数**（`AskRequest`）：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `system_prompt` | string | 是 | — | 系统提示词 |
| `user_message` | string | 是 | — | 用户消息 |
| `temperature` | float | 否 | `0.7` | 采样温度 |
| `max_tokens` | int | 否 | `500` | 最大生成 token |
| `model` | string | 否 | `"qwen-turbo"` | 模型名 |

**收到参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `reply` | string | 千问生成的回复文本 |

**示例**
```bash
curl -X POST http://8.163.65.89:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"system_prompt":"只输出城市名","user_message":"我想去巴厘岛和清迈"}'
# 收到：{"reply":"巴厘岛，清迈"}
```

---

### 4.4 POST `/customer_chat` 客户消息统一处理（核心接口）

**传入参数**（`CustomerChatRequest`）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 平台客户唯一 ID；同一客户保持稳定，不同客户不得共用，不能使用可重名的昵称 |
| `product_id` | string | 否 | 当前咨询商品 ID |
| `product_name` | string | 否，建议传 | 当前咨询商品完整标题；其中的目的地会补充到客户需求中，客户明确修改后以客户为准 |
| `message` | string | 是 | 客户本条消息（≤2000 字，不可为空） |
| `history` | array | 强烈建议每次传 | 本客户在当前消息之前的完整历史，元素格式 `{ "role": "user"\|"assistant", "content": "..." }`；显式传入时以此为准，`[]` 表示无历史。省略时仅兼容本进程最近10轮记忆；跨worker、重启或截断后不能依赖此兼容记忆 |
| `message_id` | string | 否 | 推荐传平台消息唯一ID；同一消息重试沿用ID，客户再次发言使用新ID |

最少只需填写 `username` 和 `message`，例如：

```json
{"username":"platform_customer_001","message":"过年，四人"}
```

正式接入建议同时传 `product_id`、`product_name`、`message_id` 和正确的 `history`。`username` 是平台客户唯一 ID，同一客户每轮保持一致，不随商品、消息或重试改变；`history` 按时间从旧到新，只含该客户消息（`role=user`）与客服回复（`role=assistant`），每条有 `content`。同一客户的请求按先后顺序提交；若手机号消息还未处理完成就先提交后续咨询，后者可能尚未看到留资。电话、vx、目的地和出行需求通过 `message` 自动识别，不需要另外增加手机号字段，也不需要传 `session_id`。请求头使用 `Content-Type: application/json`；当前无需 Token。

所有 worker、实例只根据调用方传入的当前客户历史重建状态，不共享客户数据库。`CUSTOMER_CONTACT_DB`、`CUSTOMER_DEDUP_DB` 已不使用。`history=[]` 会按无历史处理，不能再从旧文件找回留资或目的地；历史中漏掉号码、目的地或用途确认过程时，接口可能重新询问。旧数据库文件不再读取，不自动删除。

**收到参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `reply` | string | 最终回复文本 |
| `analysis` | object | 本轮分析结果：联系方式、是否广告、分群等 |
| `analysis.context_diagnostics` | object | 上下文诊断：历史来源、收到/采用/忽略条数、客户原文条数、识别的目的地、真实留资状态及格式警告；不改变对客回复 |
| `side_effects` | object | 副作用信息：已登记/推送的联系方式等 |
| `send_reply` | boolean | 是否发送本次 `reply`；为 false 时跳过发送、入库和界面气泡 |
| `duplicate` | boolean | 是否命中重复请求 |
| `message_id` | string | 回传本条平台消息ID |

`/customer_chat` 的 `reply` 已包含按需收尾语，发送端不要另加固定问候或留资话术。

**上下文中的留资判断**：按时间扫描本次 `history` 中客户自己提供的手机号、明确标识的vx、带区号座机等，再合并当前消息；数字用途确认和区号补全也从历史重放。客户自述“留过了”或客服声称“已登记”不能代替实际号码。历史包含真实留资时不再索要；同类型以当前历史中最新提供的号码为准。明确传入的历史优先于任何进程内记忆；不会使用上一次请求或旧数据库补回缺失字段。

**客户隔离约定**：不同客户必须使用不同的 `username`，`history` 只能包含该客户的对话。请求内派生数据通过线程上下文隔离，调用结束即释放；不传历史时仅使用以客户ID分开的进程内会话字典，最多20条消息、30分钟无活动过期。接口信任调用方提供的客户ID和无身份标记的历史，不能识别错误归属或冒用ID；多店铺接入时请在ID中包含店铺标识。正式接入请每次传完整、准确、按时间排序的历史。

`/chat` 与 `/customer_chat` 使用不同的内部命名空间，即使公开 `session_id` 恰好等于 `cust_` 加客户ID，也不继承另一接口的会话。`/chat` 新增可选 `history` 字段，语义与客户接口一致；响应字段保持不变。

**进程内去重**：同一客户的同一 `message_id` 在当前进程中保留24小时；并发重复请求返回空 `reply` 和 `send_reply=false`。未传ID时用商品、消息、完整历史的请求指纹作10秒去重。去重仅用进程内字典，不读写文件；重启、切换worker或多实例时不共享记录。调用方必须按客户ID和平台消息ID处理跨实例请求与发送去重。

这是当前进程的重复处理保护，不是平台送达确认。生成失败会释放占位，正在处理的占位最多保留10分钟。首次响应在网络中丢失时，调用方应结合自己的处理与送达记录决定重试；接口无法阻止发送端把同一响应发送两遍。

**行为要点（兜底规则）**
- RAG 判为无意义输入，或生成“没明白您的意思”等兜底时：未留资只回复“请留下耳关系方式☎️，咱们定制师会尽快解答您的问题”，不再追加标准邀请；已有真实留资则简短回复“好的亲~”，不再次索要。`/chat` 此时把完整话术放在 `details[].answer`，`closing` 为空。真实旅行问题仍正常解答。
- 本条消息本身含真实联系方式（手机号/微信号等）→ 回复"已收到您的联系方式"，不再说没听懂。
- 对客文本保留“耳关细”，不会恢复成“联系”；结构化账号字段及客户原文保持原样。
- “嗷嗷嗷／哦哦哦／噢噢噢／奥奥奥／嗯嗯／好的好的／没问题”等确认语（含“澳嗷嗷”、末尾👌／👍等表情）→ 正文“好的亲~”；“好的，谢谢”等致谢语正文回复“不客气”。末尾统一按真实留资状态追加标准邀请，同条消息仍有实际旅行问题时继续回答问题。
- 客户提及二维码、扫一扫、扫码加等文本时，未留资只回复“请勿二维码，麻烦使用vx号或耳关系方式。”，不叠加尾部留资请求；已有真实留资则回复“请勿二维码，您之前提供的耳关系方式已记下。”。“晚点发二维码”等文本不算已留资，收到真实号码或账号才保存；同条提供真实账号时仍保存账号。当前接口接收文本，尚无二维码图片接收、解码或图片推送功能。旧版本误存的“二维码: 客户”占位记录自动清理，不影响真实号码。
- 微信号必须带明确的微信／vx标识，例如“vx: qa_demo88”“VX：qa_demo88”“我的微信号是 qa_demo88”“我的微信号：qa_demo88”。允许同条消息继续提问，如“我的微信号是qa_demo88，酒店多少钱”：登记账号并继续回答酒店问题，不再补问方式。账号须是字母开头的4–20位字母、数字、下划线或短横线；不从裸发的“qa_demo88”、普通英文词、“加我某账号”或“耳关细方式是某账号”中猜测微信号。手机号、座机、QQ、邮箱照常识别，同条提供多种方式时一并保存；微信号中的数字不会被拆成电话或QQ。
- 裸发7～8位数字（如 `7093312`、`70793312`）不视为手机号、座机或QQ，只从该客户历史中记为用途待确认的数字；询问用途时不追加留资邀请。客户随后明确说“这是座机号”或“座机号”，会承接上一串数字并补问区号或所在城市，这一步同样不追加通用邀请；收到有效区号／城市后补全并登记，补全前不算已留资。支持“这是座机号，区号021”同条确认；否定、问句、假设不视为确认。“这是订单号”等不会登记为客咨；明确确认“这是QQ号”才按QQ保存。用途未确认前，单独发城市／区号不会被用于拼接。待确认状态完全由本次历史重建；历史要包含“数字→用途确认→区号／城市”的过程。切换worker时携带完整历史即可；`history=[]` 则不承接之前的数字。完整带区号座机和合法手机号照常识别；不从无区号旧“座机”或客服的登记话术推断真实留资。
- 追问“耳关细／耳关系／耳光？”或承接上句问“啥意思” → 先解释用途，反复追问则换种说法，并允许在当前窗口继续咨询。
- 客户说“你加我吧” → 已留号码时承接已有信息，未留时才询问方式。英文目的地不会从咨询长句中被截取成账号；模型不得把普通咨询回复成已收到号码。
- “我留客咨／客资／留资了，你会加我吗？”等后续对接确认 → 正文“是的亲。”。客户自述不会生成虚假号码或已留资记录；系统没有真实记录时，末尾仍补标准留资邀请。同条消息另有报价、时间或旅行问题时继续正常回答，不只回肯定短句。
- “你有我的客咨／客资吗”“我的客资你们收到了吗”等查询实际留资状态：查到真实记录回复“有的亲。”；没有真实记录时说明未查到并追加标准留资邀请。客户自述“留过了”不会被当作系统已查到；询问具体留资内容时仅回读当前真实记录。
- 支持“有我电话信息吗”“我的电话信息是什么”等“信息／资料／记录”变体；电话、手机、座机、微信、QQ、邮箱按实际类型查询。只存有微信时，不会确认已有电话；回读具体类型时不混入其他类型的账号。
- **每轮统一规则**：只要是客户回复且系统尚无真实号码／账号，每次末尾从下列5种话术选择一条，均使用耳关系和☎️，不输出“联系”。按客户、消息及历史选择，不使用跨客户共享的轮换状态。无意义输入、二维码使用各自的专用回复，整条只发一次，不叠加尾部邀请。不因上一条已经邀请、客户口头表示留过或曾拒绝提供而跳过；有真实留资即不再追加，正文也过滤重复索要。普通问答、客资查询、问候、确认语、身份询问和需求修改均使用同一出口规则。平台通知、重复请求不发送；广告消息不加客户留资邀请。`/chat` 的标准邀请放在 `closing`，`/customer_chat` 已合并到 `reply`，调用方不要再加一次。
  - 方便留一个耳关系方式☎️吗？更方便为您服务哦~
  - 亲，方便留个耳关系方式☎️吗？咱们定制师会跟进您的需求~
  - 您可以留个耳关系方式☎️，方便定制师为您解答哦~
  - 方便提供一个耳关系方式☎️吗？咱们定制师会继续为您服务~
  - 麻烦留下您的耳关系方式☎️，方便后续沟通行程哦~
- `[通知消息]` 是平台通知，返回空回复及 `send_reply=false`。客户只发与当前商品名相同的标题时，先询问咨询需求，不凭标题推断套餐包含的具体服务。
- “免签函／免签涵能否出具”先核实出具条件，不承诺公司已有出函能力；按用户配置，“你是真人么”的正文固定为“亲亲~是的呢，您有什么旅行相关的问题都可以问我。”，不经过模型生成，收尾统一按真实留资状态处理。“你会💌”与“你加我吧”分别或合并提交均能承接已留资状态。

**多轮需求记忆**：首条商品标题（如“大溪地6日5晚…”）、单独的“大溪地”、`product_name` 都能补充目的地；“过年”“春节”等出行时间、“四人”“4位”等总人数也会识别。商品信息不覆盖客户明确修改的目的地。明确说过的目的地、出发月份/日期、几大几小、孩子年龄、天数、预算，以及暂不留资的意愿，从本次历史按时间重建。简短补充或“行程想要定制”会承接已知需求，仅询问缺失信息，不重复问已知目的地。明确变更更新相应字段，未变更字段保留；假设和反问不覆盖已确认需求。号码与需求分别从客户历史识别，拒绝留资不会被当作已提供号码。“加我这个手机号微信”“这个手机号微信”会承接历史中已提供的手机号添加vx的需求，不把这句话当作无意义输入，也不凭此伪造微信账号；没有真实号码仍按规则邀请留资。调用方每轮传入历史以延续需求，超出上述规则的自由表达仍结合最近对话由模型理解。

客户简短陈述已识别需求或提出简单定制请求时，回读已有信息并只询问缺失字段；包含额外问题或偏好时继续回答实际问题，避免扩展为未询问的天气介绍。

**旧调用方兼容说明**

- 原接口路径及 `reply`、`analysis`、`side_effects` 等原有字段保留；`message_id` 可省略。`history` 保留旧版列表接收范围，允许附带时间戳、布尔值和嵌套元数据，业务判断只取 `role` 和 `content`。
- `/chat` 仍返回 `session_id`、`question`、`details`、`closing`；规则回复的 `source` 对外保持 `ai`。忽略通知时仍保留一条空 `answer`，兼容旧端访问 `details[0]`，发送端应跳过空文本。
- `/customer_chat` 的重复请求和平台通知可能返回空 `reply`。旧端仅按 `if data['reply'].strip(): ...` 判断也可跳过，不强制解析新标记。对额外响应字段执行严格校验的客户端，需要允许新增 `send_reply`、`duplicate`、`message_id` 字段。
- `username` 现在拒绝空字符串，以免不同客户混用留资记录。旧端若曾传空值，会收到400，需改传稳定客户标识。
- 部署需一起上传 `conversation_rules.py`、`contact_state.py`、`dialogue_state.py`、`number_state.py`、`message_guard.py` 及修改后的 `main.py`、`api.py`、`im_core.py`；无新增生产依赖。历史信息由每次请求的 `history` 重建，不从旧数据库迁移或恢复；同一客户改传空历史，也会按无历史重新判断。跨worker及跨实例的消息去重由调用方负责。

**示例**
```bash
curl -X POST http://8.163.65.89:8000/customer_chat \
  -H "Content-Type: application/json" \
  -d '{
    "username": "wechat_test_010",
    "product_id": "76121031",
    "product_name": "斐济+大溪地8日7晚私家团",
    "message": "我的微信号是 liushuiqianchang",
    "history": [
      {"role":"user","content":"我的手机17398788268"},
      {"role":"assistant","content":"亲亲~已收到您的联系方式（手机号: 17398788268）"}
    ]
  }'
```
```json
{
  "reply": "亲亲~已收到您的耳关细方式（微信号: liushuiqianchang），旅行管家稍后耳关细您哦~",
  "analysis": {"contacts":{"contacts":["微信号: liushuiqianchang"],"has_real_contact":true}},
  "side_effects": {},
  "send_reply": true,
  "duplicate": false,
  "message_id": ""
}
```

---

## 5. 本机 IM 兼容接口（印尼兄弟试点）

这两个端点供 `D:\workflow\IM Web Auto` 本机调用。服务必须只监听回环地址：

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8010
```

在客服项目 `.env` 中配置 `RAG_CHAT_TOKEN` 长随机值；IM 项目的 `.env` 配置同一个值。`RAG_TENANT_ID` 默认固定为 `indonesia_brother`，请求正文不能指定租户。投递账本默认写入 `data/rag_delivery.db`，可用 `RAG_DELIVERY_DB` 指定路径。不要把该服务直接暴露到公网。

### 5.1 POST `/v1/chat`

请求头必须带 `Authorization: Bearer <RAG_CHAT_TOKEN>`。消息 ID、客户 ID、会话 ID 和渠道均必填；`text` 必须是非空字符串且最多 2000 字。`history` 最多 12 条，每条只传 `role`、`content`，内容最多 1200 字。历史角色会映射为 `user` 或 `assistant`。

```json
{
  "message_id": "ctrip-msg-001",
  "customer_id": "customer-001",
  "conversation_id": "room-001",
  "channel": "ctrip",
  "text": "Can you arrange an airport transfer?",
  "history": [
    {"role": "user", "content": "We are traveling to Bali."},
    {"role": "assistant", "content": "What dates are you planning?"}
  ],
  "occurred_at": "2026-10-24T10:00:00+00:00"
}
```

首次处理返回 `action`、`reply`、`send_reply`、`trace_id` 和 `duplicate=false`。IM 只有在 `send_reply=true` 且回复非空时才发送。生成成功后回复会先保存为待确认状态；相同消息在 ACK 前重试会返回同一条回复，并带 `duplicate=true`、`send_reply=true`，发送端应以 `send_reply` 为准，允许重试发送。正在生成时的并发重复请求不会拿到发送许可。

### 5.2 POST `/v1/delivery/ack`

只有 IM 发送通道确认成功后才调用 ACK。ACK 使用同一组 `message_id`、`customer_id`、`conversation_id`、`channel`，也需要相同 Bearer Token：

```json
{
  "message_id": "ctrip-msg-001",
  "customer_id": "customer-001",
  "conversation_id": "room-001",
  "channel": "ctrip"
}
```

成功返回 `{"status":"acknowledged"}`。ACK 后同 ID 再次请求 `/v1/chat` 会得到 `send_reply=false` 和空 `reply`，不会再次发放发送许可。无对应投递记录返回 `404`；未配置 Token 返回 `503`，缺失或错误 Token 返回 `401`。请求校验错误返回 `422`。

旧 `/customer_chat` 的 Token 行为不随这两个端点改变；两个接口使用独立鉴权，错误响应和服务日志不输出消息正文或 Token。

---

## 6. Python 调用示例

```python
import requests

API_BASE = "http://8.163.65.89:8000"   # 公网地址（本机调试可换 127.0.0.1）

resp = requests.post(f"{API_BASE}/customer_chat", json={
    "username": "wechat_test_010",
    "product_id": "76121031",
    "product_name": "斐济+大溪地8日7晚私家团",
    "message": "斐济有什么玩法？",
    "message_id": "platform-message-0001",  # 来自平台，同一条重试沿用；新消息换新ID
    "history": [],
})
data = resp.json()
if not data.get("send_reply", bool(data.get("reply"))):
    raise SystemExit("重复消息，跳过发送")
print("收到 reply:", data["reply"])          # 最终回复
print("收到 analysis:", data["analysis"])    # 分析结果
```

---

## 7. 常见问题

**Q：公网地址连不上？**
A：确认服务已启动且服务器 `8000` 端口已放行（安全组/防火墙）。调用方填 `http://8.163.65.89:8000`，**不要**填 `0.0.0.0`。

**Q：返回 429？**
A：同一 IP 每分钟请求超过 30 次，请降频或调整 `RATE_LIMIT_PER_MINUTE`。

**Q：改了代码没生效？**
A：停服务 → 删除 `__pycache__` → 重新 `python api.py`，确保用最新代码。

**Q：如何开启 Token 鉴权？**
A：取消 `api.py` 相关注释并设置环境变量 `API_TOKEN`，调用时带 `Authorization: Bearer <token>`。
