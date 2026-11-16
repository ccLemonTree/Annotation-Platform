"""Read saved target attributes and fixed personnel description JSON for export."""
import json
import re

PERSON_FIELDS = {
    '性别': 'gender', '预估年龄区间': 'age_range', '人种': 'ethnicity', '身高预估': 'height',
    '体型': 'body_type', '发型': 'hair_style', '发色': 'hair_color', '面部特征': 'facial_features',
    '神态': 'expression', '穿着': 'clothing', '配饰': 'accessories',
    '特殊标记': 'distinguishing_marks', '其他特征': 'other_features',
}
PERSON_CATEGORIES = {
    'gender': ('男', '女'),
    'body_type': ('偏瘦', '标准', '健壮有肌肉', '偏胖', '肥胖'),
}
PERSON_MARKERS = ('性别', '预估年龄区间', '体型', '发型', '发色')
UNKNOWN = {'未知','其他','不确定','无法判断','无法识别','无','看不清','未提供','未识别','unknown','n/a','none','null'}


def explicit_person_negation(key, value):
    if key == 'accessories' and value in ('无', '无配饰', '无明显配饰'):
        return '没有配饰' if value != '无明显配饰' else '没有明显配饰'
    if key == 'clothing' and re.match(r'^(?:无上衣|未穿上衣|没有穿上衣)(?:$|[，,、；;。])', value):
        return '没有穿上衣'


def attribute_view(document):
    schema = document.get('attribute_schema') or {}
    values = {key: item['value'] for key, item in (document.get('attributes') or {}).items()}
    if values:
        return schema, values
    raw = document.get('description') or ''
    if not isinstance(raw, str) or not raw.lstrip().startswith('{'):
        return {}, {}
    try:
        description = json.loads(raw)
    except ValueError:
        return {}, {}
    if not isinstance(description, dict) or not any(name in description for name in PERSON_MARKERS):
        return {}, {}
    fields = []
    for name, key in PERSON_FIELDS.items():
        value = description.get(name)
        items = value if isinstance(value, list) else [value]
        known = [item.strip() for item in items if isinstance(item, str) and item.strip()
                 and (item.strip().casefold() not in UNKNOWN or explicit_person_negation(key, item.strip()))]
        if not known:
            continue
        if key == 'gender':
            known = [{'男性': '男', '女性': '女'}.get(item, item) for item in known]
        known = list(dict.fromkeys(known))
        options = PERSON_CATEGORIES.get(key, ())
        fields.append(dict(key=key, name=name, options=options,
            positive_only=len(items) != 1 or len(known) != 1 or known[0] not in options))
        values[key] = known if isinstance(value, list) else known[0]
    return dict(target_label='person', fields=fields), values
