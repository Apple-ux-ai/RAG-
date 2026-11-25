"""跨进程消息去重（sqlite 落盘）。

改因：原实现用进程内字典，uvicorn --workers 2 时每个 worker 各有一份去重表，
同一 message_id 的两次请求落到不同 worker 会各回一次，形成偶发重复回复。

现在改为同机 sqlite 共享表，claim 用 BEGIN IMMEDIATE 保证原子性：
workers=1/2/4 的去重行为一致；数据库文件可跨重启保留在途去重记录。

配置：环境变量 MESSAGE_GUARD_DB 指定文件，默认 data/message_guard.db。
兜底：sqlite 不可用（磁盘只读等）时自动回退到原进程内字典，并降频打印告警。
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_TTL_PENDING = 600          # 处理中占位：10 分钟内在途不可重复认领
_TTL_DONE_WITH_ID = 86400   # 有平台 message_id：完成记录保留 24 小时
_TTL_DONE_AUTO = 10         # 无 message_id（按内容哈希去重）：完成记录保留 10 秒

_DEFAULT_DB = Path(__file__).resolve().parent / "data" / "message_guard.db"

_init_lock = threading.Lock()
_init_done = False

_memory = {}
_memory_lock = threading.Lock()

_warn_lock = threading.Lock()
_last_warn = 0.0


def _db_file():
    raw = os.getenv("MESSAGE_GUARD_DB", "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _connect():
    path = _db_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：事务由 BEGIN IMMEDIATE / COMMIT 显式控制
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_schema():
    """建表 + 开启 WAL（多进程读写不互斥）；进程内只做一次。"""
    global _init_done
    if _init_done:
        return
    with _init_lock:
        if _init_done:
            return
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS message_claims (
                key TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                expires REAL NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_message_claims_expires "
                         "ON message_claims(expires)")
        finally:
            conn.close()
        _init_done = True


def _warn(exc):
    """同一分钟内只提示一次，避免 sqlite 长期异常时刷屏。"""
    global _last_warn
    with _warn_lock:
        now = time.time()
        if now - _last_warn < 60:
            return
        _last_warn = now
    print(f"[message_guard] sqlite 去重不可用，本次回退进程内去重: "
          f"{type(exc).__name__}: {exc}")


def _memory_claim(key, token, now):
    with _memory_lock:
        for expired in [k for k, v in _memory.items() if v[1] < now]:
            del _memory[expired]
        if key in _memory:
            return None
        _memory[key] = (token, now + _TTL_PENDING)
    return key, token


def _memory_finish(key, token, explicit_id, success):
    with _memory_lock:
        current = _memory.get(key)
        if not current or current[0] != token:
            return
        if success:
            _memory[key] = (token, time.time() + (_TTL_DONE_WITH_ID if explicit_id else _TTL_DONE_AUTO))
        else:
            del _memory[key]


def claim_message(username, product_id, product_name, message, history, message_id=""):
    """认领一条消息：返回 (key, token, explicit_id)，已被认领过返回 None。

    与旧版语义一致：带 message_id 时按 ID 去重（同一 ID 重试必被判重），
    不带时退回「同一客户 + 请求内容哈希」，10 秒窗口内判重。
    """
    payload = json.dumps([product_id, product_name, message, history], ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    identity = "id:" + message_id if message_id else "auto:" + digest
    key = hashlib.sha256(json.dumps([username, identity]).encode("utf-8")).hexdigest()
    now, token = time.time(), uuid.uuid4().hex

    try:
        _ensure_schema()
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM message_claims WHERE expires < ?", (now,))
            if conn.execute("SELECT 1 FROM message_claims WHERE key = ?", (key,)).fetchone():
                conn.execute("COMMIT")
                return None
            conn.execute("INSERT INTO message_claims(key, token, expires) VALUES (?, ?, ?)",
                         (key, token, now + _TTL_PENDING))
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except Exception as exc:
        _warn(exc)
        result = _memory_claim(key, "mem:" + token, now)
        if result is None:
            return None
        return result[0], result[1], bool(message_id)

    return key, token, bool(message_id)


def finish_message(claim, success):
    """结束认领：成功则把占位延长为「已完成」，失败则立即释放。"""
    key, token, explicit_id = claim
    now = time.time()

    if token.startswith("mem:"):
        _memory_finish(key, token, explicit_id, success)
        return

    try:
        _ensure_schema()
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if success:
                conn.execute("UPDATE message_claims SET expires = ? WHERE key = ? AND token = ?",
                             (now + (_TTL_DONE_WITH_ID if explicit_id else _TTL_DONE_AUTO), key, token))
            else:
                conn.execute("DELETE FROM message_claims WHERE key = ? AND token = ?", (key, token))
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except Exception as exc:
        # 落库失败也不影响本轮回复：占位记录会在 _TTL_PENDING 后自然过期。
        _warn(exc)
