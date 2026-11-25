"""只合并本次请求的上下文留资，不读写数据库或客户 JSON 文件。"""
import re
from contextvars import ContextVar
from functools import wraps

_request_state = ContextVar("customer_request_state", default=None)


def request_scope(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if _request_state.get() is not None:
            return func(*args, **kwargs)
        token = _request_state.set({})
        try:
            return func(*args, **kwargs)
        finally:
            _request_state.reset(token)
    return wrapped


def state_bucket(customer_key):
    state = _request_state.get()
    return state.setdefault(customer_key, {}) if state is not None else {}


def saved_contacts(customer_key, historical=(), current=()):
    bucket = state_bucket(customer_key)
    merged = {}
    for contact in [*historical, *bucket.get("contacts", []), *current]:
        if not isinstance(contact, str) or not contact:
            continue
        normalized = contact.replace("：", ":").replace(" ", "")
        if normalized == "二维码:客户" or re.fullmatch(r"(?:座机号|座机):\d{7,8}", normalized):
            continue
        merged[contact.split(":", 1)[0].strip()] = contact
    result = list(merged.values())
    bucket["contacts"] = result
    return result
