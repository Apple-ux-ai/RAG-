"""根据客户传入的历史重放数字用途确认，不保存跨请求状态。"""
import re
from conversation_rules import AMBIGUOUS_NUMBER_REPLY


def _replay(history, product_id=""):
    import im_core
    number, kind, contacts = "", "", {}
    for index, item in enumerate(history or []):
        if not isinstance(item, dict) or item.get("role") not in ("user", "guest"):
            continue
        message = item.get("content", "")
        if not isinstance(message, str):
            continue
        detected = im_core.detect_contact_info(message, "history", (history or [])[:index])
        number, kind, result = _step(number, kind, message, detected, product_id)
        found = list(detected.get("contacts") or []) if detected.get("has_real_contact") else []
        if result and result["contacts"].get("has_real_contact"):
            found.extend(result["contacts"]["contacts"])
        for contact in found:
            contacts[contact.split(":", 1)[0].strip()] = contact
    return number, kind, list(contacts.values())


def contacts_from_history(history):
    return _replay(history)[2]


def number_followup(customer_key, message, history, detected, product_id=""):
    # customer_key 保留为兼容参数；状态完全来自本次客户历史，不查其他用户。
    number, kind, _ = _replay(history, product_id)
    return _step(number, kind, message, detected, product_id)[2]


def _step(number, kind, message, detected, product_id):
    import im_core
    text = im_core._normalize_contact_text(message or "").strip()
    compact = re.sub(r"[\s，,。！!？?：:]+", "", text)
    result = None
    if detected.get("has_real_contact"):
        number, kind = "", ""
    elif re.fullmatch(r"\d{7,8}", text) and text != product_id:
        if text == number and kind == "landline":
            result = _ask_area(number)
        else:
            number, kind = text, "unknown"
            result = _reply("ambiguous_number", AMBIGUOUS_NUMBER_REPLY)
    elif detected.get("need_area_code") and detected.get("pending_landline_suffix"):
        number, kind = detected["pending_landline_suffix"], "landline"
        result = _ask_area(number)
    elif number:
        # 必须是客户肯定确认，问句、否定和假设不改变数字类型。
        confirmation = re.fullmatch(
            r"(?:这(?:个)?|那(?:个)?|刚才(?:那个|的)?|上面(?:那个|的)?)?"
            r"(?:是|就是|其实是)(?:我的?|个|一个)?(?:公司|办公)?"
            r"(?P<kind>座机(?:号码|号)?|固定电话(?:号码|号)?|QQ(?:号码|号)?|商品编号|产品编号|订单号)(?:啊|呀|哦)?"
            r"(?:(?:区号(?:是|为)?)?(?P<area>0\d{2,3}))?",
            compact, re.IGNORECASE)
        # 保留原始问号：询问“这是座机号吗”不是确认。
        if re.search(r"[?？]|吗|么|是不是|如果|假如", text):
            confirmation = None
        if re.search(r"(?:不是|并非).{0,5}(?:座机|固定电话)", compact):
            kind = "unknown"
            result = _reply("number_clarification", "好的亲，暂不按座机号登记。这串数字是什么用途呢？")
        elif not confirmation and re.fullmatch(r"座机(?:号码|号)?", compact) and not re.search(r"[?？]", text):
            kind = "landline"
            result = _ask_area(number)
        elif confirmation:
            label = confirmation.group("kind").lower()
            if "座机" in label or "固定电话" in label:
                kind = "landline"
                result = _ask_area(number)
                code = confirmation.group("area")
                if code in im_core.VALID_AREA_CODES:
                    result = _reply("number_registered", "好的亲，已为您补全座机号：" + code + number + "。",
                                    ["座机号: " + code + number])
                    number, kind = "", ""
            elif label.startswith("qq"):
                result = _reply("number_registered", "好的亲，您刚才提供的QQ号已记下。",
                                ["QQ号: " + number])
                number, kind = "", ""
            else:
                result = _reply("number_clarification", "好的亲，这串数字按编号理解，不作为客咨登记。")
                number, kind = "", ""
        elif kind == "landline":
            area = re.fullmatch(r"(?:区号(?:是|为)?)?(0\d{2,3})", compact)
            code = area.group(1) if area and area.group(1) in im_core.VALID_AREA_CODES else None
            if not code:
                code = next((ac for city, ac in im_core.CITY_AREA_CODE_MAP.items()
                             if re.fullmatch(r"(?:我在|在|位于)?" + re.escape(city) + r"市?", compact)), None)
            if code:
                result = _reply("number_registered", "好的亲，已为您补全座机号：" + code + number + "。",
                                ["座机号: " + code + number])
                number, kind = "", ""

    return number, kind, result


def _reply(intent, text, contacts=()):
    return {"intent": intent, "reply": text,
            "contacts": {"contacts": list(contacts), "has_real_contact": bool(contacts)}}


def _ask_area(number):
    result = _reply("number_clarification", "好的亲，刚才的数字按座机号处理。麻烦补充区号（如010、021），或告诉我所在城市，补全后再为您登记。")
    result["contacts"].update(need_area_code=True, pending_landline_suffix=number)
    return result
