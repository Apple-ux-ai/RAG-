"""无需模型的短句规则；只匹配完整话语，避免吞掉同条消息中的旅行问题。"""
import re
import hashlib

IDENTITY_REPLY = "亲亲~是的呢，您有什么旅行相关的问题都可以问我。"
CONTACT_INVITATIONS = (
    "方便留一个耳关系方式☎️吗？更方便为您服务哦~",
    "亲，方便留个耳关系方式☎️吗？咱们定制师会跟进您的需求~",
    "您可以留个耳关系方式☎️，方便定制师为您解答哦~",
    "方便提供一个耳关系方式☎️吗？咱们定制师会继续为您服务~",
    "麻烦留下您的耳关系方式☎️，方便后续沟通行程哦~",
    "方便留个年系方式☎️吗？更方便为您服务哦~",
    "亲，可以留下您的年系方式☎️吗？方便定制师跟进您的需求~",
    "您可以留个年系方式☎️，咱们定制师会为您解答行程问题哦~",
    "麻烦提供一个年系方式☎️，方便后续沟通出行安排~",
    "方便留下您的年系方式☎️吗？便于定制师了解您的旅行需求哦~",
    "亲，方便留个帘系方式☎️吗？更方便为您服务哦~",
    "可以留下您的帘系方式☎️吗？方便定制师为您介绍行程~",
    "麻烦留个帘系方式☎️，咱们定制师会跟进您的出行需求哦~",
    "您可以提供一个帘系方式☎️，方便后续沟通旅行安排~",
    "方便留下帘系方式☎️吗？便于定制师为您解答具体问题哦~",
    "方便留个链系方式☎️吗？更方便为您服务哦~",
    "亲，可以提供您的链系方式☎️吗？方便定制师跟进您的咨询~",
    "麻烦留下链系方式☎️，咱们定制师会为您介绍出行方案哦~",
    "您可以留个链系方式☎️，方便后续沟通行程细节~",
    "方便提供一个链系方式☎️吗？便于定制师了解您的具体需求哦~",
)
CONTACT_INVITATION = CONTACT_INVITATIONS[0]
NONSENSE_CONTACT_INVITATION = "请留下耳关系方式☎️，咱们定制师会尽快解答您的问题"
QR_CONTACT_REPLY = "请勿二维码，麻烦使用vx号或耳关系方式。"
QR_SAVED_REPLY = "请勿二维码，您之前提供的耳关系方式已记下。"
AMBIGUOUS_NUMBER_REPLY = "亲，这串数字的用途还不明确，您发的是商品编号还是其他信息呢？"


def contact_invitation(customer_key, message, history):
    """同一次请求稳定选句，不使用会混到其他客户的全局轮换状态。"""
    key = f"{customer_key}\0{message}\0{len(history or [])}"
    index = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % len(CONTACT_INVITATIONS)
    previous = next((m.get("content", "") for m in reversed(history or [])
                     if m.get("role") == "assistant"), "")
    if CONTACT_INVITATIONS[index] in previous:
        index = (index + 1) % len(CONTACT_INVITATIONS)
    return CONTACT_INVITATIONS[index]


def is_platform_notice(message):
    return (message or "").strip() in {"[通知消息]", "【通知消息】", "通知消息"}


def platform_text(text):
    """只处理对客文本，保留原始消息和结构化账号数据。"""
    return re.sub(r"(?i:lian\s*[细系])|耳关细|联系", "耳关系", text or "")


def _compact(text):
    return re.sub(r"[\s，,。.!！?？~～、；;：:]+", "", text or "").lower()


_CONTACT_SHARED = (r"(?:我|我们)?(?:已经|刚刚|刚才|之前|刚|已)?(?:"
              r"留资(?:过|好|完|了|啦)*|"
              r"(?:留(?:过|好)?|提交|填(?:写)?|发|给)(?:过|好|完)?(?:了)?(?:个|一个|我的)?"
              r"(?:客咨|客资|联系方式|耳关[细系](?:方式)?|号码|电话|手机(?:号)?|微信(?:号)?|资料)"
              r"(?:了|啦|呀|哦|呢)?)")
_CONTACT_ACTION = r"(?:加|添加|联系|耳关[细系])(?:一下|下)?(?:我|我们)(?:一下|下)?"
_CONTACT_QUESTION = (r"(?:那|所以|然后|请问|想问一下)?(?:"
                r"(?:你们?|您|旅行管家|定制师|客服)?"
                r"(?:会不会|会|还会|能不能|能|可以|是不是会|是否会)(?:主动|来|再|直接)?"
                rf"{_CONTACT_ACTION}|(?:会不会|会|是不是会)(?:有)?(?:人|客服|旅行管家|定制师)(?:来|主动)?{_CONTACT_ACTION})"
                r"(?:的)?(?:吗|么|嘛|吧|对吗|是吗|对吧|是不是|呀|啊)?")

def _claims_shared(text):
    claim = re.fullmatch(rf"(?P<shared>{_CONTACT_SHARED})(?:{_CONTACT_QUESTION})?", text)
    return bool(claim and re.search(r"已经|刚刚|刚才|之前|刚|已|过|好|完|了|啦", claim.group("shared")))


def _contact_claimed(history, message=""):
    """客户近期已留资的自述供话术判断使用，不生成真实账号记录。"""
    claimed = False
    items = list(history or [])[-6:] + [{"role": "user", "content": message}]
    for item in items:
        if item.get("role") not in ("user", "guest"):
            continue
        for part in re.split(r"[，,。！？?!；;\n]+", item.get("content", "")):
            part = _compact(part)
            if _claims_shared(part):
                claimed = True
            elif re.fullmatch(r"(?:其实)?(?:我|我们)?(?:其实|现在)?(?:还没|没有|没|尚未|未|不曾)"
                              r"(?:留资|(?:留|提交|填|发).{0,12}(?:客咨|客资|资料|号码|电话|手机|微信|耳关[细系]|联系方式))"
                              r"(?:过|呢|呀|啊)*", part):
                claimed = False
    return claimed


def _contact_followup_reply(message, history, saved_contacts, declined_contact=False):
    """查询留资与确认后续对接；客户自述不等于系统已收到可使用的账号。"""
    msg = _compact(message)
    shared, question = _CONTACT_SHARED, _CONTACT_QUESTION

    if re.fullmatch(shared, msg):
        return ("contact_shared_claim", "好的亲。") if _claims_shared(msg) else None
    record = (r"(?:(?:客咨|客资|留资|联系方式|联系信息|耳关[细系](?:方式|信息)|"
              r"手机(?:号码|号)?|(?:联系|耳关[细系])?电话(?:号码)?|座机(?:号码|号)?|"
              r"微信(?:号)?|vx(?:号)?|qq(?:号码|号)?|邮箱|电子邮箱|号码)(?:的?(?:信息|资料|记录))?|资料)")
    owner = r"(?:我|我们)(?:的|(?:之前|刚才)?留的)?"
    receiver = r"(?:你们?(?:这边|那边)?|您|这边|客服这边)?"
    received = r"(?:有没有收到|有没有查到|有没有保存|有没有记录|是否收到|是否查到|是否保存|是否记录|能不能查到|能查到|有没有|是否有|有记录|有保存|有没|没有|有|还没收到|没收到|收没收到|收到|查到|保存|记录|记不记得|记得)(?:了)?"
    ending = r"(?:了)?(?:吗|么|嘛|没|没有|了没|呢|呀|啊)?"
    record_status = bool(re.fullmatch(
        rf"(?:请问|那|所以|想问一下)?(?:{receiver}(?:现在|目前|已经|还)?{received}{owner}{record}|"
        rf"{owner}{record}{receiver}(?:现在|目前|已经|还)?{received}){ending}", msg))
    record_value = bool(re.fullmatch(
        rf"(?:请问|那)?{owner}{record}(?:是|有)?(?:什么|啥|多少|哪个|哪些)(?:来着)?{ending}", msg))
    matched = re.fullmatch(rf"(?P<shared>{shared})?{question}", msg)
    if not (matched or record_status or record_value):
        return None
    if matched and matched.group("shared") and not _claims_shared(matched.group("shared")):
        return None
    # 只承接最近客户自己的明确陈述，不使用客服话术或否定/假设句证明留资。
    claimed = _contact_claimed(history, message)
    if record_status or record_value:
        contacts = list(saved_contacts or [])
        label = "客资记录"
        # 查询电话不能因存有微信就回答“有”；回读时也仅返回客户所问的类型。
        for pattern, kinds, description in (
                (r"座机", {"座机号"}, "座机信息"),
                (r"手机", {"手机号"}, "手机信息"),
                (r"电话", {"手机号", "座机号"}, "电话信息"),
                (r"微信|vx", {"微信号"}, "微信信息"),
                (r"qq", {"QQ号"}, "QQ信息"),
                (r"邮箱", {"邮箱"}, "邮箱信息"),
                (r"号码", {"手机号", "座机号", "QQ号"}, "号码信息")):
            if re.search(pattern, msg):
                contacts = [contact for contact in contacts
                            if contact.replace("：", ":").split(":", 1)[0].strip() in kinds]
                label = description
                break
        if contacts:
            reply = "您目前留的耳关细方式是：" + "；".join(contacts) + "。" if record_value else "有的亲。"
        elif saved_contacts:
            reply = f"亲，我这边有您之前提供的耳关细方式，但暂时没查到您的{label}。"
        elif claimed:
            reply = f"亲，您之前说已经留过客资，不过我这边暂时没查到具体的{label}，您可以先在这里继续咨询。"
        else:
            reply = f"亲，我这边暂时没查到您的{label}。"
            reply += "您可以先在这里继续咨询。" if declined_contact else CONTACT_INVITATION
        return "contact_record_recall" if record_value else "contact_record_status", reply
    if saved_contacts or claimed:
        return "contact_followup_confirmation", "是的亲。"
    return "contact_followup_confirmation", "是的亲。" + ("" if declined_contact else CONTACT_INVITATION)


def short_reply(message, history=None, saved_contacts=None, declined_contact=False):
    """返回 (意图, 回复)，不符合确定规则时返回 None。"""
    msg = _compact(message)
    history = history or []
    assistants = [m.get("content", "") for m in history
                  if m.get("role") == "assistant"]
    previous = assistants[-1] if assistants else ""
    if re.search(r"二维码|扫一扫|扫码加|扫码进|扫描二维码|识别二维码", message or ""):
        return "contact_qr", QR_SAVED_REPLY if saved_contacts else QR_CONTACT_REPLY
    # 客户要求使用刚才的手机号添加 vx，不是重新提供一个裸微信号。
    phone_wechat = re.fullmatch(
        r"(?:我在国外)?(?:(?:你们?|您)?(?:就|直接|可以|麻烦)?(?:加|添加)(?:我)?(?:一下)?)?"
        r"(?:我(?:的)?|这(?:个)?|刚才(?:那个|的)?|之前(?:那个|的)?)?"
        r"(?:手机(?:号码|号)?|电话(?:号码)?)(?:就是|也是|是|的)?(?:微信(?:号)?|vx)(?:号|吧|哦|呀|就行|可以吗|吗)*", msg)
    if phone_wechat:
        phones = [c for c in saved_contacts or [] if c.startswith("手机号:")]
        if phones:
            return "contact_handoff", "好的亲，已记下使用您之前手机号添加vx的需求，后续由定制师跟进。"
        return "contact_handoff", "好的亲，您想使用手机号添加vx。"
    followup = _contact_followup_reply(message, history, saved_contacts, declined_contact)
    if followup:
        return followup
    if (re.search(r"(?:晚点|等会|等下|稍后|一会|待会|准备|将要).{0,10}(?:发|给|提供).{0,10}(?:二维码|号码|微信|手机|耳关细|联系)", message or "")
            and not re.search(r"多少钱|价格|报价|费用|行程|酒店|签证|预算|怎么玩|怎么办|[?？]", message or "")):
        return "contact_pending", "好的，您方便时再发就行，咱们也可以先在这里继续聊。"
    if (re.search(r"(?:不方便|不想|不愿|不要|不留|不提供).{0,8}(?:号码|微信|耳关细|联系|电话|手机|客资|客咨|留资)|(?:不|暂不)留(?:资|客资|客咨)", message or "")
            and not re.search(r"多少钱|报价|费用|酒店|怎么玩|几天|怎么办", message or "")):
        return "contact_declined", "好的，咱们就在这里聊，您可以直接说想了解的旅行问题。"
    aliases = r"(?:耳关[细系]|耳光|耳关|lian[细系])"
    explicit = bool(re.fullmatch(
        rf"(?:这个|你说的|你说|那个|什么叫)?{aliases}(?:方式)?(?:到底)?"
        r"(?:是啥|是什么|什么意思|啥意思|是啥意思|是什么意思|怎么填|怎么留|吗)?[啊呀呢]*", msg))
    contextual = msg in {"啥意思", "什么意思", "没看懂", "不懂", "没懂", "还是不懂", "能说清楚吗",
                         "那你刚才说的是什么意思", "你刚才说的是什么意思", "那是什么意思", "什么意思呀",
                         "你说的什么意思", "还是没懂", "还是没明白"}
    explaining = any(word in previous for word in
                     ("耳关细", "耳关系", "Lian 细", "lian细", "Lian 系", "lian系", "方便把行程方案发给您", "方便您接收行程方案"))
    if explicit or (contextual and explaining):
        count = sum(any(marker in text for marker in
                        ("这里的“耳关系”", "这里的“耳关细”", "这里的“Lian 细”", "这里的“Lian 系”", "方便把行程方案发给您", "您不用另外提供"))
                    for text in assistants)
        if count == 0:
            name = "耳关系"
            reply = f"这里的“{name}”是指方便您接收行程方案的方式。您也可以直接在这里继续咨询。"
        elif count == 1:
            reply = "就是方便把行程方案发给您的方式，您也可以先在这里聊旅行需求。"
        else:
            reply = "这是方便接收行程方案的方式，您可以先说旅行需求，我会继续为您解答。"
        return "contact_explanation", reply

    # 含实质问题的长句不会命中，如“噢噢噢，那免签函能办吗”。
    # “澳嗷嗷/澳澳澳”也按确认语处理；单独的“澳”保留给目的地判断。
    ack_message = re.sub(r"[👌👍😊🙂😄🤝🙏✅\ufe0f\U0001f3fb-\U0001f3ff]+$", "", msg)
    emoji_ack = not ack_message and bool(re.search(r"[👌👍✅]", msg))
    if ack_message != "澳" and not re.search(r"[?？]", message or "") and (emoji_ack or re.fullmatch(
            r"[嗷噢哦喔嗯恩昂奥澳]+(?:(?:好[的哒呢呀啊]*)+)?", ack_message) or re.fullmatch(
            r"(?:(?:好[的哒呢呀啊]*)+|没问题|知道了|明白了|了解了|收到|行|可以|ok|okay|[哈呵嘿]+)", ack_message)):
        return "acknowledgement", "好的亲~"
    if not re.search(r"[?？]", message or "") and re.fullmatch(
            r"(?:好[的哒呢呀啊]*|知道了|明白了|了解了|收到|嗯+|哦+|噢+|ok|okay)?"
            r"(?:谢谢[你您]*|多谢|感谢|谢啦)+[啦呀啊呢哈]*", ack_message):
        return "thanks", "不客气，有旅行相关的问题随时问我~"

    # 前端5秒防抖可能把“你会💌”和“你加我吧”合并成一条。
    envelope = re.match(r"^你会\s*[💌❤❤️]+[吗么?？，,。\s]*", (message or "").strip())
    add_message = _compact((message or "").strip()[envelope.end():]) if envelope else msg
    add_request = bool(re.fullmatch(
        r"(?:那|好|好的)?(?:你们?|您)(?:来|直接|可以|能不能|能)?"
        r"(?:加|添加)(?:一下|下)?我(?:一下|下)?(?:吧|呀|啊|呗|好吗|可以吗)?", add_message))
    # 截图中的表情短句，只在已经留过号码的语境下解释为对接确认。
    if saved_contacts and envelope and not add_message:
        return "contact_handoff", "您发来的方式已记下，后续由旅行管家跟进。"
    if add_request:
        if saved_contacts:
            noun = "号码" if any(c.startswith(("手机号:", "座机号:", "QQ号:")) for c in saved_contacts) else "方式"
            return "contact_handoff", f"好的，您之前发来的{noun}已记下，后续由旅行管家跟进。您也可以在这里继续补充出行需求。"
        return "contact_handoff", "可以的。" + ("咱们在这里继续聊也可以。" if declined_contact else CONTACT_INVITATION)
    return None


def should_solicit(message, history, has_contact=False, intent="valid", declined_contact=False):
    """每轮客户回复按真实留资记录决定邀请；保留旧参数以兼容调用方。"""
    return not has_contact and intent not in {"platform_notice", "duplicate", "ad", "ad_confirm_online",
                                              "ambiguous_number", "number_clarification"}


def unique_details(details):
    result, seen = [], set()
    for item in details:
        answer = platform_text(item.get("answer", ""))
        key = _compact(re.sub(r"^亲亲[~～，,\s]*", "", answer))
        if key and key not in seen:
            seen.add(key)
            result.append({**item, "answer": answer})
    return result


def clean_reply(text):
    """去掉同次回复里的重复段落；不影响下一轮客户重复提问。"""
    seen, parts = set(), []
    for part in (text or "").splitlines():
        part = platform_text(part.strip())
        key = _compact(part)
        if key and key not in seen:
            seen.add(key)
            parts.append(part)
    return "\n".join(parts)


def suppress_contact_requests(text):
    """已留资时统一移除索取账号的请求，保留旅行回答和已收到信息的确认。"""
    contact = (r"(?:耳关[细系](?:方式)?|(?i:lian\s*[细系])(?:方式)?|联系方式|联系|微[信讯]|[vV][xX信讯]|薇|"
               r"电话(?:号码)?|手机(?:号)?|号码|座机(?:号)?|QQ(?:号)?|邮箱|账号|📞)")
    action = r"(?:留(?:下|个|一个|一下)?|提供|发送|发(?:个|一个|一下)?|给(?:个|一个)?|补充|补全|告知|告诉|同步|确认)"
    parts = []
    for clause in re.split(r"(?<=[，,。！？?!；;\n])", text or ""):
        content = clause.strip()
        request = re.search(rf"(?:方便|请|麻烦|能否|可否|能不能|可以|不妨).{{0,14}}{action}.{{0,18}}{contact}", content)
        imperative = re.match(rf"^(?:亲亲[~～，,]*)?(?:再|您|你|给我|给我们)?{action}.{{0,18}}{contact}", content)
        if not request and re.search(r"已(?:经)?(?:收到|登记|记录|保存|记下)", content):
            imperative = None
        asks_number = re.search(rf"(?:您的?|你的?){contact}.{{0,10}}(?:多少|是什么|是啥).{{0,4}}[?？吗么]", content)
        if request or imperative or asks_number:
            continue
        if re.fullmatch(r"(?:更方便为您服务|我们能更快为您服务)[哦呢呀~～。！!，,]*", content):
            continue
        parts.append(clause)
    return "".join(parts).strip(" ，,；;\n") or "您之前提供的信息已记下，我们可以继续在这里沟通行程需求。"


def guard_unverified_quote(answer, state):
    """客户预算可以回读；没有实时资源报价时，不让模型自报金额或承诺包含项目。"""
    amounts = re.findall(r"\d[\d,.]*(?:\s*[-~～至到]\s*\d[\d,.]*)?\s*(?:万元?|元|美元|美金|欧元|人民币)", answer or "")
    budget = re.sub(r"\s+", "", state.get("budget", ""))
    unverified = any(re.sub(r"\s+", "", amount) != budget for amount in amounts)
    unverified = unverified or bool(re.search(
        r"(?:几千|数千|几万|数万).{0,8}元|(?<![\d.])[一二三四五六七八九十百千万两]+元|(?:几|数)[千万百](?:到|至)", answer or ""))
    unverified = unverified or bool(re.search(r"预算.{0,30}(?:覆盖|足够|宽裕)|包含当地[四五]星", answer or ""))
    if not unverified:
        return answer
    prefix = f"您目前提供的预算是{budget}。" if budget else ""
    return prefix + "酒店及行程报价需要结合出发日期、人数和住宿偏好核实，暂时不能直接给出具体金额或承诺包含项目。您可以在这里补充需求，确认后再为您说明。"
