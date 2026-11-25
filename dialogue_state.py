"""从本次传入的客户历史提取需求，不把客服回复当作客户事实。"""
import re
from contact_state import state_bucket


def extract_facts(message, destinations):
    facts = {}
    names = "|".join(re.escape(name) for name in sorted(set(destinations), key=len, reverse=True))
    for part in re.finditer(r"([^，,。！？?；;\n]+)([，,。！？?；;\n]|$)", message or ""):
        clause, ending = part.groups()
        # 询问和假设不改写需求；“改成X呢”是明确提出的变更。
        if re.search(r"如果|假如|要是|是不是|是否|是什么|多少|几岁|几大|几小|哪个|哪里|还是|能不能|能否|可否", clause):
            continue
        if ending in ("?", "？") and not re.search(r"那(?:就)?改成|确定改成|改为|调整为", clause):
            continue
        if re.search(r"[吗么呢]", clause) and not re.search(r"改成|改去|改为|改到|换成|换去", clause):
            continue
        if names:
            # 商品标题和独立的目的地回答也属于客户上下文，不要求必须写“我想去”。
            bare = re.fullmatch(rf"\s*({names})(?:[呀啊哦呢])?\s*", clause)
            title = re.match(rf"\s*({names}(?:\s*[+、]\s*(?:{names}))*)\s*\d+\s*(?:日|天)", clause)
            if bare:
                facts["destinations"] = [bare.group(1)]
            elif title:
                facts["destinations"] = re.split(r"\s*[+、]\s*", title.group(1))
            pattern = rf"(?:想去|计划去|打算去|改(?:成|去|为)?|换(?:成|去)|目的地(?:是|为)|去)\s*({names})"
            for match in re.finditer(pattern, clause):
                before = clause[max(0, match.start() - 4):match.start()]
                if re.search(r"不|别|不能|不想|暂不", before):
                    continue
                facts["destinations"] = [match.group(1)]
        dates = re.findall(r"(?:\d{4}年)?\d{1,2}月(?:\d{1,2}[日号]|上旬|中旬|下旬|初|底|末)?", clause)
        if dates:
            facts["departure"] = dates[-1]
        holiday = re.fullmatch(r"\s*(?:(?:我们|我)?(?:打算|计划|准备)?(?:在)?)?(春节|过年|寒假|暑假|国庆|五一|元旦)(?:期间)?(?:出发|出行|去|去玩)?\s*", clause)
        if holiday:
            facts["departure"] = holiday.group(1)
        match = re.search(r"(?:预算(?:改成|改为|调整为|是|为|大概|约|控制在|在|不超过)?|改成|改为|调整为)\s*(\d+(?:\.\d+)?\s*(?:万元?|千元?|元))", clause)
        if match:
            facts["budget"] = match.group(1)
        match = re.search(r"([一二两三四五六七八九十\d]+)\s*大\s*([一二两三四五六七八九十\d]+)\s*小", clause)
        if match:
            facts["travellers"] = match.group(1) + "大" + match.group(2) + "小"
        count = re.fullmatch(r"\s*(?:我们|一共|总共|人数(?:是|为)?|共)?\s*([一二两三四五六七八九十\d]+)\s*(?:个人|人|位)\s*", clause)
        if count:
            facts["travellers"] = count.group(1) + "人"
        match = re.search(r"(?:孩子|小孩|小朋友|儿童|宝宝|娃)(?:年龄)?(?:今年|是|有|现在|改成|改为|改到|调整为)?\s*(\d{1,2})\s*岁", clause)
        if match:
            facts["child_age"] = match.group(1) + "岁"
        match = re.search(r"(?:(?:玩|待|旅行|行程)(?:改成|改为|是|为)?|改成|改为|调整为)\s*(\d+)\s*(天|日)", clause)
        if match:
            facts["duration"] = match.group(1) + match.group(2)
    if re.search(r"(?:不方便|不想|不愿|不要|不留|不提供).{0,8}(?:号码|微信|耳关细|联系|电话|手机|客资|客咨|留资)|(?:不|暂不)留(?:资|客资|客咨)", message or ""):
        facts["declined_contact"] = True
    if re.search(r"(?:现在|愿意|可以).{0,6}(?:留|提供).{0,5}(?:号码|耳关细|联系|客资|客咨)|(?:现在|愿意|可以).{0,6}留资|你加我吧", message or ""):
        facts["declined_contact"] = False
    return facts


def update_state(customer_key, history, message, destinations, product_facts=None):
    historical = {}
    for item in history or []:
        if isinstance(item, dict) and item.get("role") in ("user", "guest"):
            historical.update(extract_facts(item.get("content", ""), destinations))
    current = extract_facts(message, destinations)
    bucket = state_bucket(customer_key)
    previous = bucket.get("trip", {})
    state = {**(product_facts or {}), **historical, **previous, **current}
    bucket["trip"] = state
    return state



def describe_state(state):
    labels = {"destinations": "目的地", "departure": "出发时间", "budget": "预算", "travellers": "人数",
              "child_age": "孩子年龄", "duration": "行程天数"}
    parts = []
    for key, label in labels.items():
        if state.get(key):
            value = "、".join(state[key]) if isinstance(state[key], list) else state[key]
            parts.append(label + "是" + value)
    return "，".join(parts)


def recall_reply(message, state):
    # 口语、省略“的/是/问号”及“那/哪”同音输入，统一直接查询当前客户记录。
    compact = re.sub(r"[\s，,。！？?!~～]+", "", message or "")
    if re.fullmatch(
            r"(?:请问|你记得|你还记得|还记得)?(?:我(?:们)?的?)?目的地(?:是|在|为)?"
            r"(?:(?:哪|那)(?:里|儿|个(?:国家|地区)?)?|什么|啥)(?:来着|呀|啊|呢)?", compact):
        if state.get("destinations"):
            return "您之前提到的目的地是" + "、".join(state["destinations"]) + "。"
        return "我这边暂时没有查到您的目的地信息，您想去哪个国家或地区呢？"
    if not re.search(r"刚才|刚刚|之前|我说|我提|我们说|我的|我们.*说", message or ""):
        return None
    if not re.search(r"什么|多少|几岁|几大|几小|多大|哪|几天|吗|么|[?？]", message):
        return None
    requested = {}
    selectors = {"destinations": r"目的地|去哪里|去哪", "departure": r"出发|出行时间|启程|几月",
                 "budget": r"预算", "travellers": r"几大|几小|几个人|人数|几位",
                 "child_age": r"孩子|小孩|小朋友|儿童|宝宝", "duration": r"几天|天数"}
    for key, pattern in selectors.items():
        if re.search(pattern, message) and state.get(key):
            requested[key] = state[key]
    if requested:
        return "您之前提到的" + describe_state(requested) + "。"
    return None


def correction_reply(message, destinations):
    if not re.search(r"改成|改去|改为|改到|调整为|换成|换去", message or ""):
        return None
    current = extract_facts(message, destinations)
    current.pop("declined_contact", None)
    description = describe_state(current)
    if description:
        return "好的，" + description.replace("是", "已改为") + "，其他已说明的需求保持不变。"
    return None


def requirements_reply(message, state, destinations):
    """多项需求陈述先确认信息；问题和定制请求仍交给正常回答流程。"""
    if re.search(r"[?？]|吗|么|呢|如何|怎么|多少|能否|能不能|可否|是不是|是否|如果|假如|要是|帮|推荐|介绍|安排|设计|规划|想问|想了解|麻烦|希望|要求", message or ""):
        return None
    facts = extract_facts(message, destinations)
    facts.pop("declined_contact", None)
    customization = bool(re.fullmatch(r"(?:我|我们)?(?:想|想要|要|需要)?(?:定制(?:一下)?(?:行程|旅行)?|(?:行程|旅行)(?:想|想要|要|需要)?定制)(?:一下)?[。！!\s]*", message or ""))
    if not facts and customization:
        return _requirements_confirmation(state)
    if not facts:
        return None
    # 仅确认明确字段组成的陈述，保留额外偏好、限制和其他诉求给正常回答流程。
    names = "|".join(re.escape(name) for name in sorted(set(destinations), key=len, reverse=True))
    field = (rf"(?:我|我们)?(?:想去|计划去|打算去|目的地(?:是|为)|去)(?:{names})|(?:{names})|"
             r"(?:(?:我们|我)?(?:打算|计划|准备)?(?:在)?)?(?:春节|过年|寒假|暑假|国庆|五一|元旦)(?:期间)?(?:出发|出行|去|去玩)?|"
             r"(?:我们|一共|总共|人数(?:是|为)?|共)?\s*[一二两三四五六七八九十\d]+\s*(?:个人|人|位)|"
             r"(?:\d{4}年)?\d{1,2}月(?:\d{1,2}[日号]|上旬|中旬|下旬|初|底|末)?(?:出发|出行)?|"
             r"[一二两三四五六七八九十\d]+\s*大\s*[一二两三四五六七八九十\d]+\s*小|"
             r"(?:孩子|小孩|小朋友|儿童|宝宝|娃)(?:今年|是|有|现在)?\s*\d{1,2}\s*岁|"
             r"(?:玩|待|旅行|行程)\s*\d+\s*(?:天|日)|"
             r"预算(?:是|为|大概|约|控制在|在|不超过)?\s*\d+(?:\.\d+)?\s*(?:万元?|千元?|元)")
    if any(not re.fullmatch(field, part.strip()) for part in re.split(r"[，,。；;\n]+", message.strip("。！! ")) if part.strip()):
        return None
    return _requirements_confirmation(state)


def _requirements_confirmation(state):
    description = describe_state(state)
    prefix = "好的，已记下您的需求：" + description + "。" if description else "好的亲，可以为您定制行程。"
    missing = next((question for key, question in (
        ("destinations", "您想去哪个目的地呢？"),
        ("departure", "您计划什么时候出行呢？"),
        ("travellers", "这次一共几位出行呢？")) if not state.get(key)),
        "您对住宿类型或特别想体验的项目有什么偏好吗？")
    return prefix + missing
