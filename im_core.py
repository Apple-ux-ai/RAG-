# -*- coding: utf-8 -*-
"""im_core.py —— 自包含业务逻辑大脑（已从 travel_im_auto.py 内联，不再依赖 travel_im_auto）。

重构说明：
- 原 travel_im_auto.py 是 9500 行的桌面单体（OCR 取词 + 截图 + 点击粘贴 + 业务大脑）。
- 现业务大脑（广告/同行判定、联系方式识别、分组路由、飞书推送、Bitable 读写、会话记忆）
  已完整内联到本文件，im_core 不再 import travel_im_auto，可独立部署/测试。
- 数据通过 API 参数传入（username / product_id / product_name / 对话上下文）。
- 编排助手 detect_ad / detect_contact / route_group / analyze_message / push_and_log 在文件末尾。
"""
import os
import sys
import json
import time
import re
import io
from datetime import datetime
import requests

try:
    from rag_delivery import RAG_SAFE_LOGGING
except ImportError:
    RAG_SAFE_LOGGING = None

# ---- Windows 控制台 GBK 无法打印 emoji/中文，重配置 stdout/stderr 为 utf-8 ----
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- 目录 / 缺失配置补定义 ----
LOG_FOLDER = "log"
os.makedirs(LOG_FOLDER, exist_ok=True)
# ========== 飞书集成（已完全弃用） ==========
# 2026-09-19 改造：不再实际推送飞书群（A/B/C 群），改为在副作用 contact_info 中展示
# "建议推送的群"（基于目的地 / 广告判定的标签）。
# 2026-09-20：飞书 token 与多维表格（Bitable）集成已完全移除——access_token 获取、
# 凭证配置、写库/查库逻辑均不再使用；相关函数保留为不可达的死代码，后续可整体删除。
# 仅保留「通过目的地判断属于哪个群」的逻辑（见 route_group，返回群标签，不再带 chat_id）。

# ====================== 【飞书多维表格配置区（已弃用）】 ======================
# 是否启用飞书多维表格写入——飞书集成已移除，此开关恒为 False，仅作兼容占位。
ENABLE_FEISHU_BITABLE = False

# ========== 【进线IM店写死配置】 ==========
# 是否写死进线IM店（True = 用下面的 HARDCODED_SHOP_NAME 覆盖 OCR 识别结果）
# False = 继续用 OCR 识别的店铺名（原有逻辑）
# 【2026-07-06 修改】改为 False，使用 extract_shop_name() 采集本机用户名（dscl 命令）
HARDCODED_SHOP_NAME_ENABLED = False
# 写死的进线IM店值（原 Bitable "进线IM店" 字段白名单里的值）
# 白名单: {"总店1", "菲菲", "海星", "路境", "印尼", "总店2", "总店3"}
# 保留此配置以便快速切换到写死模式（设为 True + 修改此值）
HARDCODED_SHOP_NAME = "印尼"
# 【多维表格专用应用凭证】—— 飞书集成已完全弃用（2026-09-20），凭证不再使用，置空。
FEISHU_BITABLE_APP_ID = ""      # 已弃用：原 cli_aacda0662b785cbd
FEISHU_BITABLE_APP_SECRET = ""  # 已弃用：原 mXNQiSHmE9Oj...（已从源码移除）
FEISHU_BITABLE_APP_TOKEN = ""   # 已弃用
FEISHU_BITABLE_TABLE_ID = ""    # 已弃用
FEISHU_BITABLE_VIEW_ID = ""     # 已弃用
# 字段映射（已弃用，不再写入飞书多维表格）
FEISHU_BITABLE_FIELD_MAPPING = {}

# ====================== 【GeoNames 地理服务配置】 ======================
# GeoNames 用户名（免费注册：https://www.geonames.org/login）
GEONAMES_USERNAME = "jy_persica"  # GeoNames 用户名
# 【2026-08-17 改造】分群规则反转：A群=亚洲+中东+海岛，B群=欧美非+大洋洲
# 亚洲国家代码列表（A群）
ASIA_COUNTRY_CODES = ['JP', 'KR', 'TH', 'VN', 'ID', 'MY', 'SG', 'PH', 'KH', 'LA',
                      'MM', 'BN', 'TL', 'IN', 'LK', 'NP', 'BT', 'MV', 'CN', 'HK',
                      'TW', 'MO', 'BD', 'PK', 'AF']
# 中东国家代码列表（A群）- 注：埃及(EG)保留在非洲列表归B群
MIDDLE_EAST_COUNTRY_CODES = ['AE', 'TR', 'IR', 'SA', 'IL', 'JO', 'QA', 'KW', 'BH',
                             'OM', 'YE', 'LB', 'SY', 'IQ', 'PS']
# 欧洲国家代码列表（B群）
EUROPE_COUNTRY_CODES = ['AD', 'AL', 'AT', 'BA', 'BE', 'BG', 'BY', 'CH', 'CY', 'CZ',
                        'DE', 'DK', 'EE', 'ES', 'FI', 'FR', 'FO', 'GB', 'GE', 'GR',
                        'HR', 'HU', 'IE', 'IS', 'IT', 'LI', 'LT', 'LU', 'LV', 'MC',
                        'MD', 'ME', 'MK', 'MT', 'NL', 'NO', 'PL', 'PT', 'RO', 'RS',
                        'SE', 'SI', 'SK', 'SM', 'UA', 'VA', 'GL']  # FO=法罗群岛, GL=格陵兰
# 非洲国家代码列表（B群，海岛国家除外——海岛走关键词判定归A群）
AFRICA_COUNTRY_CODES = ['DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CM', 'CV', 'CF', 'TD',
                        'CG', 'CD', 'CI', 'DJ', 'EG', 'GQ', 'ER', 'ET', 'GA',
                        'GM', 'GH', 'GN', 'GW', 'KE', 'LS', 'LR', 'LY',
                        'ML', 'MR', 'NA', 'NE', 'NG', 'RW', 'ST',
                        'SL', 'SO', 'ZA', 'SS', 'SD', 'TG', 'TN',
                        'UG', 'ZM', 'ZW']
# 注：KM(科摩罗)、MU(毛里求斯)、SC(塞舌尔)、MG(马达加斯加) 等非洲海岛国家
#     由 ISLAND_KEYWORDS 关键词优先判定归 A群（在 is_vip_destination 中先判海岛）
# 美洲国家代码列表（B群）
AMERICA_COUNTRY_CODES = ['US', 'CA', 'MX', 'BR', 'AR', 'CL', 'CO', 'PE', 'VE', 'EC',
                         'BO', 'PY', 'UY', 'GY', 'SR', 'CR', 'PA', 'GT', 'HN', 'NI',
                         'SV', 'CU', 'DO', 'JM', 'TT', 'BS', 'BB', 'PR']
# 大洋洲国家代码列表（B群，海岛除外）
OCEANIA_COUNTRY_CODES = ['AU', 'NZ', 'PG']
# 【极速优化】API请求超时时间（秒）- 缩短到5秒
API_TIMEOUT = 5  # 快速失败，超时自动切换到默认回复
# 是否启用API回复（True=使用API，False=使用默认回复）
ENABLE_API_REPLY = True  # 重新启用API，添加详细调试
# 使用的模型
DEEPSEEK_MODEL =  "qwen-turbo"

# 全局计数器
OCR_RUN_COUNT = 0

# ====================== 【二维码识别配置】 ======================
# 是否启用二维码识别（文本层意图识别，用于标记客户发二维码/扫码加好友）
ENABLE_QR_CODE_RECOGNITION = True
# 二维码最小尺寸（像素）
QR_CODE_MIN_SIZE = 30



ISLAND_KEYWORDS = [
    # 亚洲海岛
    '巴厘岛', '普吉岛', '长滩岛', '宿务', '薄荷岛', '甲米', '沙巴', '仙本那',
    '美娜多', '科莫多', '兰卡威', '苏梅岛', '冲绳', '北海道', '济州岛', '热浪岛',
    '停泊岛', '刁曼岛', '民丹岛', '龙目岛', '吉利岛', '丽贝岛', '象岛', '沙美岛',
    '涛岛', '下龙湾', '富国岛',
    # 其他海岛（含非洲/大洋洲/美洲海岛，统一归A群）
    '马尔代夫', '塞班', '帕劳', '关岛', '夏威夷', '大堡礁', '塔希提', '大溪地',
    '塞舌尔', '毛里求斯', '留尼汪', '马达加斯加', '斐济', '瓦努阿图', '汤加',
    '库克群岛', '瑙鲁', '图瓦卢', '基里巴斯', '所罗门', '新喀里多尼亚',
    '波拉波拉', '巴厘'
]

# 亚洲+中东关键词列表（GeoNames失败兜底）
ASIA_KEYWORDS = [
    # 亚洲国家
    '日本', '韩国', '泰国', '越南', '印尼', '印度', '马来西亚', '新加坡', '菲律宾',
    '柬埔寨', '老挝', '缅甸', '文莱', '斯里兰卡', '尼泊尔', '不丹', '东帝汶',
    '孟加拉', '巴基斯坦',
    # 亚洲城市
    '东京', '大阪', '京都', '首尔', '釜山', '曼谷', '清迈', '河内', '胡志明',
    '岘港', '芽庄', '吉隆坡', '马尼拉', '暹粒', '新德里', '孟买', '科伦坡', '加德满都'
]

MIDDLE_EAST_KEYWORDS = [
    '迪拜', '阿联酋', '土耳其', '伊朗', '沙特', '以色列', '约旦', '卡塔尔',
    '科威特', '巴林', '阿曼', '也门', '黎巴嫩', '叙利亚', '伊拉克', '巴勒斯坦',
    '阿布扎比', '伊斯坦布尔', '安卡拉', '德黑兰', '利雅得', '特拉维夫', '多哈'
]

# 欧洲/非洲(非海岛)/美洲/大洋洲关键词（GeoNames失败兜底，命中归B群）
EUROPE_KEYWORDS = [
    '欧洲', '法国', '德国', '意大利', '西班牙', '葡萄牙', '英国', '爱尔兰',
    '荷兰', '比利时', '卢森堡', '瑞士', '奥地利', '希腊', '芬兰', '瑞典',
    '挪威', '丹麦', '冰岛', '波兰', '捷克', '匈牙利', '斯洛伐克', '斯洛文尼亚',
    '克罗地亚', '波黑', '塞尔维亚', '黑山', '阿尔巴尼亚', '马其顿', '保加利亚',
    '罗马尼亚', '乌克兰', '白俄罗斯', '俄罗斯', '爱沙尼亚', '拉脱维亚', '立陶宛',
    '法瑞意', '德法瑞', '德奥捷', '英爱', '北欧', '东欧', '西欧', '南欧',
    '巴黎', '罗马', '伦敦', '柏林', '阿姆斯特丹', '维也纳', '巴塞罗那', '马德里',
    '慕尼黑', '法兰克福', '米兰', '威尼斯', '佛罗伦萨', '雅典', '圣托里尼',
    '布拉格', '布达佩斯', '里斯本', '波尔图', '爱丁堡', '哥本哈根', '斯德哥尔摩'
]

AFRICA_KEYWORDS = [
    '非洲', '埃及', '南非', '肯尼亚', '坦桑尼亚', '摩洛哥', '突尼斯', '阿尔及利亚',
    '埃塞俄比亚', '尼日利亚', '乌干达', '卢旺达', '赞比亚', '津巴布韦',
    '博茨瓦纳', '纳米比亚', '马拉维', '莫桑比克', '安哥拉', '刚果', '苏丹',
    '利比亚', '索马里', '吉布提', '厄立特里亚', '坦桑', '北非', '东非', '西非', '中非',
    '开罗', '开普敦', '内罗毕', '拉巴特', '卡萨布兰卡', '马拉喀什', '桑给巴尔'
]

AMERICA_OCEANIA_KEYWORDS = [
    '美国', '加拿大', '墨西哥', '巴西', '阿根廷', '智利', '秘鲁', '哥伦比亚',
    '委内瑞拉', '厄瓜多尔', '玻利维亚', '巴拉圭', '乌拉圭', '圭亚那', '苏里南',
    '哥斯达黎加', '巴拿马', '危地马拉', '洪都拉斯', '尼加拉瓜', '萨尔瓦多',
    '古巴', '多米尼加', '牙买加', '特立尼达', '巴哈马', '巴巴多斯', '波多黎各',
    '澳大利亚', '新西兰', '澳洲', '悉尼', '墨尔本', '布里斯班', '黄金海岸',
    '奥克兰', '皇后镇', '纽约', '洛杉矶', '旧金山', '拉斯维加斯', '夏威夷',
    '华盛顿', '芝加哥', '波士顿', '西雅图', '迈阿密', '多伦多', '温哥华'
]

VALID_AREA_CODES = ['010', '020', '021', '022', '023', '024', '025', '027', '028', '029',
                    '0371', '0372', '0373', '0374', '0375', '0376', '0377', '0379',
                    '0411', '0412', '0413', '0414', '0415', '0416', '0417', '0418',
                    '0419', '0431', '0432', '0433', '0434', '0435', '0436', '0437',
                    '0438', '0439', '0451', '0452', '0453', '0454', '0455', '0456',
                    '0457', '0458', '0459', '0471', '0472', '0473', '0474', '0475',
                    '0476', '0477', '0478', '0479', '0482', '0483', '0510', '0511',
                    '0512', '0513', '0514', '0515', '0516', '0517', '0518', '0519',
                    '0523', '0527', '0531', '0532', '0533', '0534', '0535', '0536',
                    '0537', '0538', '0539', '0543', '0546', '0551', '0552', '0553',
                    '0554', '0555', '0556', '0557', '0558', '0559', '0561', '0562',
                    '0563', '0564', '0566', '0570', '0571', '0572', '0573', '0574',
                    '0575', '0576', '0577', '0578', '0579', '0570', '0580', '0580',
                    '0591', '0592', '0593', '0594', '0595', '0596', '0597', '0598',
                    '0599', '0631', '0632', '0633', '0634', '0635', '0660', '0662',
                    '0663', '0668', '0691', '0692', '0731', '0734', '0735', '0736',
                    '0737', '0738', '0739', '0743', '0744', '0745', '0746', '0750',
                    '0751', '0752', '0753', '0754', '0755', '0756', '0757', '0758',
                    '0759', '0760', '0762', '0763', '0766', '0768', '0769', '0770',
                    '0771', '0772', '0773', '0774', '0775', '0776', '0777', '0778',
                    '0779', '0770', '0780', '0781', '0782', '0783', '0784', '0785',
                    '0786', '0787', '0788', '0789', '0812', '0813', '0816', '0817',
                    '0818', '0825', '0826', '0833', '0835', '0836', '0837', '0838',
                    '0851', '0852', '0853', '0854', '0855', '0856', '0857', '0858',
                    '0859', '0870', '0871', '0872', '0873', '0874', '0875', '0876',
                    '0877', '0878', '0879', '0880', '0883', '0886', '0887', '0888',
                    '0891', '0892', '0893', '0894', '0895', '0896', '0897', '0898',
                    '0899', '0911', '0912', '0913', '0914', '0915', '0916', '0917',
                    '0918', '0919', '0931', '0932', '0933', '0934', '0935', '0936',
                    '0937', '0938', '0939', '0941', '0943', '0944', '0945', '0946',
                    '0947', '0948', '0949', '0951', '0952', '0953', '0954', '0955',
                    '0956', '0957', '0958', '0959', '0971', '0972', '0973', '0974',
                    '0975', '0976', '0977', '0979', '0981', '0982', '0983', '0984',
                    '0985', '0986', '0987', '0988', '0989', '0990', '0991', '0992',
                    '0993', '0994', '0995', '0996', '0997', '0998', '0999',
                    # —— 以下为补全的缺失区号（原表未覆盖）——
                    '0310', '0311', '0312', '0313', '0314', '0315', '0316', '0317', '0318', '0319',
                    '0335', '0349', '0350', '0352', '0353', '0354', '0355', '0356', '0357', '0358', '0359',
                    '0370', '0391', '0392', '0393', '0394', '0395', '0396', '0398',
                    '0410', '0421', '0427', '0429',
                    '0464', '0467', '0468', '0469',
                    '0530', '0550', '0701',
                    '0710', '0711', '0712', '0713', '0714', '0715', '0716', '0717', '0718', '0719',
                    '0722', '0724', '0730', '0732', '0733',
                    '0827', '0830', '0831', '0832', '0834', '0839',
                    '0901', '0902', '0903', '0906', '0908', '0909']

# 城市名 -> 区号（用于客户说城市而非区号的情况）。覆盖全国直辖市、地级市及地区/盟/自治州。
# 注：部分城市共享区号（如郑州/开封同为 0371、西安/咸阳同为 029、成都/资阳同为 028），
# 子串匹配（if city in msg）时以本表「首个命中」为准，故同号城市保留其一即可。
CITY_AREA_CODE_MAP = {
    # 直辖市
    '北京': '010', '天津': '022', '上海': '021', '重庆': '023',
    # 河北
    '石家庄': '0311', '唐山': '0315', '秦皇岛': '0335', '邯郸': '0310', '邢台': '0319',
    '保定': '0312', '张家口': '0313', '承德': '0314', '沧州': '0317', '廊坊': '0316', '衡水': '0318',
    # 山西
    '太原': '0351', '大同': '0352', '阳泉': '0353', '长治': '0355', '晋城': '0356',
    '朔州': '0349', '晋中': '0354', '运城': '0359', '忻州': '0350', '临汾': '0357', '吕梁': '0358',
    # 内蒙古
    '呼和浩特': '0471', '包头': '0472', '乌海': '0473', '赤峰': '0476', '通辽': '0475',
    '鄂尔多斯': '0477', '呼伦贝尔': '0470', '巴彦淖尔': '0478', '乌兰察布': '0474',
    '兴安盟': '0482', '锡林郭勒盟': '0479', '阿拉善盟': '0483',
    # 辽宁
    '沈阳': '024', '大连': '0411', '鞍山': '0412', '抚顺': '0413', '本溪': '0414',
    '丹东': '0415', '锦州': '0416', '营口': '0417', '阜新': '0418', '辽阳': '0419',
    '盘锦': '0427', '铁岭': '0410', '朝阳': '0421', '葫芦岛': '0429',
    # 吉林
    '长春': '0431', '吉林': '0432', '四平': '0434', '辽源': '0437', '通化': '0435',
    '白山': '0439', '松原': '0438', '白城': '0436', '延边': '0433',
    # 黑龙江
    '哈尔滨': '0451', '齐齐哈尔': '0452', '鸡西': '0467', '鹤岗': '0468', '双鸭山': '0469',
    '大庆': '0459', '伊春': '0458', '佳木斯': '0454', '七台河': '0464', '牡丹江': '0453',
    '黑河': '0456', '绥化': '0455', '大兴安岭': '0457',
    # 江苏
    '南京': '025', '无锡': '0510', '徐州': '0516', '常州': '0519', '苏州': '0512',
    '南通': '0513', '连云港': '0518', '淮安': '0517', '盐城': '0515', '扬州': '0514',
    '镇江': '0511', '泰州': '0523', '宿迁': '0527',
    # 浙江
    '杭州': '0571', '宁波': '0574', '温州': '0577', '嘉兴': '0573', '湖州': '0572',
    '绍兴': '0575', '金华': '0579', '衢州': '0570', '舟山': '0580', '台州': '0576', '丽水': '0578',
    # 安徽
    '合肥': '0551', '芜湖': '0553', '蚌埠': '0552', '淮南': '0554', '马鞍山': '0555',
    '淮北': '0561', '铜陵': '0562', '安庆': '0556', '黄山': '0559', '滁州': '0550',
    '阜阳': '0558', '宿州': '0557', '六安': '0564', '亳州': '0558', '池州': '0566', '宣城': '0563',
    # 福建
    '福州': '0591', '厦门': '0592', '莆田': '0594', '三明': '0598', '泉州': '0595',
    '漳州': '0596', '南平': '0599', '龙岩': '0597', '宁德': '0593',
    # 江西
    '南昌': '0791', '景德镇': '0798', '萍乡': '0799', '九江': '0792', '新余': '0790',
    '鹰潭': '0701', '赣州': '0797', '吉安': '0796', '宜春': '0795', '抚州': '0794', '上饶': '0793',
    # 山东
    '济南': '0531', '青岛': '0532', '淄博': '0533', '枣庄': '0632', '东营': '0546',
    '烟台': '0535', '潍坊': '0536', '济宁': '0537', '泰安': '0538', '威海': '0631',
    '日照': '0633', '莱芜': '0634', '临沂': '0539', '德州': '0534', '聊城': '0635',
    '滨州': '0543', '菏泽': '0530',
    # 河南
    '郑州': '0371', '开封': '0371', '洛阳': '0379', '平顶山': '0375', '安阳': '0372',
    '鹤壁': '0392', '新乡': '0373', '焦作': '0391', '濮阳': '0393', '许昌': '0374',
    '漯河': '0395', '三门峡': '0398', '南阳': '0377', '商丘': '0370', '信阳': '0376',
    '周口': '0394', '驻马店': '0396', '济源': '0391',
    # 湖北
    '武汉': '027', '黄石': '0714', '十堰': '0719', '宜昌': '0717', '襄阳': '0710',
    '鄂州': '0711', '荆门': '0724', '孝感': '0712', '荆州': '0716', '黄冈': '0713',
    '咸宁': '0715', '随州': '0722', '恩施': '0718',
    # 湖南
    '长沙': '0731', '株洲': '0733', '湘潭': '0732', '衡阳': '0734', '邵阳': '0739',
    '岳阳': '0730', '常德': '0736', '张家界': '0744', '益阳': '0737', '郴州': '0735',
    '永州': '0746', '怀化': '0745', '娄底': '0738',
    # 广东
    '广州': '020', '韶关': '0751', '深圳': '0755', '珠海': '0756', '汕头': '0754',
    '佛山': '0757', '江门': '0750', '湛江': '0759', '茂名': '0668', '肇庆': '0758',
    '惠州': '0752', '梅州': '0753', '汕尾': '0660', '河源': '0762', '阳江': '0662',
    '清远': '0763', '东莞': '0769', '中山': '0760', '潮州': '0768', '揭阳': '0663', '云浮': '0766',
    # 广西
    '南宁': '0771', '柳州': '0772', '桂林': '0773', '梧州': '0774', '北海': '0779',
    '防城港': '0770', '钦州': '0777', '贵港': '0775', '玉林': '0775', '百色': '0776',
    '贺州': '0774', '河池': '0778', '来宾': '0772', '崇左': '0771',
    # 海南
    '海口': '0898', '三亚': '0899', '三沙': '0898', '儋州': '0890',
    # 四川
    '成都': '028', '自贡': '0813', '攀枝花': '0812', '泸州': '0830', '德阳': '0838',
    '绵阳': '0816', '广元': '0839', '遂宁': '0825', '内江': '0832', '乐山': '0833',
    '南充': '0817', '眉山': '0833', '宜宾': '0831', '广安': '0826', '达州': '0818',
    '雅安': '0835', '巴中': '0827', '资阳': '028', '阿坝': '0837', '甘孜': '0836', '凉山': '0834',
    # 贵州
    '贵阳': '0851', '六盘水': '0858', '遵义': '0851', '安顺': '0853', '毕节': '0857',
    '铜仁': '0856', '黔西南': '0859', '黔东南': '0855', '黔南': '0854',
    # 云南
    '昆明': '0871', '曲靖': '0874', '玉溪': '0877', '保山': '0875', '昭通': '0870',
    '丽江': '0888', '普洱': '0879', '临沧': '0883', '楚雄': '0878', '红河': '0873',
    '文山': '0876', '西双版纳': '0691', '大理': '0872', '德宏': '0692', '怒江': '0886', '迪庆': '0887',
    # 西藏
    '拉萨': '0891', '日喀则': '0892', '山南': '0893', '林芝': '0894', '昌都': '0895',
    '那曲': '0896', '阿里': '0897',
    # 陕西
    '西安': '029', '铜川': '0919', '宝鸡': '0917', '咸阳': '029', '渭南': '0913',
    '延安': '0911', '汉中': '0916', '榆林': '0912', '安康': '0915', '商洛': '0914',
    # 甘肃
    '兰州': '0931', '嘉峪关': '0937', '金昌': '0935', '白银': '0943', '天水': '0938',
    '武威': '0935', '张掖': '0936', '平凉': '0933', '酒泉': '0937', '庆阳': '0934',
    '定西': '0932', '陇南': '0939', '临夏': '0930', '甘南': '0941',
    # 青海
    '西宁': '0971', '海东': '0972', '海北': '0970', '黄南': '0973', '海南': '0974',
    '果洛': '0975', '玉树': '0976', '海西': '0977',
    # 宁夏
    '银川': '0951', '石嘴山': '0952', '吴忠': '0953', '固原': '0954', '中卫': '0955',
    # 新疆
    '乌鲁木齐': '0991', '克拉玛依': '0990', '吐鲁番': '0995', '哈密': '0902', '昌吉': '0994',
    '博尔塔拉': '0909', '巴音郭楞': '0996', '阿克苏': '0997', '克孜勒苏': '0908', '喀什': '0998',
    '和田': '0903', '伊犁': '0999', '塔城': '0901', '阿勒泰': '0906', '石河子': '0993',
}


def log(msg):
    if RAG_SAFE_LOGGING is not None and RAG_SAFE_LOGGING.get():
        return
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{time_str}] {msg}"

    # 直接输出到终端（使用 print 确保正确编码）
    print(log_msg)

    # 写入日志
    log_file = os.path.join(LOG_FOLDER, now.strftime("%Y-%m-%d.log"))
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")



def try_resolve_landline_area(message, suffix, valid_area_codes, city_map):
    """客户被索要区号后，解析其回复（三种情况）：
    1) 发区号（如 010 / 0755）  2) 说城市（如 北京/深圳）  3) 发完整区号+号码（如 010-66549322）
    返回补全后的完整座机号字符串；无法解析返回 None。
    """
    import re
    msg = (message or '').strip()
    # 情况3：完整区号+号码（含分隔符）
    m = re.search(r'0\d{2,3}[-\s]?\d{7,8}', msg)
    if m:
        full = re.sub(r'[^\d]', '', m.group())
        ac4, ac3, ac2 = full[:4], full[:3], full[:2]
        if ac4 in valid_area_codes or ac3 in valid_area_codes or ac2 in valid_area_codes:
            return full
        if 10 <= len(full) <= 12 and full.startswith('0'):
            return full
    # 情况1：仅区号（3-4位，0开头），允许区号后紧跟其它文字（如 "0755请问价格"）
    am = re.match(r'^(0\d{2,3})(\D|$)', msg)
    if am:
        ac = am.group(1)
        if ac in valid_area_codes or ac[:3] in valid_area_codes or ac[:2] in valid_area_codes:
            full = ac + suffix
            if 10 <= len(full) <= 12:
                return full
    # 情况2：说城市名
    for city, ac in city_map.items():
        if city in msg:
            full = ac + suffix
            if 10 <= len(full) <= 12:
                return full
    return None


# ===== 无状态辅助：从历史上下文推导拼号 / 待补座机状态（不再依赖服务端内存） =====
PRODUCT_CONSULT_KEYWORDS = [
    '产品咨询通知', '我来自', '产品详情页', '来源渠道',
    '产品ID：', '产品ID:', '产品名称：', '产品名称:',
    '出发城市：', '出发城市:', '产品标签：', '产品标签:',
    '订单号：', '订单号:',
]


def _is_product_consult(text):
    """是否为「产品咨询通知」类消息（含产品ID/订单号等数字，易污染拼号）。"""
    return any(kw in (text or "") for kw in PRODUCT_CONSULT_KEYWORDS)


_LANDMARK_NO_AREA_KW = r'(?:电话|座机|办公电话|办公室电话|公司电话|单位电话|分机)'


def _extract_landline_no_area_suffix(text):
    """仅从明确标记电话/座机的消息提取7-8位后缀，裸数字不猜用途。"""
    if not text:
        return None
    text = _normalize_contact_text(text)
    # 订单号/单号/预约号/交易号后的数字不当作座机后缀（如「我的订单号是 76121031」）
    if re.search(r'(?:订单|单号|预约|交易|order)\s*[:：]?\s*\d{4,}', text, re.IGNORECASE):
        return None
    m = re.search(_LANDMARK_NO_AREA_KW + r'[^\d]{0,5}(\d{7,8})(?!\d)', text)
    if m:
        return m.group(1)
    return None


def _reconstruct_accumulated_digits(history, guest_id, current_digits):
    """无状态重建「跨消息累积的数字」：从 history 末尾向前回溯连续的数字碎片消息。

    等价于原 guest_digit_cache：仅串连「最近、连续、未被完整手机号/产品咨询打断」的
    客人数字碎片（客服消息跳过），避免与历史会话中的无关数字误拼。回溯最多 6 条碎片。
    """
    if not history:
        return ""
    frags = []
    for _m in reversed(history[-12:]):
        if isinstance(_m, dict):
            _c = str(_m.get("content", ""))
            _role = _m.get("role")
        else:
            _c = str(_m)
            _role = None
        # 客服（agent）消息跳过：分段发号时中间常有 bot 回复，不应打断拼号连续性
        if _role and _role not in ("guest", "user"):
            continue
        if _is_product_consult(_c):
            break
        # 只拼接真正的发号碎片，日期、人数、年龄和预算里的数字不是号码。
        fragment = _normalize_contact_text(_c).strip()
        fragment = re.sub(r'^(?:我的?|这是)?(?:手机号?|电话(?:号码)?|号码)(?:是|为)?\s*[:：]?\s*', '', fragment)
        if not re.fullmatch(r'[\d\s-]+', fragment):
            break
        _d = ''.join(re.findall(r'\d+', fragment))
        if not _d:
            break
        # 若某条历史消息本身已是完整手机号，说明上一轮拼号已结束，不再继续往前并
        if re.search(r'1[3-9]\d{9}', _d):
            break
        frags.append(_d)
        if len(frags) >= 6:
            break
    return ''.join(reversed(frags))


def find_pending_landline_suffix(history, guest_id, limit=8, product_id_digits=None):
    """无状态推导「待补区号的座机后缀」：从 history 找到最近一条无区号座机消息。

    返回后缀字符串；若该座机已被补全过（history 中存在客服补全确认/带区号座机）则返回 None，
    避免补全后再次触发索要区号。
    product_id_digits：可选，传入当前咨询商品ID的数字部分，用于排除「产品ID」这类
    纯数字被误判为待补座机后缀（产品ID常为 6~8 位，与无区号座机形态几乎一致，见 76121031 案例）。
    """
    if not history:
        return None
    for _m in reversed(history[-limit:]):
        if isinstance(_m, dict):
            _c = str(_m.get("content", ""))
            _role = _m.get("role")
        else:
            _c = str(_m)
            _role = None
        if _role and _role not in ("guest", "user"):
            # 客服消息：若已补全过（含"补全座机号"或带区号座机），则 pending 已满足
            if "补全座机号" in _c or re.search(r'0\d{2,3}[-\s]?\d{7,8}', _c):
                return None
            continue
        suf = _extract_landline_no_area_suffix(_c)
        if suf:
            # 排除产品ID：其数字部分与当前咨询商品ID一致，不是座机号
            if product_id_digits and re.sub(r"\D", "", str(suf)) == product_id_digits:
                continue
            return suf
    return None


def _normalize_contact_text(text):
    """联系方式识别前的归一化：全角数字→半角、全角空格/破折号→半角、
    并去除数字之间的空格（兼容 '010 88886666' / '138 0013 8000' 这类带空格号码）。"""
    if not text:
        return text
    # 全角数字 → 半角（手机用户常切到全角输入）
    text = text.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    # 全角空格、全角破折号 → 半角
    text = text.replace('　', ' ').replace('－', '-').replace('—', '-')
    # 去除区号括号（如 (010)88886666 / （010）88886666），让座机号成为连续数字流
    text = text.replace('（', '').replace('）', '').replace('(', '').replace(')', '')
    # 去除数字之间的普通空格（仅限"数字 数字"形态，避免误并无关数字）
    text = re.sub(r'(?<=\d) (?=\d)', '', text)
    return text


_WECHAT_ACCOUNT_LABEL = re.compile(
    r'(?<![a-zA-Z0-9_])(?P<label>(?:我(?:的)?\s*)?(?:微信(?:号)?|vx(?:号)?)\s*'
    r'(?:(?:是|为)\s*[:：]?|[:：])\s*)'
    r'(?P<account>[a-zA-Z][a-zA-Z0-9_-]*)(?![a-zA-Z0-9_@-])(?!\.[a-zA-Z0-9])',
    re.IGNORECASE)


def _extract_labeled_wechat(message):
    """只认微信/vx的明确账号标识，允许后面继续咨询；裸账号不推断。"""
    message = message or ""
    accounts = []

    def replace(match):
        account = match.group('account')
        prefix = re.split(r'[，,。！？?!；;\n]', message[:match.start()])[-1]
        hypothetical = re.search(r'如果|假如|假设|例如|比如|示例|不是|(?:你|您|他|她|朋友|客户|对方)(?:们)?的', prefix)
        asking = re.match(r'\s*(?:吗|么|嘛|[?？])', message[match.end():])
        if 4 <= len(account) <= 20 and not hypothetical and not asking:
            accounts.append(account)
        # 不把账号内数字截取为电话/QQ；其余问题、电话及邮箱保留给后续检测。
        return match.group('label') + '[微信账号]'

    remaining = _WECHAT_ACCOUNT_LABEL.sub(replace, message)
    return list(dict.fromkeys(accounts)), remaining


def detect_contact_info(message, guest_id="", history=None):
    """检测消息中是否包含联系方式（手机号、微信号等，支持分散发送和跨消息累积）

    返回值：
    - found_contacts: 找到的联系方式列表
    - has_real_contact: 是否找到完整联系方式
    - need_more_digits: 是否需要更多数字（用于提示客人补充）
    - partial_digits: 当前累积的数字（用于判断是否需要询问）
    """
    import re
    import time

    found_contacts = []
    has_real_contact = False
    need_more_digits = False
    partial_digits = ""
    need_area_code = False
    pending_landline_suffix = ""
    is_pending = False
    is_qr_code = False
    contact_type = None

    # ========== 【2026-07-16 新增】产品咨询通知过滤 ==========
    # 当客人发"产品咨询通知"时，整段文本中包含产品ID、订单号、日期等数字
    # 这些数字若被拼入手机号会误判；无状态化后，_reconstruct_accumulated_digits
    # 在回溯时会主动跳过产品咨询消息，故此处不再清理服务端缓存。
    is_product_consult = _is_product_consult(message)
    if is_product_consult:
        log(f"⏭️ 检测到产品咨询通知，跳过联系方式检测: '{message[:60]}...'")
        return {
            'has_real_contact': False,
            'contacts': [],
            'is_pending': False,
            'is_qr_code': False,
            'contact_type': None
        }

    # 明确标识后的微信号可与咨询同条发送，不能提前返回而漏掉其他联系方式。
    wechat_accounts, message = _extract_labeled_wechat(message)
    if wechat_accounts:
        found_contacts.extend(f'微信号: {account}' for account in wechat_accounts)
        has_real_contact = True
        contact_type = '微信号'

    # 未明确提供的字母账号不猜类型，也不能将其中数字截取成电话。
    if not wechat_accounts and re.fullmatch(r'\s*[a-zA-Z][a-zA-Z0-9_-]*\s*', message or ''):
        return {'contacts': [], 'has_real_contact': False, 'is_pending': False,
                'is_qr_code': False, 'contact_type': None,
                'need_more_digits': False, 'partial_digits': '',
                'need_area_code': False, 'pending_landline_suffix': ''}

    # ========== 归一化（全角→半角、去除号码内空格、剔除分机号）==========
    # 这些预处理只动数字/空格，不影响中文关键词判断
    message = _normalize_contact_text(message)
    # 剔除"分机 123 / 转 123"这类分机号数字，避免其拼入数字流后把主号码误判为
    # "更长数字串的一部分"而被过滤（如 '010 88886666 分机 123'）
    message = re.sub(r'(?:分机|转分机|转)\s*\d+', ' ', message)

    # ========== 座机区号补全流程（多轮） ==========
    # 注：待补区号状态已改为「基于 history 无状态推导」——由 main.py 分支② 调用
    # im_core.find_pending_landline_suffix 从 history 取出最近的无区号座机后缀，
    # 再调 try_resolve_landline_area 补全；此处不再维护服务端 pending 缓存。


    # ========== 【增强】时间戳过滤（防止误识别为QQ号） ==========
    def is_timestamp_like(text):
        """判断字符串是否像时间戳格式"""
        if not text or not text.isdigit():
            return False

        # 6位数字可能是时间戳 HHMMSS
        if len(text) == 6:
            hour = int(text[:2])
            minute = int(text[2:4])
            second = int(text[4:6])
            if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                return True

        # 8位数字可能是日期 YYYYMMDD
        if len(text) == 8:
            year = int(text[:4])
            month = int(text[4:6])
            day = int(text[6:8])
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return True

        # 12位数字可能是完整时间戳 YYYYMMDDHHMM
        if len(text) == 12:
            year = int(text[:4])
            month = int(text[4:6])
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return True

        # 14位数字可能是完整时间戳 YYYYMMDDHHMMSS
        if len(text) == 14:
            year = int(text[:4])
            month = int(text[4:6])
            hour = int(text[8:10])
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 0 <= hour <= 23:
                return True

        return False

    # ========== 【新增】身份证和银行卡过滤 ==========
    def is_id_or_bank_card(text):
        """判断字符串是否像身份证号或银行卡号"""
        if not text or not text.isdigit():
            return False

        # 身份证号：18位，前17位数字，最后一位可能是X或数字
        if len(text) == 18:
            return True

        # 银行卡号：16-19位数字
        if 16 <= len(text) <= 19:
            return True

        # 超过15位的数字串，可能是各种ID
        if len(text) > 15:
            return True

        return False

    # 先检查是否是"将要发送联系方式"的表达，这些不算真正的联系方式
    pending_phrases = [
        '留个电话', '给你电话', '发你电话', '给你联系方式',
        '留联系方式', '发联系方式', '给个电话', '发个电话',
        '留个薇', '留个V讯', '加个V讯', '加个薇',
        '留微信', '加微信', '给微信', '发微信',
        '我电话是', '我的电话', '手机号是', '我的手机',
        'vx号是', '微信号是', 'v信号是',
        '方便留个', '能不能留个', '给我留个', '可以留个',
        '请留个', '留下你的', '留一下你的', '发一下你的',
    ]

    # 如果只是表达"将要发送"的意愿，不算真正发送了联系方式
    is_pending = any(phrase in message for phrase in pending_phrases) and not wechat_accounts

    # 【新增】严格模式：只有消息中包含明确的联系方式关键词才进行检测
    # 避免误识别正常聊天中的数字
    contact_keywords = [
        '微信', 'vx', 'V讯', '薇', 'WeChat', 'wechat',
        '电话', '手机', '手机号', '联系电话', '电话号码',
        'QQ', 'qq', 'QQ号', 'qq号',
        '座机', '办公电话', '公司电话', '单位电话',
        '二维码', '扫码', '加我', '添加我',
        '联系', '耳关细', '耳关系', '号码', '拨打', 'call',
        '邮箱', 'email', 'Email', 'EMAIL',
    ]

    # 如果消息中没有任何联系方式关键词，且没有纯数字内容，跳过检测
    has_contact_keyword = any(keyword in message for keyword in contact_keywords)
    is_pure_digits = re.match(r'^\d+$', message.strip())

    # 【新增】检查是否包含明显的手机号格式（即使没有关键词也应该检测）
    has_phone_pattern = bool(re.search(r'1[3-9]\d{9}', message))

    # 【新增】检查是否包含连续数字片段（至少3位连续数字，用于累积识别分散发送的手机号）
    has_consecutive_digits = bool(re.search(r'\d{3,}', message))

    # 【新增】邮箱检测：@ 是强信号，即使无关键词也应检测
    email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    has_email = bool(re.search(email_pattern, message))

    # 这里只能识别文本意图，不能据此认定已经收到可使用的二维码/账号。
    if ENABLE_QR_CODE_RECOGNITION:
        qr_intent_keywords = ['二维码', '扫一扫', '扫二维码', '扫码加', '扫码进',
                              '扫描二维码', '识别二维码', '加我二维码']
        if any(k in message for k in qr_intent_keywords):
            is_qr_code = True
            is_pending = True
            contact_type = '二维码'

    # 没有其他联系方式信号时直接跳过；不再把独立英文串当作微信号。
    if not has_real_contact and not has_contact_keyword and not is_pure_digits and not has_phone_pattern and not has_consecutive_digits and not is_pending and not has_email:
        log(f"⏭️ 消息中无联系方式关键词且非纯数字，跳过检测: '{message}'")
        return {
            'has_real_contact': False,
            'contacts': [],
            'is_pending': False,
            'is_qr_code': False,
            'contact_type': None
        }

    # 1. 提取当前消息中的所有数字
    current_digits = ''.join(re.findall(r'\d+', message))
    log(f"🔢 当前消息提取数字: '{current_digits}'")

    # 2. 无状态重建「跨消息累积数字」：从 history 回溯连续数字碎片（不再依赖服务端缓存）
    # 注意：只有当前消息包含数字时才参与拼号
    cached_digits = ""
    if current_digits and guest_id and not re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)', message):
        cached_digits = _reconstruct_accumulated_digits(history, guest_id, current_digits)
        if cached_digits:
            log(f"🔢 从 history 重建累积数字: '{cached_digits}'")
            # 【增强】智能判断是否需要合并（沿用原缓存合并规则）
            current_len = len(current_digits)
            cached_len = len(cached_digits)
            starts_with_1 = current_digits.startswith('1')
            # 【修复】当前消息本身是无区号座机的完整候选(7-8位纯数字且不以1开头)时，
            # 不与历史数字碎片合并，避免把两条独立座机号拼成长串后被"更长数字串"过滤掉
            is_standalone_landline = message.strip().isdigit() and (not current_digits.startswith('1')) and 7 <= current_len <= 8
            completes_mobile = bool(re.fullmatch(r'1[3-9]\d{9}', cached_digits + current_digits))
            should_merge = completes_mobile or (not is_standalone_landline) and (
                (current_len <= 4 and cached_len > 0) or
                (cached_len >= 7 and current_len >= 2) or
                (starts_with_1 and current_len >= 3) or
                (cached_len > 0 and current_len >= 7)
            )
            if should_merge:
                current_digits = cached_digits + current_digits
                log(f"🔢 累积后数字: '{current_digits}'")
                log(f"🔢 合并原因: 当前长度={current_len}, 缓存长度={cached_len}, 是否以1开头={starts_with_1}")
            else:
                log(f"🔢 当前数字长度={current_len}，视为普通数字回复，不合并")

    # 3. 无状态化：不再更新服务端数字缓存（累积值改由 history 实时重建）
    if guest_id and current_digits:
        log(f"🔢 当前数字: '{current_digits[-30:]}'（拼号状态由 history 推导，不落内存）")
    elif guest_id and not current_digits:
        log(f"🔢 当前消息无数字")

    partial_digits = current_digits

    # 4. 从累积的数字中查找手机号
    phone_pattern = r'1[3-9]\d{9}'
    phone_matches = re.findall(phone_pattern, current_digits)
    log(f"🔢 从累积数字中查找手机号，找到 {len(phone_matches)} 个匹配: {phone_matches}")

    # 【新增】过滤掉是更长数字串一部分的手机号匹配
    filtered_matches = []
    for match in phone_matches:
        # 检查这个匹配在原始数字串中的位置
        idx = current_digits.find(match)
        if idx == -1:
            continue

        # 检查匹配前后是否有更多数字（说明是更长数字串的一部分）
        has_digit_before = idx > 0 and current_digits[idx-1].isdigit()
        has_digit_after = idx + len(match) < len(current_digits) and current_digits[idx + len(match)].isdigit()

        # 【修正】只有当前后都有数字时才过滤，允许手机号后面跟着微信号中的数字
        # 例如："13812345678abc123" 应该能识别出手机号 13812345678
        if has_digit_before and has_digit_after:
            log(f"⏭️ 过滤部分匹配（是更长数字串的一部分）: {match}")
            continue

        filtered_matches.append(match)

    phone_matches = filtered_matches

    for match in phone_matches:
        # 验证手机号格式：确保前3位是有效的网络号段
        prefix = match[:3]
        valid_prefixes = [
            '13', '14', '15', '16', '17', '18', '19',  # 常见号段
        ]
        is_valid_prefix = any(prefix.startswith(p) for p in valid_prefixes)

        if not is_valid_prefix:
            log(f"⏭️ 无效的手机号前缀: {match} (前缀: {prefix})")
            continue

        # 【新增】过滤身份证和银行卡号
        if is_id_or_bank_card(match):
            log(f"⏭️ 过滤身份证/银行卡号（非手机号）: {match}")
            continue

        # 【新增】如果消息中包含身份证/银行卡关键词，过滤所有手机号匹配
        id_bank_keywords = ['身份证', '身份证号', '银行卡', '银行卡号', '信用卡', '借记卡']
        if any(keyword in message for keyword in id_bank_keywords):
            # 如果消息中包含身份证/银行卡关键词，过滤所有手机号匹配（避免误识别）
            log(f"⏭️ 消息包含身份证/银行卡关键词，过滤手机号匹配: {match}")
            continue

        found_contacts.append(f"手机号: {match}")
        has_real_contact = True
        log(f"✅ 识别到手机号: {match}")

        # 找到完整手机号后，无需清空服务端缓存（无状态化：下一轮拼号将由 history 自动重建，
        # 完整手机号消息会作为「上一轮已结束」的边界，不会继续往前并）

    # ========== 座机号码检测 ==========
    # 座机格式：
    # - 无区号：12345678 或 1234-5678 或 1234 5678
    # - 带区号：020-12345678 或 021 12345678 或 0755-12345678

    # 从原始消息中检测带区号的座机号（如 020-12345678）
    landline_with_area_code_pattern = r'0\d{2,3}[-.\s]?\d{3,4}[-.\s]?\d{4}'
    landline_matches = re.findall(landline_with_area_code_pattern, message)

    # 从原始消息中检测无区号的座机号（如 87654321）
    # 匹配：电话/座机/办公等关键词后的7-8位数字
    landline_no_area_code_pattern = _LANDMARK_NO_AREA_KW + r'[^\d]{0,5}(\d{7,8})(?!\d)'
    landline_no_area_matches = re.findall(landline_no_area_code_pattern, message)
    landline_matches.extend(landline_no_area_matches)

    # 【新增】检测带分隔符的无区号座机（如 876-54321）
    landline_with_sep_pattern = _LANDMARK_NO_AREA_KW + r'[^\d]{0,5}(\d{3,4}-\d{4,5})(?!\d)'
    landline_sep_matches = re.findall(landline_with_sep_pattern, message)
    landline_matches.extend(landline_sep_matches)

    # 也从提取的数字中检测（用于纯数字消息）
    combined_numbers = current_digits
    landline_digit_patterns = [
        r'[^1]\d{7}(?:\d)?',  # 8位数字（前面不是1）
        r'\d{3,4}[-\s]\d{4}(?:\d)?',  # 3-4位区号+分隔符+4位号码
    ]
    for pattern in landline_digit_patterns:
        matches = re.findall(pattern, combined_numbers)
        landline_matches.extend(matches)

    # 去重处理
    processed_landlines = set()
    _has_landline_kw = bool(re.search(_LANDMARK_NO_AREA_KW, message))
    for match in landline_matches:
        # 清理后检查长度
        clean_match = re.sub(r'[^\d]', '', match)
        if 7 <= len(clean_match) <= 8 and not _has_landline_kw:
            # 长度只能说明是数字串，不能证明这是座机，也不能据此等待补区号。
            continue

        # 避免重复处理相同的号码
        if clean_match in processed_landlines:
            continue
        processed_landlines.add(clean_match)

        # 排除时间戳格式
        if is_timestamp_like(clean_match):
            log(f"⏭️ 过滤时间戳格式（非座机号）: {clean_match}")
            continue

        # 【新增】过滤身份证和银行卡号
        if is_id_or_bank_card(clean_match):
            log(f"⏭️ 过滤身份证/银行卡号（非座机号）: {clean_match}")
            continue

        # 【修复】过滤掉「同一数字段内」更长数字串一部分的座机号匹配。
        # 只要该号码在「原始消息」里前后任意一侧紧邻数字（即属于更长的连续数字串，
        # 如 QQ 号 123456789 被切成 23456789），就视为子串跳过，避免误判为待补座机
        # 而挤掉真正的 QQ / 微信等联系方式。不再要求「前后都是数字」。
        _lm = re.search(re.escape(clean_match), message)
        if _lm and (
            (_lm.start() > 0 and message[_lm.start() - 1].isdigit())
            or (_lm.end() < len(message) and message[_lm.end()].isdigit())
        ):
            log(f"⏭️ 过滤部分匹配（是更长数字串的一部分）: {clean_match}")
            continue

        # 【新增】检查原始消息中该数字前面是否有大写字母（订单号格式）
        import re
        # 查找数字在原始消息中的位置
        digit_pattern = re.escape(clean_match)
        matches = list(re.finditer(r'[A-Z]*' + digit_pattern, message))
        for m in matches:
            # 如果数字前面有大写字母，则认为是订单号的一部分
            prefix = m.group()[:-len(clean_match)]
            if prefix and prefix.isupper():
                log(f"⏭️ 过滤订单号格式（前面有大写字母）: {clean_match}")
                continue

        # 座机号长度判断：
        # - 无区号：7-8位
        # - 带区号：10-12位（区号2-4位 + 号码7-8位）
        is_valid_landline = False

        # 有效的中国区号列表
        valid_area_codes = ['010', '020', '021', '022', '023', '024', '025', '027', '028', '029',
                           '0371', '0372', '0373', '0374', '0375', '0376', '0377', '0379',
                           '0411', '0412', '0413', '0414', '0415', '0416', '0417', '0418',
                           '0419', '0431', '0432', '0433', '0434', '0435', '0436', '0437',
                           '0438', '0439', '0451', '0452', '0453', '0454', '0455', '0456',
                           '0457', '0458', '0459', '0471', '0472', '0473', '0474', '0475',
                           '0476', '0477', '0478', '0479', '0482', '0483', '0510', '0511',
                           '0512', '0513', '0514', '0515', '0516', '0517', '0518', '0519',
                           '0523', '0527', '0531', '0532', '0533', '0534', '0535', '0536',
                           '0537', '0538', '0539', '0543', '0546', '0551', '0552', '0553',
                           '0554', '0555', '0556', '0557', '0558', '0559', '0561', '0562',
                           '0563', '0564', '0566', '0570', '0571', '0572', '0573', '0574',
                           '0575', '0576', '0577', '0578', '0579', '0570', '0580', '0580',
                           '0591', '0592', '0593', '0594', '0595', '0596', '0597', '0598',
                           '0599', '0631', '0632', '0633', '0634', '0635', '0660', '0662',
                           '0663', '0668', '0691', '0692', '0731', '0734', '0735', '0736',
                           '0737', '0738', '0739', '0743', '0744', '0745', '0746', '0750',
                           '0751', '0752', '0753', '0754', '0755', '0756', '0757', '0758',
                           '0759', '0760', '0762', '0763', '0766', '0768', '0769', '0770',
                           '0771', '0772', '0773', '0774', '0775', '0776', '0777', '0778',
                           '0779', '0770', '0780', '0781', '0782', '0783', '0784', '0785',
                           '0786', '0787', '0788', '0789', '0812', '0813', '0816', '0817',
                           '0818', '0825', '0826', '0833', '0835', '0836', '0837', '0838',
                           '0851', '0852', '0853', '0854', '0855', '0856', '0857', '0858',
                           '0859', '0870', '0871', '0872', '0873', '0874', '0875', '0876',
                           '0877', '0878', '0879', '0880', '0883', '0886', '0887', '0888',
                           '0891', '0892', '0893', '0894', '0895', '0896', '0897', '0898',
                           '0899', '0911', '0912', '0913', '0914', '0915', '0916', '0917',
                           '0918', '0919', '0931', '0932', '0933', '0934', '0935', '0936',
                           '0937', '0938', '0939', '0941', '0943', '0944', '0945', '0946',
                           '0947', '0948', '0949', '0951', '0952', '0953', '0954', '0955',
                           '0956', '0957', '0958', '0959', '0971', '0972', '0973', '0974',
                           '0975', '0976', '0977', '0979', '0981', '0982', '0983', '0984',
                           '0985', '0986', '0987', '0988', '0989', '0990', '0991', '0992',
                           '0993', '0994', '0995', '0996', '0997', '0998', '0999']

        if 7 <= len(clean_match) <= 8 and (not clean_match.startswith('1') or _has_landline_kw):
            # 无区号座机号（含关键词的 7-8 位号，即使以 1 开头也判为座机，
            # 如「座机12345678」；裸数字串仍要求不以 1 开头，避免误伤手机号/产品ID碎片）
            is_valid_landline = True
        elif 10 <= len(clean_match) <= 12 and clean_match.startswith('0'):
            # 带区号座机号（以0开头）- 需要验证区号是否有效（兼容3位/4位区号）
            area_code4 = clean_match[:4]
            area_code3 = clean_match[:3]
            area_code2 = clean_match[:2]
            if area_code4 in valid_area_codes or area_code3 in valid_area_codes or area_code2 in valid_area_codes:
                is_valid_landline = True
            else:
                log(f"⏭️ 过滤无效区号（非座机号）: {clean_match}")
                continue

        if is_valid_landline:
            # 排除产品ID / 订单号（通常是8位纯数字，且不在正常对话语境中）
            # 如果消息中包含产品/订单相关关键词，则不识别为座机号
            if ('产品' in message or '编号' in message or 'ID' in message
                    or '订单' in message or '单号' in message or '预约' in message
                    or '交易' in message or 'order' in message.lower()):
                log(f"⏭️ 过滤产品ID（非座机号）: {clean_match}")
                continue
            # 如果消息就是纯数字（没有其他内容）
            if message.strip().isdigit():
                # 允许7-8位座机号（纯数字消息）
                if 7 <= len(clean_match) <= 8:
                    log(f"✅ 纯数字消息中的座机号(无区号): {clean_match}")
                elif len(message.strip()) <= 4:
                    log(f"⚠️ 短数字消息，可能是分段联系方式，不过滤: {message}")
                else:
                    # 10-12 位且以 0 开头：已是有效的带区号座机，绝不当作产品ID过滤
                    if 10 <= len(clean_match) <= 12 and clean_match.startswith('0'):
                        pass
                    else:
                        log(f"⏭️ 过滤纯数字（可能是产品ID）: {clean_match}")
                        continue
            # 【2026-09-18 修改】无区号的7-8位本地号码无法直接拨打，
            # 需向客户索要区号后补全，而不是当成完整座机号登记
            if 7 <= len(clean_match) <= 8 and (not clean_match.startswith('1') or _has_landline_kw):
                # 若同一消息已识别出「完整带区号座机」（如"座机010-8888-6666"会被
                # 无区号正则误抓出 0108888 这一片段），则不再把它强加为待补区号，
                # 否则完整座机号会被"缺区号"分支覆盖、导致已收到的座机被错误索要区号。
                if not need_area_code and not any(
                        c.startswith("座机号:") for c in found_contacts):
                    need_area_code = True
                    pending_landline_suffix = clean_match
                    log(f"🔧 识别到无区号座机后缀(需补区号): {clean_match}，将向客户索要区号")
                continue
            # 带区号的座机号：正常登记
            found_contacts.append(f"座机号: {clean_match}")
            has_real_contact = True
            log(f"🔍 识别到座机号：{clean_match}")

    # ========== 标准格式手机号检测 ==========
    # 【新增】如果消息中包含身份证/银行卡关键词，跳过标准格式检测
    id_bank_keywords = ['身份证', '身份证号', '银行卡', '银行卡号', '信用卡', '借记卡']
    should_skip_standard_phone = any(keyword in message for keyword in id_bank_keywords)

    if not should_skip_standard_phone:
        phone_patterns = [
            r'1[3-9]\d{2}\s*\d{4}\s*\d{4}',  # 带空格的手机号 138 1234 5678
            r'1[3-9]\d{2}[-.]\d{4}[-.]\d{4}',  # 带分隔符的手机号 138-1234-5678
            r'86\s*1[3-9]\d{9}',  # 带国家码的手机号
            r'\+?86\s*1[3-9]\d{9}',  # 带+86的手机号
        ]
        _valid_prefixes = ('13', '14', '15', '16', '17', '18', '19')
        for pattern in phone_patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                clean_match = re.sub(r'[\s.\-+]', '', match)
                # 仅接受合法长度：纯 11 位手机号，或带 +86/86 前缀的 13 位（去前缀后 11 位）。
                # 不再对超长匹配盲取末 11 位，避免两个手机号黏连时跨号切出伪号（如 38001380001）。
                if len(clean_match) == 11:
                    candidate = clean_match
                elif len(clean_match) == 13 and clean_match.startswith('86'):
                    candidate = clean_match[2:]
                else:
                    continue
                # 号段前缀校验（与累积路径一致），过滤跨号黏连/异常号段
                if candidate[:3] not in _valid_prefixes:
                    log(f"⏭️ 过滤异常号段手机号: {candidate}")
                    continue
                found_contacts.append(f"手机号: {candidate}")
                has_real_contact = True
                log(f"🔍 识别到标准格式手机号：{candidate}")

    # ========== 邮箱检测 ==========
    for em in re.findall(email_pattern, message):
        found_contacts.append(f"邮箱: {em}")
        has_real_contact = True
        log(f"🔍 识别到邮箱：{em}")


    # ========== QQ号检测（5-11位数字，允许数字内空格） ==========
    qq_patterns = [
        r'QQ\s*[:：]?\s*(\d[\d ]{4,10})',
        r'qq\s*[:：]?\s*(\d[\d ]{4,10})',
        r'Q\s*[:：]?\s*(\d[\d ]{4,10})',
        r'我的QQ是\s*(\d[\d ]{4,10})',
        r'我QQ是\s*(\d[\d ]{4,10})',
        r'QQ号[是:]?\s*(\d[\d ]{4,10})',
        r'我的qq是\s*(\d[\d ]{4,10})',  # 新增：小写qq
        r'我qq是\s*(\d[\d ]{4,10})',    # 新增：小写qq
        r'qq号[是:]?\s*(\d[\d ]{4,10})', # 新增：小写qq号
    ]
    for pattern in qq_patterns:
        matches = re.findall(pattern, message)
        for qq_number in matches:
            qq_number = re.sub(r'\s+', '', qq_number)  # 去除数字内空格（如 9 876 543）
            # 排除时间戳格式
            if is_timestamp_like(qq_number):
                log(f"⏭️ 过滤时间戳格式（非QQ号）: {qq_number}")
                continue
            # 【新增】过滤掉是更长数字串一部分的QQ号
            all_digits_in_msg = ''.join(re.findall(r'\d+', message))
            if len(all_digits_in_msg) > len(qq_number):
                idx = all_digits_in_msg.find(qq_number)
                if idx != -1:
                    has_digit_before = idx > 0
                    has_digit_after = idx + len(qq_number) < len(all_digits_in_msg)
                    if has_digit_before and has_digit_after:
                        log(f"⏭️ 过滤部分匹配（是更长数字串的一部分）: {qq_number}")
                        continue
            found_contacts.append(f"QQ号: {qq_number}")
            has_real_contact = True
            log(f"🔍 识别到QQ号：{qq_number}")

    # ========== 去重 ==========
    unique_contacts = list(dict.fromkeys(found_contacts))

    # 只有当真正检测到联系方式（手机号、微信号等）时才返回
    # 如果只是表达"将要发送"的意愿（如"我留个电话吧"），不返回联系方式
    if is_pending and not has_real_contact:
        log(f"⚠️ 检测到'将要发送联系方式'的表达，但未发现真实联系方式: '{message}'")
        return {'contacts': [], 'has_real_contact': False, 'is_pending': True,
                'is_qr_code': is_qr_code, 'contact_type': contact_type,
                'need_more_digits': False, 'partial_digits': ""}

    return {
        'contacts': unique_contacts,
        'has_real_contact': has_real_contact,
        'is_pending': is_pending,
        'is_qr_code': is_qr_code,
        'contact_type': contact_type,
        'need_more_digits': need_more_digits,
        'partial_digits': partial_digits,
        'need_area_code': need_area_code,
        'pending_landline_suffix': pending_landline_suffix
    }



def is_vip_destination(destination):
    """
    【2026-08-17 改造】判断目的地是否属于 A群（亚洲+中东+海岛+其他未分类）
    判定优先级：海岛关键词 > 国家码(亚洲/中东→A / 欧美非大洋洲→B) > 兜底关键词 > 默认A群

    :param destination: 目的地字符串
    :return: True 归A群（亚洲/中东/海岛/其他未分类），False 归B群（欧洲/非洲/美洲/大洋洲）
    """
    if not destination:
        return False

    # 提取目的地的第一个城市或国家
    # 例如："巴厘岛+科莫多6日5晚私家团" -> "巴厘岛"
    import re
    first_destination = destination

    # 按"+"分割，取第一个部分
    if '+' in destination:
        first_destination = destination.split('+')[0].strip()

    # 从第一个部分中提取城市名称（去掉天数等信息）
    city_match = re.search(r'([\u4e00-\u9fff]{2,})', first_destination)
    if city_match:
        first_destination = city_match.group(1).strip()

    log(f"📍 提取第一个目的地用于分群判断: '{destination}' -> '{first_destination}'")

    # ========== 第0步：海岛关键词优先判定（最高优先级，归A群）==========
    destination_lower = destination.lower()
    first_lower = first_destination.lower()
    for kw in ISLAND_KEYWORDS:
        if kw in destination or kw in first_destination:
            log(f"🏝️ 海岛关键词命中 '{kw}'，归A群")
            return True

    # ========== 第一步：GeoNames 地理识别（带重试机制）==========
    max_retries = 3
    retry_delay = 1  # 重试间隔（秒）

    for attempt in range(max_retries):
        try:
            # 使用 GeoNames API 查询目的地所属国家
            geonames_url = f"http://api.geonames.org/searchJSON?q={first_destination}&maxRows=1&username={GEONAMES_USERNAME}"
            response = requests.get(geonames_url, timeout=10)

            if response.status_code == 200:
                result = response.json()
                if "geonames" in result and len(result["geonames"]) > 0:
                    country_code = result["geonames"][0].get("countryCode", "")
                    country_name = result["geonames"][0].get("countryName", "")
                    admin_name = result["geonames"][0].get("adminName1", "")
                    log(f"📍 GeoNames识别目的地 '{first_destination}' -> 国家: {country_name} ({country_code}), 地区: {admin_name}")

                    # 亚洲 → A群
                    if country_code in ASIA_COUNTRY_CODES:
                        log(f"✅ 目的地 '{first_destination}' 属于亚洲，归A群")
                        return True
                    # 中东 → A群
                    if country_code in MIDDLE_EAST_COUNTRY_CODES:
                        log(f"✅ 目的地 '{first_destination}' 属于中东，归A群")
                        return True
                    # 欧洲/非洲/美洲/大洋洲 → B群
                    if (country_code in EUROPE_COUNTRY_CODES or
                        country_code in AFRICA_COUNTRY_CODES or
                        country_code in AMERICA_COUNTRY_CODES or
                        country_code in OCEANIA_COUNTRY_CODES):
                        log(f"❌ 目的地 '{first_destination}' 属于欧美非大洋洲，归B群")
                        return False
                    # 其他未分类国家码 → A群（默认归A）
                    log(f"✅ 目的地 '{first_destination}' 国家码未分类，默认归A群")
                    return True

        except requests.exceptions.RequestException as e:
            log(f"⚠️ GeoNames识别第{attempt+1}次失败: {str(e)[:50]}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue

        except Exception as e:
            log(f"⚠️ GeoNames识别第{attempt+1}次异常: {str(e)[:50]}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue

    # ========== 第二步：备用规则（GeoNames调用失败时使用关键词匹配）==========
    log(f"⚠️ GeoNames识别失败，使用备用关键词判断目的地: '{destination}'")

    # 亚洲关键词 → A群
    for keyword in ASIA_KEYWORDS:
        if keyword in destination or keyword in first_destination:
            log(f"✅ 备用规则检测到亚洲目的地: {keyword}，归A群")
            return True

    # 中东关键词 → A群
    for keyword in MIDDLE_EAST_KEYWORDS:
        if keyword in destination or keyword in first_destination:
            log(f"✅ 备用规则检测到中东目的地: {keyword}，归A群")
            return True

    # 欧洲/非洲/美洲/大洋洲关键词 → B群（命中即返回False）
    for keyword in EUROPE_KEYWORDS:
        if keyword in destination or keyword in first_destination:
            log(f"❌ 备用规则检测到欧洲目的地: {keyword}，归B群")
            return False

    for keyword in AFRICA_KEYWORDS:
        if keyword in destination or keyword in first_destination:
            log(f"❌ 备用规则检测到非洲目的地: {keyword}，归B群")
            return False

    for keyword in AMERICA_OCEANIA_KEYWORDS:
        if keyword in destination or keyword in first_destination:
            log(f"❌ 备用规则检测到美洲/大洋洲目的地: {keyword}，归B群")
            return False

    # 默认归 A群（未匹配任何规则时）
    log(f"⚪ 目的地 '{destination}' 未匹配任何规则，默认归A群")
    return True







# 【飞书相关缓存已移除】2026-09-20 飞书 token / 群通知 / Bitable 写入集成已完全弃用：
# last_feishu_notify / last_bitable_write / feishu_token_cache / feishu_bitable_token_cache
# 均不再需要，已删除。



# 拼号 / 待补座机状态已改为「基于 web 传入 history 无状态推导」（见
# _reconstruct_accumulated_digits / find_pending_landline_suffix），
# 不再使用服务端内存缓存。

# 缓存过期时间（秒）- 默认10分钟，超过10分钟未更新的缓存将被清理
DIGIT_CACHE_EXPIRE_SECONDS = 10 * 60  # 10分钟

# 最大缓存数量限制（防止内存溢出）
MAX_CACHE_ENTRIES = 1000

# 【已弃用】get_bitable_access_token 已移除：飞书 token 不再获取（2026-09-20）。
# 飞书多维表格（Bitable）读写逻辑虽保留为死代码，但已无任何入口调用。















def clean_destination_for_bitable(raw_destination):
    """
    把粗糙的产品目的地清洗成多维表"咨询目的地"字段能用的短名
    （多维表"咨询目的地"是 type=4 多选字段，选项是短名如"苏梅岛"、"法瑞意"等，
     写入"苏梅岛5日4晚私家团"会创建脏选项，必须先清洗）

    清洗规则（按顺序匹配）：
      1. 移除尾部所有时间/天数/团型信息：
         - "5日4晚私家团"  /  "5天4晚跟团游"  /  "10日游"  /  "10天自由行"  /  "5晚"
      2. 移除尾部"+"号和空格
      3. 命中已知"地区/国家组合"别名（如"法瑞意"、"土耳其希腊"、"东欧"、"毛球塞舌尔"）→ 整体保留
      4. 兜底：返回原始值（去尾空格）
    """
    if not raw_destination:
        return ''

    import re
    name = str(raw_destination).strip()
    if not name:
        return ''

    # ---------- 0. 别名优先匹配（防止"法瑞意"被切成"欧洲法瑞意"） ----------
    # 多维表里"咨询目的地"的现有选项的别名表
    alias_priority = [
        '毛球塞舌尔', '土耳其希腊', '法瑞意', '德法瑞意', '德奥捷', '英爱',
        '东欧', '西欧', '南欧', '北欧', '欧洲',
        '美娜多', '亚庇',
    ]
    for alias in alias_priority:
        if alias in name:
            return alias

    # ---------- 1. 尾部时间+团型 一次性吃掉 ----------
    tail_patterns = [
        r'\d+\s*日\s*\d*\s*晚?\s*(?:私家团|跟团游|定制游|自由行|半自由行|小包团|包团|私家定制|定制套餐|深度游|品质游|跟团|私家|定制|自由|深度|品质|探索之旅|之旅|行程|游|团|行)?\s*$',
        r'\d+\s*天\s*\d*\s*晚?\s*(?:私家团|跟团游|定制游|自由行|半自由行|小包团|包团|私家定制|定制套餐|深度游|品质游|跟团|私家|定制|自由|深度|品质|探索之旅|之旅|行程|游|团|行)?\s*$',
        r'\d+\s*晚\s*\d*\s*日?\s*(?:私家团|跟团游|定制游|自由行|半自由行|私家|定制|自由|行程|游|团|行)?\s*$',
        r'(?:私家团|跟团游|定制游|自由行|半自由行|小包团|包团)\s*$',
        r'(?:私家|定制|跟团|品质|深度|包团|自由|半自由)?\s*(?:团|游|行)\s*$',
    ]

    cleaned = name
    # 反复剥离尾部，直到没有匹配为止
    for _ in range(3):  # 最多 3 轮，防止极端情况
        prev = cleaned
        for pat in tail_patterns:
            new = re.sub(pat, '', cleaned).strip()
            if new and new != cleaned:
                cleaned = new
        if cleaned == prev:
            break

    # ---------- 2. 处理尾部的"+号"和空格（如"巴厘岛+科莫多+"） ----------
    cleaned = re.sub(r'[+＋\s]+$', '', cleaned).strip()

    # ---------- 3. 如果清洗后是空的，回退到原始值 ----------
    if not cleaned:
        return name

    return cleaned



def add_record_to_bitable(record_data, max_retries=2):
    """
    向飞书多维表格新增一条记录（无需触发条件，按需调用）
    :param record_data: dict，键为飞书多维表格的字段名（中文），值为实际写入的内容
    :param max_retries: 最大重试次数
    :return: 成功返回True，失败返回False
    """
    if not ENABLE_FEISHU_BITABLE:
        log("ℹ️ 飞书多维表格写入未启用，跳过")
        return False

    if not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        log("⚠️ 未配置 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID，跳过写入")
        return False

    # 1. 获取 access_token（使用多维表格专用应用凭证，与群消息应用隔离）
    access_token = get_bitable_access_token()
    if not access_token:
        log("❌ 无法获取多维表格 access_token，跳过写入")
        return False

    # 2. 构造 fields 字段：按字段类型做正确格式化
    MULTISELECT_FIELDS = {"进线IM店", "咨询目的地", "客户级别", "定制师接单", "下单平台"}
    BOOLEAN_FIELDS = {"是否留电", "是否下单", "定制单"}
    DATE_FIELDS = {"咨询日期时间"}

    def _fmt_value(field_name, raw_value):
        """根据字段类型格式化值"""
        if raw_value is None or raw_value == "" or raw_value == []:
            return None
        if field_name in MULTISELECT_FIELDS:
            # 多选：转 list[str]
            if isinstance(raw_value, list):
                return [str(x).strip() for x in raw_value if str(x).strip()]
            else:
                s = str(raw_value).strip()
                if not s:
                    return None
                # 支持 " / , | " 等分隔符
                parts = [p.strip() for p in s.replace(" / ", ",").replace("/", ",").replace("|", ",").split(",") if p.strip()]
                return parts if parts else [s]
        if field_name in BOOLEAN_FIELDS:
            # 复选框：转 bool
            if isinstance(raw_value, bool):
                return raw_value
            s = str(raw_value).strip().lower()
            return s in ("是", "yes", "true", "1", "✓", "已留电", "已下单")
        if field_name in DATE_FIELDS:
            # 日期：转 int 毫秒时间戳
            if isinstance(raw_value, (int, float)):
                # 如果是秒级时间戳，转毫秒
                return int(raw_value * 1000) if raw_value < 1e12 else int(raw_value)
            return int(time.time() * 1000)
        # 文本
        return str(raw_value)

    fields = {}
    if 'FEISHU_BITABLE_FIELD_MAPPING' in globals() and isinstance(FEISHU_BITABLE_FIELD_MAPPING, dict):
        for field_name, data_key in FEISHU_BITABLE_FIELD_MAPPING.items():
            if data_key in record_data:
                v = _fmt_value(field_name, record_data[data_key])
                if v is not None and v != "" and v != []:
                    fields[field_name] = v
    else:
        # 如果没有映射表，则直接使用 record_data 的 key 作为字段名（按文本处理）
        for k, v in record_data.items():
            if v not in (None, "", []):
                fields[k] = str(v) if not isinstance(v, str) else v

    if not fields:
        log("⚠️ 无有效字段可写入多维表格")
        return False

    # 3. 构造请求 URL
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
    )
    params = {}
    if 'FEISHU_BITABLE_VIEW_ID' in globals() and FEISHU_BITABLE_VIEW_ID:
        params["view_id"] = FEISHU_BITABLE_VIEW_ID

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {"fields": fields}

    # 4. 发起请求（带重试）
    for attempt in range(max_retries):
        try:
            log(f"📊 正在写入飞书多维表格: 字段数={len(fields)} (第{attempt+1}次)")
            response = requests.post(
                url, headers=headers, params=params, json=payload, timeout=8
            )
            result = response.json()
            if result.get("code") == 0:
                record_id = result.get("data", {}).get("record", {}).get("record_id", "")
                log(f"✅ 飞书多维表格写入成功: record_id={record_id}")
                return True
            else:
                log(f"❌ 飞书多维表格写入失败（{attempt+1}/{max_retries}）: {result}")
        except requests.exceptions.Timeout:
            log(f"❌ 飞书多维表格写入超时（{attempt+1}/{max_retries}）")
        except Exception as e:
            log(f"❌ 飞书多维表格写入异常（{attempt+1}/{max_retries}）: {e}")

        if attempt < max_retries - 1:
            time.sleep(1)

    return False



def safe_bitable_write(record_data, contact_info_key="contact_info", allow_empty=False):
    """
    【稳定版】写入飞书多维表格
    行为：
      1. 检查 contact_info 字段（默认 "contact_info"）是否非空
         - 如果为空（即没识别到手机号/微信号）→ 默认跳过，不写 Bitable
         - 如果 allow_empty=True → 即使无联系方式也写入（用于"新客人首次创建行"）
         - 如果非空 → 写入 Bitable
      2. 写入失败 → 自动保存到本地队列 bitable_pending.jsonl
      3. 队列中的记录会在下次启动时自动重试
    :param record_data: 飞书记录 dict
    :param contact_info_key: 用于判断的联系方式字段 key
    :param allow_empty: 是否允许空联系方式写入（True=新客人首次创建行，False=老客人无新联系方式跳过）
    :return: True=写入成功，False=跳过/失败
    """
    if not ENABLE_FEISHU_BITABLE:
        return False

    # 【关键】只有识别到联系方式（手机号/微信号）才写入 Bitable
    # 【2026-07-14 改造】新增 allow_empty 参数：新客人首次创建行时强制写入
    contact_value = record_data.get(contact_info_key, "")
    if not allow_empty and (not contact_value or (isinstance(contact_value, str) and not contact_value.strip())):
        log(f"⏭️ [Bitable] 未识别到联系方式，跳过写入（客户: {record_data.get('guest_nickname', '?')}）")
        return False

    # 尝试写入
    try:
        ok = add_record_to_bitable(record_data)
        if ok:
            return True
        else:
            log(f"⚠️ [Bitable] 写入失败，将加入失败队列")
    except Exception as e:
        log(f"⚠️ [Bitable] 写入异常: {e}，将加入失败队列")

    # 写入失败 → 保存到本地队列
    _save_to_bitable_pending_queue(record_data)
    return False



def _save_to_bitable_pending_queue(record_data):
    """保存到失败队列（jsonl 格式）"""
    try:
        import json as _json
        record_data = dict(record_data)  # 拷贝避免修改原数据
        record_data['_pending_time'] = time.time()
        record_data['_pending_time_str'] = time.strftime('%Y-%m-%d %H:%M:%S')
        record_data['_pending_count'] = 0
        with open(BITABLE_PENDING_FILE, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record_data, ensure_ascii=False) + "\n")
        log(f"💾 [Bitable] 已保存到失败队列: {BITABLE_PENDING_FILE} (待启动时重试)")
    except Exception as e:
        log(f"❌ [Bitable] 保存到队列失败: {e}")



def build_bitable_record_from_context(guest_id, guest_msg, effective_location,
                                       product_info=None, guest_info=None,
                                       contact_info=None, is_returning_customer=False,
                                       screenshot_url=""):
    """
    把当前会话的上下文信息组装成多维表格记录（统一的数据收集点）
    :return: dict
    """
    # ========== 【多选字段选项白名单】 ==========
    # ⚠️ 飞书多选字段（type=4）只接受表中已有的选项值
    # 传入不在白名单里的值会报 1254302 错误（误报为"权限"，实际是"非法选项"）
    # 这里的白名单基于"测试"sheet 的实际字段选项，需在 Bitable 新增选项时同步更新
    MULTISELECT_WHITELIST = {
        "进线IM店": {"路境天河", "路境浙江", "畅游店", "合美广州", "印尼店", "宝龙杭州", "酷游白云"},
        "咨询目的地": {
            "法罗群岛", "美娜多", "哈尔滨", "阿勒泰", "薄荷岛", "巴厘岛", "甲米",
            "肯尼亚", "挪威", "美国", "法瑞意", "亚庇", "大溪地", "新疆",
            "马达加斯加", "苏梅岛", "土耳其希腊", "冰岛", "东欧", "毛球塞舌尔",
            "桂林", "新西兰", "格陵兰岛", "奥地利", "新加坡", "斯里兰卡+马代",
            "丽贝岛", "西欧多地", "敦煌", "雅加达", "稻城", "赛尔维亚", "澳洲",
            "仙本那", "格鲁吉亚", "韩国", "俄罗斯", "长白山", "澳大利亚",
            "马德拉群岛", "埃及", "日本", "普吉岛", "南非", "秘鲁", "留尼旺",
            "日本东京", "马尔代夫", "欧洲", "非洲", "南美", "东南亚",
        },
        "客户级别": {"普通", "黄金", "白银", "铂金", "钻石", "金钻", "黑钻"},
        "定制师接单": {"菲菲", "海星", "小云", "关关", "安娜", "圆圆", "娇娇", "甜甜", "空号不通"},
        "下单平台": {"线下合同", "平台下单"},
    }
    MULTISELECT_FIELD_NAMES = set(MULTISELECT_WHITELIST.keys())

    def _normalize_user_tag(value):
        """
        清洗客户等级值：去除"会员"/"客户"等后缀
        "铂金会员" -> "铂金"
        """
        if not value:
            return value
        s = str(value).strip()
        # 常见后缀
        for suffix in ["会员", "客户", "vip", "VIP", "Vip", "用户", "游客"]:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                break
        return s

    def _sanitize_multiselect(field_name, raw_value, remark_collector):
        """
        把多选字段值清洗成白名单里的合法选项
        - 匹配白名单的 → 保留
        - 不匹配的 → 丢到 remark（保留信息但不影响写入）
        - 特殊：咨询目的地遇到"+号组合" → 尝试拆分单个再匹配
        - 【2026-07-06 简化】进线IM店字段：走标准白名单逻辑（飞书表已手动添加全部选项）
        """
        if not raw_value:
            return None

        # 统一成 list[str]
        if isinstance(raw_value, list):
            candidates = [str(x).strip() for x in raw_value if str(x).strip()]
        else:
            s = str(raw_value).strip()
            if not s:
                return None
            candidates = [p.strip() for p in s.replace(" / ", ",").replace("/", ",").replace("|", ",").split(",") if p.strip()]
            if not candidates:
                candidates = [s]

        # 【2026-07-16 新增】"用户标签+等级"组合拆分（"用户标签黄金会员" → "用户标签" + "黄金"）
        if field_name == "客户级别":
            new_candidates = []
            for c in candidates:
                if "用户标签" in c:
                    # 提取"用户标签"后的部分作为等级
                    _level_part = c.replace("用户标签", "").strip()
                    if _level_part:
                        # "用户标签"原值转存到 remark（保留信息）
                        remark_collector.append(f"{field_name}(用户标签原值): {c}")
                        new_candidates.append(_level_part)
                    else:
                        remark_collector.append(f"{field_name}(用户标签): {c}")
                else:
                    new_candidates.append(c)
            candidates = new_candidates

        # 【新增】客户级别字段先做后缀清洗（"铂金会员" → "铂金"）
        if field_name == "客户级别":
            candidates = [_normalize_user_tag(c) for c in candidates]

        whitelist = MULTISELECT_WHITELIST.get(field_name, set())
        valid = []
        invalid = []

        for c in candidates:
            if c in whitelist:
                # 整体在白名单
                valid.append(c)
            elif field_name == "咨询目的地" and ("+" in c or "＋" in c):
                # 尝试拆分"+号组合"
                parts = [p.strip() for p in c.replace("＋", "+").split("+") if p.strip()]
                parts_in_wl = [p for p in parts if p in whitelist]
                if parts_in_wl:
                    # 拆分后的部分有在白名单的，加入
                    valid.extend(parts_in_wl)
                    if any(p not in whitelist for p in parts):
                        remark_collector.append(f"{field_name}(部分未匹配): {c}")
                else:
                    invalid.append(c)
            else:
                invalid.append(c)

        if invalid:
            # 丢到 remark，保留信息
            remark_collector.append(f"{field_name}(原值): {'/'.join(invalid)}")
            log(f"⚠️ {field_name} 值不在白名单，已转存到 remark: {invalid}")

        return valid if valid else None

    # 客人昵称：去掉括号后面部分
    clean_nickname = guest_id or ""
    if clean_nickname:
        m = re.match(r'^([^(]+)', clean_nickname)
        if m:
            clean_nickname = m.group(1).strip()

    # 提取基础信息
    product_info = product_info or {}
    guest_info = guest_info or {}

    # 提取目的地/出发地（多维表用清洗后的短名）
    raw_destination = product_info.get('destination', '') or effective_location or ''
    destination = clean_destination_for_bitable(raw_destination) if 'clean_destination_for_bitable' in globals() else raw_destination
    if not destination:
        destination = raw_destination  # 兜底
    departure_city = product_info.get('departure_city', '')

    # 联系方式
    contact_text = ""
    if contact_info:
        contact_text = "; ".join(contact_info) if isinstance(contact_info, list) else str(contact_info)

    # 咨询时间
    consult_time_ms = int(time.time() * 1000)

    # 用来收集被多选字段丢弃的值
    remark_collect = []

    # 清洗多选字段值
    # ========== 【进线IM店】支持写死模式 ==========
    if HARDCODED_SHOP_NAME_ENABLED and HARDCODED_SHOP_NAME:
        # 写死模式：直接使用配置的店铺名（跳过 OCR 识别结果）
        shop_name_raw = HARDCODED_SHOP_NAME
        log(f"🔒 [进线IM店写死] 使用固定值: {shop_name_raw}")
    else:
        # 正常模式：从产品信息面板 OCR 识别（即 extract_shop_name() 采集的本机用户名）
        shop_name_raw = product_info.get('shop_name', '')
        if shop_name_raw:
            log(f"🏪 [进线IM店采集] 使用采集值: {shop_name_raw}")
        else:
            log(f"⚠️ [进线IM店采集] 采集值为空，请检查 extract_shop_name()")
    shop_name_sanitized = _sanitize_multiselect("进线IM店", shop_name_raw, remark_collect)
    destination_sanitized = _sanitize_multiselect("咨询目的地", destination, remark_collect)
    user_tag_sanitized = _sanitize_multiselect("客户级别", guest_info.get('user_tag') or '', remark_collect)

    # 组装 remark（暂不填写，按用户要求留空）
    remark_text = ""

    # 是否留电：只有有联系方式（手机号/微信号）才打勾
    # 【2026-07-16 修改】不再因为"老客户已留过"自动打勾，必须本次有联系方式才打勾
    is_liudian = bool(contact_text)

    record = {
        # 多选字段：合法选项为空时返回 None（让 add_record_to_bitable 跳过该字段）
        "shop_name": shop_name_sanitized if shop_name_sanitized else None,
        "guest_nickname": clean_nickname,
        # ⚠️ 修正：留咨信息=联系方式，不是留言；留言已塞到 remark 里
        "contact_info": contact_text,
        "destination": destination_sanitized if destination_sanitized else None,
        "user_tag": user_tag_sanitized if user_tag_sanitized else None,
        # 识别产品名称：OCR 识别的产品全名（如"巴厘岛+科莫多6日5晚私家团-在静谧的海湾划独木舟"）
        "product_name": product_info.get('product_name', '') or '',
        "is_new_customer": is_liudian,  # 复选框 bool：已留电/老客户=True
        "consult_time": consult_time_ms,
        "remark": remark_text,  # 暂留空
    }
    return record



AD_STRONG_KEYWORDS = [
    "有需要可以联系", "有需要可以随时联系", "有需要联系", "需要的话联系", "联系我们",
    "欢迎咨询我们", "合作推广", "推广合作", "渠道合作", "引流", "代运营",
    "接单", "互推", "广而告之",
    # 招代理 / 拉新类垃圾（明确是向本社兜售，必判广告）
    "诚招代理", "招代理", "代理加盟", "招商加盟", "招收代理", "代理招募", "招募代理",
    # 同行 / 分销 / 招商类垃圾（自报同行身份向本社兜售）
    "代理招商", "招商代理", "合作分销", "分销合作", "渠道分销", "同行接单",
    "同行招商", "同业接单", "接单分销", "渠道接单",
    # 分销 / 拉新 / 佣金类垃圾（向本社兜售代理或引流）
    "分销", "代理分销", "佣金", "日结", "招代理", "校园代理", "旅游卡分销",
    # 推广 / 集赞 / 进群类垃圾
    "免费送旅游", "免费送行程", "免费旅游", "集赞", "朋友圈集赞", "扫码进群", "进群领", "进群抢",
    # 广告置换 / 接广告
    "广告置换", "接广告", "置换合作",
    # 转介绍 / 带客合作
    "介绍客户", "互相介绍", "合作带客", "带客合作",
    # 倒卖 / 加价 / 批量批发类
    "量大从优", "成本价加", "加价出", "倒卖", "转卖", "批发合作",
]
AD_SELF_INTRO_WORDS = ["我这边是", "我这边提供", "我们提供", "我们这边提供", "我们这边是", "我们这边可", "我司", "本公司", "专做", "主营", "专业提供",
                       "我是导游", "我带团", "我是做", "我是一名", "我公司", "我们公司", "我这边做", "我是做导游"]
AD_SERVICE_WORDS = ["车队", "用车", "包车", "地接", "华人接", "中文司机", "中文车", "送机", "接机", "接车", "送车", "导游", "租车", "华人地陪", "境外接送", "境外接车",
                     "机票", "特价机票", "国际机票", "机票预订", "机票代理", "机票出票", "订票", "出票", "航班", "航空票务", "机票批发", "低价机票",
                     "签证代办", "签证代理", "地陪", "酒店资源", "车队资源"]
# 供应商招商/供货兜售特征词：对方以本社为客户、主动把服务"提供/可承接"给旅行社。
# 正常游客绝不会对旅行社说"我们提供/可承接 XX 服务"，故可作为可靠广告信号。
AD_SUPPLIER_OFFER_WORDS = [
    "我们提供", "我们这边提供", "我们可以提供", "我们可提供", "我们可承接", "可提供",
    "我司提供", "我司可", "我司承接", "本公司提供", "我方提供", "我方可以",
    "我这边提供", "我这边可", "我们这边可", "我们这边承接", "我们这边是",
    "贵社提供", "为贵社提供", "贵方提供",
]
# 招商上下文词：出现供货/自荐表述时，搭配这些词即可认定是向本社兜售
AD_SOLICIT_CONTEXT = ["配套", "需求", "合作", "资源", "承接", "推广", "渠道", "代理",
                      "贵社", "贵公司", "贵方", "票务", "酒店", "车队", "地接"]
AD_CUSTOMER_CUES = ["请问", "想问", "咨询", "多少钱", "报价", "行程", "攻略", "推荐", "怎么", "可以吗", "你们有", "有没有", "帮我看", "计划", "想带", "想玩"]

# 广告类型分类规则：根据命中的服务关键词归类（用于推送给群时标注"这是什么广告"）
AD_CATEGORY_RULES = [
    ("用车/接送/地接", ["车队", "用车", "包车", "地接", "华人接", "中文司机", "中文车", "送机", "接机", "接车", "送车", "导游", "租车", "华人地陪", "境外接送", "境外接车"]),
    ("机票/票务", ["机票", "特价机票", "国际机票", "机票预订", "机票代理", "机票出票", "订票", "出票", "航班", "航空票务", "机票批发", "低价机票"]),
    ("代运营/推广合作", ["代运营", "互推", "商务合作", "合作推广", "推广合作", "渠道合作", "引流", "广而告之", "接单"]),
]


def classify_ad(content):
    """【2026-09-17 新增】判断广告具体是关于什么的，返回逗号分隔的类型标签。
    仅做关键词归类，不重新判定是否广告（是否广告由 is_ad_message 负责）。
    命中多个类别则全部列出；都没命中但确为广告时归为'其他推广/广告'。
    """
    if not content:
        return "其他推广/广告"
    cats = []
    for label, kws in AD_CATEGORY_RULES:
        for kw in kws:
            if kw in content:
                cats.append(label)
                break
    if not cats:
        return "其他推广/广告"
    # 去重保序
    seen = set()
    result = []
    for c in cats:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return "、".join(result)


def is_ad_message(text):
    """判断客人消息是否为广告/同行推销。
    规则：
      1. 命中强特征词 → 广告
      2. 同时出现"自报家门"和"推销服务"词，且不含客户咨询语气 → 广告
      3. 其余 → 非广告
    """
    if not text:
        return False
    t = str(text)
    for kw in AD_STRONG_KEYWORDS:
        if kw in t:
            return True
    has_service = any(w in t for w in AD_SERVICE_WORDS)
    # 供应商招商/供货兜售：对方主动把服务"提供/可承接"给本社（"我们提供/可承接 X"）。
    # 方向是"对方供货给本社"，即使含"咨询/请问"等字眼也不能放行（如
    # "想咨询下贵社是否有用车需求，我们可以提供配套服务"）。
    supplier_offer = any(w in t for w in AD_SUPPLIER_OFFER_WORDS)
    if supplier_offer:
        ctx = has_service or any(w in t for w in AD_SOLICIT_CONTEXT)
        if ctx:
            return True
    # 客户咨询语气 → 放行（避免"请问有接机服务吗"被误判）
    for cue in AD_CUSTOMER_CUES:
        if cue in t:
            return False
    # 同行 / 合作分销推销：自报同行身份（同行/同业）+ 第一人称自荐（我/我们/我司）
    # + 商业意向词（合作/分销/招商/代理/资源/接单…）。用"第一人称"约束避免把
    # "你们和同行合作吗"这类客户好奇问句误判为广告。
    _peer = ("同行", "同业")
    _self = ("我这边", "我们", "我司", "本公司", "我方", "我是", "我这边是", "我们这边")
    _comm = ("合作", "分销", "渠道", "接单", "供货", "招商", "代理", "资源", "提供", "承接", "可接")
    if any(p in t for p in _peer) and any(s in t for s in _self) and any(c in t for c in _comm):
        return True
    has_intro = any(w in t for w in AD_SELF_INTRO_WORDS)
    if has_intro and has_service:
        return True
    # 私下交易 / 绕开平台 / 代购推销：旅游IM场景下的违规揽客与绕过平台行为
    _bypass = ("不走平台", "别走平台", "绕开平台", "绕平台", "私下转账", "私下交易", "私下付款", "私下付")
    if any(b in t for b in _bypass):
        return True
    if "代购" in t and any(w in t for w in ("加vx", "加微信", "微信", "vx", "优惠", "便宜", "券", "折扣")):
        return True
    if ("加vx" in t or "加微信" in t or "加V" in t) and any(
            w in t for w in ("优惠", "便宜", "券", "甩单", "代理", "招商", "接单", "折扣", "低价", "最低价")):
        return True
    # 第一人称「卖家口吻」：我给你/我们给 + 最低价/底价/成本价 → 同行/卖家推销
    # 例如"旅游线路我给你最低价""我们给您成本价"。正常游客不会用"我给你/我们给"向旅行社报价。
    _seller_self = ("我给你", "我们给", "我这边给", "我给您", "我们给您", "我给你报", "我这边给您")
    _seller_price = ("最低价", "底价", "成本价", "优惠价", "工厂价", "批发价")
    if any(s in t for s in _seller_self) and any(p in t for p in _seller_price):
        return True
    return False



def detect_ad_for_guest(guest_id, guest_msg, history=None):
    """判断某客人当前/历史是否为同行广告号（接入主流程的核心判定）。

    纯基于 web 传入的 history 做内容扫描，不再依赖本地广告号库：
      web 保证每次传入该客户全生命周期的完整 history（含历史会话的广告句），
      因此历史任一条命中广告词即判定为广告，无需服务端持久化黑名单，
      重启 / 多实例均不受影响。

    同行常把"广告话术"和"留资"分两条发（先发'境外车队'，再发'我的号码是138…'），
    单看留资条永远不是广告，故必须结合历史消息一起扫描。

    返回 True 的两种情况：
      1. 当前消息直接命中广告词；
      2. 历史消息中任一条命中广告词。
    """
    if is_ad_message(guest_msg):
        return True
    if history:
        for _m in history:
            _c = _m.get("content", "") if isinstance(_m, dict) else str(_m)
            if is_ad_message(_c):
                return True
    return False



def find_ad_evidence(guest_id, guest_msg="", history=None):
    """回溯"真正触发广告判定"的那条消息原文（用于取证，避免误归因）。

    场景：同行常分两条发——先发广告话术（如"我们这边是境外车队服务…"），
    等你回复后再发留资（如"我的号码是138…"）。若取证时只取当前留资消息，
    群里会误以为"按手机号判广告"。判定逻辑(detect_ad_for_guest)会看历史，
    取证也必须同样回溯。

    查找顺序：当前消息 → 传入 history（全生命周期）。都未命中则返回当前消息
    （保持旧行为）。

    注：web 已保证每次传入该客户全生命周期的完整 history，取证只需回扫 web 传入的
    history 即可，无需任何本地会话内存（conversation_history.json 及其相关逻辑已移除）。
    """
    cur = str(guest_msg or "").strip()
    if cur and is_ad_message(cur):
        return cur
    # 1. web 传入的全生命周期 history（与 detect_ad_for_guest 同源同范围）
    if history:
        for _m in history:
            _c = _m.get("content", "") if isinstance(_m, dict) else str(_m)
            _c = str(_c or "").strip()
            if _c and is_ad_message(_c):
                return _c
    return cur



def is_product_id_reshare(guest_msg, product_info):
    """判断客人消息是否只是重复发送了当前产品的产品ID。
    命中则返回产品ID（字符串），否则返回 None。
    放在座机匹配之前调用，避免产品ID（如 66549322）被误判为座机号。
    """
    import re
    if not product_info:
        return None
    pid_raw = (product_info.get('product_id') or '').strip()
    if not pid_raw:
        return None
    pid_digits = re.sub(r'\D', '', pid_raw)
    if len(pid_digits) < 6:
        return None
    # 避免产品ID本身呈手机号形态时，把真实手机号误判成产品
    if re.match(r'1[3-9]\d{9}$', pid_digits):
        return None
    msg_digits = re.sub(r'\D', '', guest_msg or '')
    if not msg_digits:
        return None
    # 情况1：消息去掉非数字后等于产品ID数字部分（含字母前缀的产品ID也兼容）
    if msg_digits == pid_digits:
        return pid_raw
    # 情况2：消息里的数字串包含完整产品ID（如"我要咨询66549322这个产品"）
    if pid_digits in msg_digits:
        return pid_raw
    # 情况3：客人只发了部分ID（>=6位且是产品ID的子串），且消息基本就是这个数字
    if len(msg_digits) >= 6 and msg_digits in pid_digits and len((guest_msg or '').strip()) <= 25:
        return pid_raw
    return None


def clean_product_name_ocr(name):
    """清理产品名中的 OCR 噪声（迁移自 travel_im_auto，供 build_product_id_reply 使用）：
    - 去掉字段标签前缀（如 '名称'）
    - 去掉粘连的引号/反引号/分号
    - 去掉 OCR 误插入的空白与结尾孤立噪声字
    """
    if not name:
        return name
    import re
    for ch in ['`', "'", '’', '‘', '“', '”', '"', ';', '；']:
        name = name.replace(ch, '')
    name = re.sub(r'^名称[\s：:]*', '', name)
    name = name.strip()
    name = re.sub(r'\s+', '', name)
    name = re.sub(r'(团|游|行|之旅|线路|定制)[在的是她他你静]+$', r'\1', name)
    name = re.sub(r'[在静的是她他你]+$', '', name)
    return name.strip()


def build_product_id_reply(product_info, already_has_contact=False):
    """将产品名称/目的地等信息加工成给客人的回复文本（迁移自 travel_im_auto）。
    在座机匹配前发出：客户只复读了产品ID 时，直接回优化后的产品名，而不是丢给 LLM。

    :param already_has_contact: 本次 history 或当前消息是否包含真实联系方式。
        为 True 时不再追加"留联系方式"索取话术，避免重复索要。
    """
    pid = (product_info.get('product_id') or '').strip()
    name = (product_info.get('product_name') or '').strip()
    name = clean_product_name_ocr(name)
    # 目的地：优先取 destinations 列表首个，其次取 destination 字段
    dests = product_info.get('destinations') or []
    dest = (product_info.get('destination') or '').strip() or (dests[0] if dests else '')
    dest = clean_product_name_ocr(dest)
    contact_tail = "方便的话也可以加个V讯或📞哦（请勿二维码），让旅行管家给您规划更方便~"
    # 已留过联系 → 不重复索要
    tail = "" if already_has_contact else contact_tail
    if name:
        if dest:
            reply = (f"亲亲~您所说的是【{name}】，目的地是{dest}呢~ "
                     f"我可以帮您介绍玩法亮点、报价和适合人群，您想先了解哪方面呀？{tail}")
        else:
            reply = (f"亲亲~您所说的是【{name}】定制行程呢~ "
                     f"我可以帮您介绍玩法亮点、报价和适合人群，您想先了解哪方面呀？{tail}")
    elif pid:
        reply = f"亲亲~您发送的是产品编号 {pid}，我帮您核对一下详情，稍等哦~{tail}"
    else:
        reply = "亲亲~您发送的好像是产品编号，我帮您核实一下哈~"
    return reply


def _classify_contact(contact):
    """分类单条联系方式: 'phone' / 'wechat' / 'landline'"""
    if not contact:
        return 'wechat'
    import re as _re
    # 去除可能带的前缀
    cleaned = _re.sub(r'^(手机号|微信号|座机|需求单联系方式|需求单)[：:]', '', str(contact)).strip()
    # 【2026-09-19】座机号单独归为 landline，飞书/回显用"座机号："标签，避免误标成手机号
    if str(contact).startswith('座机'):
        return 'landline'
    # 手机号/座机: 含 7+ 位数字，可能带 + - 空格
    digits = _re.sub(r'\D', '', cleaned)
    if len(digits) >= 7 and (cleaned.startswith('+') or (cleaned and cleaned[0].isdigit())):
        return 'phone'
    return 'wechat'



def _parse_existing_contact_text(text):
    """解析已有的留咨信息文本

    统一使用新前缀：
      - 手机号：xxx
      - 微信号：xxx
      - 座机：xxx
      - 需求单:xxx  （英文冒号，cy2 不使用但保留兼容）
    """
    result = {"手机号": [], "微信号": [], "座机": [], "需求单联系方式": []}
    if not text:
        return result
    import re as _re
    # 匹配前缀（兼容中英文冒号）
    _req_re = _re.compile(r'^需求单[：:]\s*')
    _phone_re = _re.compile(r'^手机号[：:]\s*')
    _wechat_re = _re.compile(r'^微信号[：:]\s*')
    _landline_re = _re.compile(r'^座机号?[：:]\s*')
    for part in str(text).split(';'):
        part = part.strip()
        if not part:
            continue
        matched = False
        if _phone_re.match(part):
            value = _phone_re.sub('', part).strip()
            if value:
                _value_digits = _re.sub(r'\D', '', value)
                if len(_value_digits) == 11 and _value_digits.startswith('1'):
                    result['手机号'].append(value)
                else:
                    result['座机'].append(value)
            matched = True
        elif _wechat_re.match(part):
            value = _wechat_re.sub('', part).strip()
            if value:
                result['微信号'].append(value)
            matched = True
        elif _landline_re.match(part):
            value = _landline_re.sub('', part).strip()
            if value:
                result['座机'].append(value)
            matched = True
        elif _req_re.match(part):
            value = _req_re.sub('', part).strip()
            if value:
                result['需求单联系方式'].append(value)
            matched = True
        if not matched:
            kind = _classify_contact(part)
            label = '手机号' if kind == 'phone' else ('座机' if kind == 'landline' else '微信号')
            result[label].append(part)
    return result



def _merge_contact_info(existing_text, new_contacts, req_contact=None):
    """合并新旧联系方式，按前缀归类去重

    :param existing_text: 已有留咨信息（带前缀的字符串）
    :param new_contacts: 新聊天联系方式列表（裸值，如 ['138xx', 'wxid_xxx']）
    :param req_contact: 需求单联系方式（cy2 不传，传 None）
    :return: 合并后的字符串（如 "手机号：138xx; 微信号：wxid"）
    """
    parsed = _parse_existing_contact_text(existing_text)

    # 处理新聊天联系方式
    import re as _merge_re
    _merge_prefix_re = _merge_re.compile(r'^(手机号|微信号|座机|QQ号|需求单联系方式|需求单)[：:]\s*')
    for c in (new_contacts or []):
        c_str = str(c).strip()
        if c_str.startswith('需求单:') or c_str.startswith('需求单联系方式:'):
            continue
        _cleaned = _merge_prefix_re.sub('', c_str).strip()
        if not _cleaned:
            continue
        kind = _classify_contact(c_str)
        if kind == 'phone':
            _cleaned_digits = _merge_re.sub(r'\D', '', _cleaned)
            if len(_cleaned_digits) == 11 and _cleaned_digits.startswith('1'):
                parsed['手机号'].append(_cleaned)
            else:
                parsed['座机'].append(_cleaned)
        elif kind == 'landline':
            parsed['座机'].append(_cleaned)
        else:
            parsed['微信号'].append(_cleaned)

    # 处理需求单联系方式（cy2 不用，保留兼容）
    if req_contact:
        _req_val = re.sub(r'^需求单[：:]\s*', '', str(req_contact)).strip()
        if _req_val:
            parsed['需求单联系方式'].append(_req_val)

    # 去重
    for k in parsed:
        seen = set()
        uniq = []
        for v in parsed[k]:
            if v and v not in seen:
                seen.add(v)
                uniq.append(v)
        parsed[k] = uniq

    # 按固定顺序拼接
    parts = []
    for label in ["手机号", "微信号", "座机"]:
        for v in parsed[label]:
            parts.append(f"{label}：{v}")
    for v in parsed['需求单联系方式']:
        parts.append(f"需求单:{v}")
    return "; ".join(parts)



def search_bitable_by_nickname(nickname, max_retries=2):
    """
    按"用户账号"（昵称）搜索飞书多维表格中的现有记录
    返回 record_id 字符串；找不到返回 None
    """
    if not ENABLE_FEISHU_BITABLE or not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        return None
    if not nickname:
        return None

    access_token = get_bitable_access_token()
    if not access_token:
        return None

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records/search"
    )
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "用户账号",
                    "operator": "is",
                    "value": [nickname]
                }
            ]
        },
        "automatic_fields": False
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=8)
            result = response.json()
            if result.get("code") == 0:
                items = result.get("data", {}).get("items", [])
                if items:
                    record_id = items[0].get("record_id", "")
                    log(f"🔍 [Bitable] 按昵称找到现有记录: {nickname} → {record_id}")
                    return record_id
                else:
                    log(f"🔍 [Bitable] 按昵称未找到现有记录: {nickname}")
                    return None
            else:
                log(f"⚠️ [Bitable] 搜索失败（{attempt+1}/{max_retries}）: {result.get('msg', '')}")
        except Exception as e:
            log(f"⚠️ [Bitable] 搜索异常（{attempt+1}/{max_retries}）: {e}")
        if attempt < max_retries - 1:
            time.sleep(0.5)

    return None



def get_bitable_record(record_id, max_retries=2):
    """
    获取单条 Bitable 记录的完整内容（用于读出现有"留咨信息"做合并）
    返回 fields 字典；失败返回 {}
    """
    if not ENABLE_FEISHU_BITABLE or not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        return {}
    if not record_id:
        return {}

    access_token = get_bitable_access_token()
    if not access_token:
        return {}

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records/{record_id}"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=8)
            result = response.json()
            if result.get("code") == 0:
                record = result.get("data", {}).get("record", {})
                fields = record.get("fields", {})
                return fields
            else:
                log(f"⚠️ [Bitable] 读取记录失败（{attempt+1}/{max_retries}）: {result.get('msg', '')}")
        except Exception as e:
            log(f"⚠️ [Bitable] 读取记录异常（{attempt+1}/{max_retries}）: {e}")
        if attempt < max_retries - 1:
            time.sleep(0.5)

    return {}



def update_bitable_record(record_id, fields, max_retries=2):
    """
    PATCH 更新一条 Bitable 记录
    :param record_id: 飞书记录 ID
    :param fields: dict，飞书字段名 -> 值
    :return: True=成功，False=失败
    """
    if not ENABLE_FEISHU_BITABLE or not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        return False
    if not record_id or not fields:
        return False

    access_token = get_bitable_access_token()
    if not access_token:
        return False

    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records/{record_id}"
    )
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {"fields": fields}

    for attempt in range(max_retries):
        try:
            log(f"📝 [Bitable] PATCH 更新记录 {record_id}（{attempt+1}/{max_retries}）字段数={len(fields)}")
            response = requests.put(url, headers=headers, json=payload, timeout=8)
            result = response.json()
            if result.get("code") == 0:
                log(f"✅ [Bitable] PATCH 更新成功: {record_id}")
                return True
            else:
                log(f"❌ [Bitable] PATCH 失败（{attempt+1}/{max_retries}）: {result}")
        except requests.exceptions.Timeout:
            log(f"❌ [Bitable] PATCH 超时（{attempt+1}/{max_retries}）")
        except Exception as e:
            log(f"❌ [Bitable] PATCH 异常（{attempt+1}/{max_retries}）: {e}")
        if attempt < max_retries - 1:
            time.sleep(1)

    return False



def merge_or_create_bitable_record(record_data, contact_info_key="contact_info", max_retries=2, allow_empty=False):
    """
    智能写入：按"用户账号"搜索现有记录 → PATCH 合并 / POST 新建

    :param record_data: dict，来自 build_bitable_record_from_context
    :param contact_info_key: 联系方式字段 key（默认 'contact_info'）
    :param allow_empty: 是否允许联系方式为空时创建新记录
    :return: True=成功，False=失败
    """
    if not ENABLE_FEISHU_BITABLE:
        return False
    if not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        log("⚠️ 未配置 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID，跳过写入")
        return False

    nickname = record_data.get('guest_nickname', '')
    new_contact = record_data.get(contact_info_key, '') or ''
    req_contact = record_data.get('req_contact', '') or ''
    has_any_contact = bool(new_contact) or bool(req_contact)

    # 1. 搜索现有记录
    existing_record_id = search_bitable_by_nickname(nickname, max_retries=max_retries) if nickname else None

    if existing_record_id:
        # 2. 老客人：只有有新联系方式时才合并"留咨信息"
        if not has_any_contact:
            log(f"⏭️ [Bitable] 老客人无新联系方式，跳过更新（客户: {nickname}）")
            return True

        # 3. 读出现有 fields，准备合并
        existing_fields = get_bitable_record(existing_record_id, max_retries=max_retries)
        existing_contact = existing_fields.get('留咨信息', '') or ''

        # 4. 合并"留咨信息"（按前缀归类去重）
        merged_contact = _merge_contact_info(
            existing_text=existing_contact,
            new_contacts=record_data.get('contact_info_list') or (record_data.get(contact_info_key, '').split('; ') if record_data.get(contact_info_key) else []),
            req_contact=req_contact,
        )

        if merged_contact == existing_contact:
            log(f"⏭️ [Bitable] 合并后无变化（客户: {nickname}），跳过 PATCH")
            return True

        # 5. PATCH 更新（只更新留咨信息，不覆盖其他字段）
        log(f"🔄 [Bitable] PATCH 合并：现有='{existing_contact[:50]}...' + 新='{merged_contact[:50]}...'")
        return update_bitable_record(
            existing_record_id,
            {"留咨信息": merged_contact},
            max_retries=max_retries
        )
    else:
        # 6. 新客人：默认只有有联系方式才创建；allow_empty=True 时无联系方式也创建
        if not has_any_contact and not allow_empty:
            log(f"⏭️ [Bitable] 新客人无联系方式，跳过创建（客户: {nickname}）")
            return False

        # 7. 新建：把"留咨信息"用合并格式写入
        new_merged = _merge_contact_info(
            existing_text="",
            new_contacts=record_data.get('contact_info_list') or (record_data.get(contact_info_key, '').split('; ') if record_data.get(contact_info_key) else []),
            req_contact=req_contact,
        )
        record_data[contact_info_key] = new_merged
        log(f"➕ [Bitable] 新建记录（客户: {nickname}），留咨信息='{new_merged[:50] if new_merged else '(空)'}'")
        return add_record_to_bitable(record_data, max_retries=max_retries)



# ---- 自动补齐的散落常量 ----
BITABLE_PENDING_FILE = "bitable_pending.jsonl"
BITABLE_MAX_RETRY = 5
def extract_shop_name():
    """获取本机Mac的管理员显示名称作为店铺名称（桌面/macOS 专属，服务端一般用不到）。"""
    try:
        import os
        import subprocess
        # 使用dscl命令获取当前用户的显示名称（RealName）
        result = subprocess.run(
            ['dscl', '.', '-read', f'/Users/{os.getlogin()}', 'RealName'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # 解析输出，获取显示名称
            output = result.stdout
            # 格式通常是 "RealName: 显示名称"
            lines = output.strip().split('\n')
            for line in lines:
                if ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        shop_name = parts[1].strip()
                        if shop_name:
                            log(f"🏪 获取的店铺名称（管理员显示名称）: '{shop_name}'")
                            return shop_name

        # 如果获取失败，回退到使用系统用户名
        shop_name = os.getlogin()
        log(f"🏪 获取的店铺名称（系统用户名）: '{shop_name}'")
        return shop_name
    except Exception as e:
        log(f"❌ 提取店铺名称失败: {e}")
        # 回退到使用系统用户名
        try:
            return os.getlogin()
        except Exception:
            return ""


__all__ = [
    "is_ad_message", "classify_ad", "detect_ad_for_guest", "find_ad_evidence",
    "detect_contact_info", "_classify_contact", "is_product_id_reshare",
    "is_vip_destination",

    "log", "route_group", "analyze_message", "push_and_log",
    "detect_ad", "detect_contact",
]


# ====================== 编排助手（不含 OCR / 截图） ======================
def detect_ad(guest_id, guest_msg, history=None):
    """广告 / 同行推销判定。返回结构化结果。"""
    gid = guest_id
    is_ad = bool(detect_ad_for_guest(gid, guest_msg, history))
    ad_type = classify_ad(guest_msg) if is_ad else ""
    evidence = find_ad_evidence(gid, guest_msg, history) if is_ad else ""
    return {"guest_id": gid, "is_ad": is_ad, "ad_type": ad_type, "evidence": evidence}


def detect_contact(message, guest_id="", history=None):
    """联系方式识别。返回 detect_contact_info 的原始结构。"""
    return detect_contact_info(message, guest_id, history)


def route_group(destination="", is_ad=False):
    """根据目的地与是否广告号决定应投递的群。返回 (chat_id, label)。

    2026-09-19 改造：不再实际推送飞书群，故 chat_id 恒为 ""（占位），
    仅保留 label（A群 / B群 / C群）用于展示「建议推送的群」。
    """
    if is_ad:
        return "", "C群(广告/同行推销)"
    vip = is_vip_destination(destination or "")
    if vip:
        return "", "A群(亚洲/中东/海岛/其他)"
    return "", "B群(欧美非大洋洲)"


def analyze_message(user, message, history=None):
    """对一个客户消息做完整业务分析（迁移自 travel_im_auto 的广告/联系方式/路由逻辑）。

    返回 dict：
      guest_id        客户标识（由 API 直接传入，未再做清洗）
      is_ad           是否广告 / 同行推销
      ad_type         广告类型（is_ad 为真时）
      evidence        取证文本（is_ad 为真时）
      contacts        联系方式识别结果（detect_contact_info 原始结构）
      route_chat_id   应推送的飞书群 ID
      route_label     群说明（A/B群 / C群）
    """
    gid = user or ""
    is_ad = bool(detect_ad_for_guest(gid, message, history))
    ad_type = classify_ad(message) if is_ad else ""
    evidence = find_ad_evidence(gid, message, history) if is_ad else ""
    contacts = detect_contact_info(message, gid, history)
    route_chat_id, route_label = route_group("", is_ad)
    return {
        "guest_id": gid,
        "is_ad": is_ad,
        "ad_type": ad_type,
        "evidence": evidence,
        "contacts": contacts,
        "route_chat_id": route_chat_id,
        "route_label": route_label,
    }


def push_and_log(user, message, analysis, reply,
                 product_id="", product_name="",
                 image_path=None, stored_contacts=None):
    """不再实际推送飞书群；改为在副作用里展示联系方式与建议投递群。

    当检测到「完整联系方式」(contacts.has_real_contact) 时，在 result["contact_info"]
    中结构化展示：
        1) user_id        用户编号
        2) user_contact   用户联系方式
        3) product_id     产品ID
        4) suggested_group 建议推送的群（来自 analysis.route_label）
    不再向 A/B/C 群发送任何消息。广告/同行号判定已改为基于 web 传入 history 的全量内容扫描（本地广告号库已弃用），
    飞书多维表格（Bitable）落库已于 2026-09-20 完全弃用，不再写入。
    """
    result = {"notes": [],
              "route_chat_id": analysis.get("route_chat_id"),
              "route_label": analysis.get("route_label")}
    gid = analysis.get("guest_id") or (user or "")
    is_ad = analysis.get("is_ad", False)
    _rcid = analysis.get("route_chat_id")
    _rlabel = analysis.get("route_label") or "未定"
    contacts = analysis.get("contacts") or {}
    has_real_contact = contacts.get("has_real_contact", False)
    log(f"📨 本条消息目标群: {_rlabel}（chat_id={_rcid}）")
    result["notes"].append(f"目标群: {_rlabel} (chat_id={_rcid})")

    # 客人此前已留存的联系方式：由调用方基于 history 无状态推导后传入（不再读 guest_contacts 内存）
    stored = stored_contacts or []
    # 检测到完整联系方式 → 在副作用中展示四项信息（不再推送飞书群）
    if has_real_contact:
        shown_contacts = contacts.get("contacts") or []
        # 合并「本条新识别到的联系方式」与「历史已留存的联系方式(stored_contacts)」，
        # 避免只显示最新一条而漏掉此前已补全的座机号等其他号码
        # （例如客人先补全座机、后又说了手机号，下方应同时列出座机+手机）。
        # 按「类型」合并：本消息(shown)优先覆盖历史(stored)同类型，不同类型并存；
        # 保证客户多次提供同类号码时只显示最新一个，而非新旧都列出
        # （例如客人先留 01076121032、后留 01076121031，下方只显示后者）。
        _merged = []
        for _c in (list(stored) + list(shown_contacts)):
            if not _c:
                continue
            _t = str(_c).split(":", 1)[0].strip()
            _replaced = False
            for i, _m in enumerate(_merged):
                if str(_m).split(":", 1)[0].strip() == _t:
                    _merged[i] = _c
                    _replaced = True
                    break
            if not _replaced:
                _merged.append(_c)
        result["contact_info"] = {
            "user_id": gid,                                  # ① 用户编号
            "user_contact": _merged,                         # ② 用户联系方式（已合并历史留存）
            "product_id": product_id or "",                  # ③ 产品ID
            "suggested_group": _rlabel,                      # ④ 建议推送的群
        }
        result["notes"].append(
            "✅ 检测到完整联系方式，已在副作用 contact_info 展示（不再推送飞书群）")
        log(f"📞 已检测到完整联系方式: 用户={gid}, 群={_rlabel}, 联系方式={_merged}")
    elif stored:
        # 本条未新增联系方式，但客人之前已留过 → 副作用展示"此前已留存"，不再误显"无联系方式"
        result["contact_info"] = {
            "user_id": gid,
            "user_contact": stored,
            "product_id": product_id or "",
            "suggested_group": _rlabel,
        }
        result["notes"].append(
            "客户此前已留存联系方式（本条未新增）：" + "；".join(stored))
        log(f"📞 客人此前已留存联系方式: 用户={gid}, 群={_rlabel}, 联系方式={stored}")
    else:
        result["notes"].append("未检测到完整联系方式，无推送信息")

    # 广告/同行号：基于 web 传入 history 的内容扫描判定，仅标记，不推送任何群
    if is_ad:
        result["notes"].append("命中广告/同行号（基于历史内容扫描，不推送任何群）")

    return result
