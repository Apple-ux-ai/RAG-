import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from main import load_faq, load_banned_words, public_kb_chat, CLOSING_PHRASE, FAQ_FILE, BANNED_WORDS_FILE, client, handle_customer_message
from message_guard import claim_message, finish_message
from rag_delivery import DeliveryRecord, DeliveryScope, DeliveryStore, RAG_SAFE_LOGGING

RAG_CHAT_TOKEN = os.getenv("RAG_CHAT_TOKEN", "").strip()
RAG_TENANT_ID = os.getenv("RAG_TENANT_ID", "indonesia_brother").strip() or "indonesia_brother"
_RAG_DELIVERY_DB = os.getenv("RAG_DELIVERY_DB", "").strip()
_rag_delivery_store = None
_rag_delivery_store_lock = threading.Lock()
_rag_security = HTTPBearer(auto_error=False)
_rag_logger = logging.getLogger("rag_bridge")

def get_delivery_store() -> DeliveryStore:
    """Create the delivery ledger lazily so importing the legacy API has no new disk side effects."""
    global _rag_delivery_store
    if _rag_delivery_store is None:
        with _rag_delivery_store_lock:
            if _rag_delivery_store is None:
                path = (
                    Path(_RAG_DELIVERY_DB)
                    if _RAG_DELIVERY_DB
                    else Path(__file__).resolve().parent / "data" / "rag_delivery.db"
                )
                _rag_delivery_store = DeliveryStore(path)
    return _rag_delivery_store

# ========= Token 鉴权配置 =========
# 调用方需要在请求头里带上: Authorization: Bearer <token>
# 可以通过环境变量 API_TOKEN 自定义，否则使用默认值
# API_TOKEN = os.getenv("API_TOKEN", "STLY1391")  # 已临时注释：关闭 Token 鉴权
# security = HTTPBearer(auto_error=False)         # 已临时注释：关闭 Token 鉴权

# ========= 限流配置 =========
# 每个 IP 每分钟最多请求次数
RATE_LIMIT_PER_MINUTE = 30
# 存储每个 IP 的请求时间戳列表
_request_logs = {}

def cleanup_old_requests():
    """清理超过1分钟的旧请求记录"""
    now = time.time()
    to_delete = []
    for ip, timestamps in _request_logs.items():
        # 只保留最近60秒内的记录
        _request_logs[ip] = [t for t in timestamps if now - t < 60]
        if not _request_logs[ip]:
            to_delete.append(ip)
    for ip in to_delete:
        del _request_logs[ip]

# async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
#     """验证请求头中的 Token"""
#     if not credentials or credentials.credentials != API_TOKEN:
#         raise HTTPException(status_code=401, detail="无效的 Token，请在请求头中携带正确的 Authorization: Bearer <token>")
#     return credentials

def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。

    在阿里云/负载均衡/Nginx 反代后面，request.client.host 拿到的是网关 IP，
    必须读取反向代理透传的 X-Forwarded-For / X-Real-IP 才能正确限流，
    否则要么限流对所有请求失效，要么把整个网关当成同一 IP 限流（一拥而上就全 429）。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # 格式通常为 "真实IP, 代理1, 代理2"，取第一个
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request):
    """简单限流：每个 IP 每分钟最多 RATE_LIMIT_PER_MINUTE 次请求"""
    cleanup_old_requests()
    client_ip = get_client_ip(request)
    now = time.time()

    if client_ip not in _request_logs:
        _request_logs[client_ip] = []

    # 检查该 IP 最近1分钟内的请求次数
    recent_requests = [t for t in _request_logs[client_ip] if now - t < 60]
    if len(recent_requests) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail=f"请求过于频繁，每个 IP 每分钟最多 {RATE_LIMIT_PER_MINUTE} 次请求")

    _request_logs[client_ip].append(now)

# 超时保底（秒）
CHAT_TIMEOUT = 60
executor = ThreadPoolExecutor(max_workers=10)

# ========= 初始化 =========
app = FastAPI(
    title="旅行管家客服API",
    description="基于本地FAQ知识库 + 千问大模型的度假旅行RAG客服接口",
    version="1.0.0"
)

# 允许跨域（方便前端或其他服务调用）
# 安全约束：不允许把 allow_origins 设为 "*" 的同时开启 credentials（浏览器规范无效且过于宽松）。
# 可通过环境变量 ALLOWED_ORIGINS 指定具体前端域名（逗号分隔），如 "https://a.com,https://b.com"；
# 未配置时默认开放所有来源，但关闭 credentials。
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= 路径容错：客户端常因「base URL 末尾带 /」+「path 开头带 /」拼出 //customer_chat 导致 404 =========
# 这里把路径里的连续斜杠归一成单斜杠，使 /customer_chat 与 //customer_chat（乃至 ///...）都能命中同一路由。
@app.middleware("http")
async def _collapse_double_slash(request: Request, call_next):
    _path = request.scope.get("path", "")
    if "//" in _path:
        while "//" in _path:
            _path = _path.replace("//", "/")
        request.scope["path"] = _path
    return await call_next(request)

# 启动时加载FAQ和违禁词
faq_list = load_faq(FAQ_FILE)
banned_words = load_banned_words(BANNED_WORDS_FILE)
print(f"API启动完成，已加载 {len(faq_list)} 条FAQ知识，{len(banned_words)} 条违禁词规则")


# ========= 请求/响应模型 =========
class ChatRequest(BaseModel):
    question: str          # 用户问题
    session_id: str        # 会话ID（必填，调用方自行拟定）
    context: str = ""      # 可选：产品/目的地上下文（用于目的地定制师称呼等，非客户原话）
    history: Optional[list] = None  # 显式传入时作为本轮完整历史；[] 表示无历史

class AnswerDetail(BaseModel):
    sub_question: str   # 子问题
    answer: str         # 对应的答案
    matched_faq: list   # 匹配到的FAQ
    source: str         # 回答来源：faq 或 ai

class ChatResponse(BaseModel):
    session_id: str     # 会话ID，下次请求带上这个ID即可实现上下文
    question: str       # 用户原始问题
    details: list       # 每个子问题及其对应的答案
    closing: str        # 统一收尾语，放在返回末尾

class HealthResponse(BaseModel):
    status: str
    faq_count: int

class AskRequest(BaseModel):
    system_prompt: str      # 系统提示词
    user_message: str       # 用户消息
    temperature: float = 0.7
    max_tokens: int = 500
    model: str = "qwen-turbo"


class _RagIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=256)
    customer_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=256)
    channel: str = Field(min_length=1, max_length=64)

    @field_validator("message_id", "customer_id", "conversation_id", "channel")
    @classmethod
    def require_nonblank_identity(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("identifier cannot be blank")
        return value


class RagHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str = Field(min_length=1, max_length=1200)

    @field_validator("role")
    @classmethod
    def normalize_role(cls, value):
        roles = {
            "user": "user", "guest": "user", "customer": "user", "client": "user",
            "assistant": "assistant", "agent": "assistant", "ai": "assistant",
            "kefu": "assistant", "bot": "assistant",
        }
        role = roles.get(value.strip().lower())
        if role is None:
            raise ValueError("unsupported history role")
        return role

    @field_validator("content")
    @classmethod
    def require_nonblank_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("history content cannot be blank")
        return value


class RagChatRequest(_RagIdentityRequest):
    text: str = Field(min_length=1, max_length=2000)
    history: list[RagHistoryItem] = Field(default_factory=list, max_length=12)
    occurred_at: Optional[str] = Field(default=None, max_length=64)

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("text cannot be blank")
        return value


class RagDeliveryAckRequest(_RagIdentityRequest):
    pass


class RagChatResponse(BaseModel):
    action: str
    reply: str
    send_reply: bool
    trace_id: str
    duplicate: bool

# ========= 接口定义 =========

@app.get("/", response_model=HealthResponse)
async def health():
    """健康检查"""
    return HealthResponse(status="ok", faq_count=len(faq_list))


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit)])
def chat(req: ChatRequest):
    """
    客服对话接口
    - 请求头：Authorization: Bearer <your-token>
    - 请求体：{"question": "用户问题", "session_id": "必填，调用方自行拟定"}
    - 响应体：{"session_id": "...", "question": "...", "details": [...], "closing": "统一收尾语"}
    - 限流：每个 IP 每分钟最多 30 次请求
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    if not req.session_id or not req.session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空，调用方必须自行拟定")
    if len(req.question) > 2000:
        raise HTTPException(status_code=400, detail="问题过长，单次最多 2000 字")

    # 保底话术
    timeout_reply = "抱歉让您久等了，当前回复暂时不可用，请稍后再试一下。"

    try:
        # 在线程池中运行，支持超时控制
        future = executor.submit(
            public_kb_chat, req.question.strip(), faq_list, banned_words, req.session_id, req.context, req.history
        )
        result = future.result(timeout=CHAT_TIMEOUT)
        # 保留旧版 source 枚举；内部 rule 标签不要求旧客户端新增解析分支。
        return {**result, "details": [
            {**detail, "source": "ai"} if detail.get("source") == "rule" else detail
            for detail in result.get("details", [])
        ]}
    except FuturesTimeoutError:
        # 超时（CHAT_TIMEOUT 秒，当前 60 秒）仍无结果，返回保底回复
        print(f"[超时] 会话 {req.session_id[:8]} 处理超过{CHAT_TIMEOUT}秒，触发保底回复")
        return {
            "session_id": req.session_id,
            "question": req.question,
            "details": [{"sub_question": req.question, "answer": timeout_reply, "matched_faq": [], "source": "ai"}],
            "closing": ""
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务异常: {str(e)}")


# ========= 通用问答/抽取接口 =========
@app.post("/ask", dependencies=[Depends(rate_limit)])
def ask(req: AskRequest):
    """
    通用问答/抽取接口（内部转发千问）。
    适用于非客服话术类任务，例如地点识别、信息抽取等。
    入参：{"system_prompt": "...", "user_message": "...", "temperature": 0.7, "max_tokens": 500, "model": "qwen-turbo"}
    出参：{"reply": "..."}
    """
    def _do():
        resp = client.chat.completions.create(
            model=req.model,
            messages=[
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": req.user_message},
            ],
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
        return resp.choices[0].message.content.strip()

    try:
        future = executor.submit(_do)
        reply = future.result(timeout=CHAT_TIMEOUT)
        return {"reply": reply}
    except FuturesTimeoutError:
        raise HTTPException(status_code=504, detail="抽取接口超时")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"抽取接口异常: {str(e)}")


# ========= 客户消息统一处理接口（重构：数据由调用方通过参数传入，不再依赖 OCR/截图） =========
class CustomerChatRequest(BaseModel):
    username: str                       # 客户标识（用户名 / 微信ID 等）
    product_id: str = ""                # 当前咨询商品 ID
    product_name: str = ""              # 当前咨询商品名
    message: str = ""                   # 客户本条消息（分条碎片合并已移至前端防抖，不再需要 fragments）
    history: Optional[list] = None      # 兼容旧版 history 附带时间戳等元数据；业务层只取 role/content
    message_id: str = ""                # 平台本条消息唯一 ID；重试必须使用同一 ID


@app.post("/customer_chat", dependencies=[Depends(rate_limit)])
def customer_chat(req: CustomerChatRequest):
    """
    接收调用方传入的客户消息 + 商品信息 + 对话上下文，使用迁移自 travel_im_auto 的
    业务函数做广告/联系方式/路由分析，并用现有千问 RAG 生成回复。
    返回 {reply, analysis, side_effects}。

    分条碎片合并已移至前端（chat.py 防抖）：用户停止输入超过设定时长后才提交一条完整消息，
    后端不再接收 fragments。单次 message 行为不变。
    """
    message = req.message

    if not req.username.strip():
        raise HTTPException(status_code=400, detail="username 不能为空，需使用客户唯一标识")
    if not message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="消息过长，单次最多 2000 字")
    claim = claim_message(req.username, req.product_id, req.product_name,
                          message, req.history, req.message_id)
    if claim is None:
        return {"reply": "", "analysis": {}, "side_effects": {},
                "send_reply": False, "duplicate": True, "error": "duplicate",
                "message_id": req.message_id}
    try:
        result = handle_customer_message(
            req.username, req.product_id, req.product_name,
            message, req.history
        )
        finish_message(claim, success=True)
        return {**result, "send_reply": bool(result.get("reply")),
                "duplicate": False, "message_id": req.message_id}
    except Exception as e:
        finish_message(claim, success=False)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理异常: {str(e)}")


# ========= IM 兼容回复与送达确认 =========

def _verify_rag_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_rag_security),
):
    expected = RAG_CHAT_TOKEN.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="RAG chat access is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _rag_scope(req: _RagIdentityRequest) -> DeliveryScope:
    return DeliveryScope(
        tenant_id=RAG_TENANT_ID,
        channel=req.channel,
        customer_id=req.customer_id,
        conversation_id=req.conversation_id,
    )


def _rag_user_key(scope: DeliveryScope) -> str:
    identity = json.dumps(
        [scope.tenant_id, scope.channel, scope.customer_id, scope.conversation_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "rag_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]


def _record_response(record: DeliveryRecord) -> dict:
    if record.state == "pending":
        return {
            "action": record.action or "answer",
            "reply": record.reply or "",
            "send_reply": bool((record.reply or "").strip()) and record.action != "ignore",
            "trace_id": record.trace_id or "",
            "duplicate": True,
        }
    if record.state == "acknowledged":
        return {
            "action": record.action or "ignore",
            "reply": "",
            "send_reply": False,
            "trace_id": record.trace_id or "",
            "duplicate": True,
        }
    return {
        "action": "ignore",
        "reply": "",
        "send_reply": False,
        "trace_id": "",
        "duplicate": True,
    }


@app.post("/v1/chat", response_model=RagChatResponse)
def rag_chat(
    req: RagChatRequest,
    _credentials: Optional[HTTPAuthorizationCredentials] = Depends(_verify_rag_token),
):
    scope = _rag_scope(req)
    store = get_delivery_store()
    claim = store.claim(scope, req.message_id)
    if isinstance(claim, DeliveryRecord):
        return _record_response(claim)

    trace_id = uuid.uuid4().hex
    history = [{"role": item.role, "content": item.content} for item in req.history]
    try:
        safe_log_token = RAG_SAFE_LOGGING.set(True)
        try:
            result = handle_customer_message(
                _rag_user_key(scope),
                "",
                "",
                req.text,
                history,
            )
        finally:
            RAG_SAFE_LOGGING.reset(safe_log_token)

        if not isinstance(result, dict):
            raise TypeError("reply handler returned an invalid result")
        reply = result.get("reply")
        reply = reply.strip() if isinstance(reply, str) else ""
        analysis = result.get("analysis")
        intent = analysis.get("intent") if isinstance(analysis, dict) else ""
        send_reply = bool(reply) and intent != "platform_notice"
        action = "answer" if send_reply else "ignore"
        if not send_reply:
            reply = ""
        store.complete(
            claim,
            reply=reply,
            action=action,
            trace_id=trace_id,
            send_reply=send_reply,
        )
        return {
            "action": action,
            "reply": reply,
            "send_reply": send_reply,
            "trace_id": trace_id,
            "duplicate": False,
        }
    except Exception as exc:
        store.release(claim)
        _rag_logger.warning(
            "RAG chat failed trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="RAG chat processing failed") from None


@app.post("/v1/delivery/ack")
def rag_delivery_ack(
    req: RagDeliveryAckRequest,
    _credentials: Optional[HTTPAuthorizationCredentials] = Depends(_verify_rag_token),
):
    if not get_delivery_store().acknowledge(_rag_scope(req), req.message_id):
        raise HTTPException(status_code=404, detail="Delivery record not found")
    return {"status": "acknowledged"}

# ========= 启动入口 =========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
