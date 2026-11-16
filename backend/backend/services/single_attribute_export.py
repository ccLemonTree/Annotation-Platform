"""Build explicit image pairs from known values of one categorical attribute."""
from collections import defaultdict, deque
import re
from unicodedata import normalize

from services.export_attributes import UNKNOWN, attribute_view, explicit_person_negation
from services.swift_export import TARGET_FAMILIES, TARGET_SUBJECTS

COLOR_KEYS = {'body_color','plate_color','upper_color','lower_color','color'}
BRAND_ALIASES = {'benz':'奔驰','mercedes-benz':'奔驰','mercedes benz':'奔驰',
    'mercedes':'奔驰','梅赛德斯奔驰':'奔驰','梅赛德斯-奔驰':'奔驰','audi':'奥迪','bmw':'宝马'}
BRANDS = set('奔驰 奥迪 宝马 大众 丰田 本田 比亚迪 特斯拉 福特 日产 五菱 吉利 长安 奇瑞 现代 起亚 沃尔沃 保时捷'.split())
COLORS = set('黑白红蓝绿黄紫橙粉灰棕银金')
PLATE_TYPES = {
    '小型汽车':'小型汽车号牌','普通小型汽车':'小型汽车号牌',
    '大型汽车':'大型汽车号牌','普通大型汽车':'大型汽车号牌',
    '小型新能源汽车':'小型新能源汽车号牌','新能源小型汽车':'小型新能源汽车号牌',
    '大型新能源汽车':'大型新能源汽车号牌','新能源大型汽车':'大型新能源汽车号牌',
    '挂车':'挂车号牌',
}


def normalized_value(key, value):
    value = normalize('NFKC', value).strip().casefold()
    if key in COLOR_KEYS:return value.removesuffix('色')
    if key == 'brand':return BRAND_ALIASES.get(value,value)
    if key == 'plate':return re.sub(r'[\s·-]','',value)
    if key == 'plate_type':
        stem=re.sub(r'(号牌|车牌|牌照)$','',value)
        return PLATE_TYPES.get(stem,{'新能源':'新能源汽车号牌','新能源汽车':'新能源汽车号牌'}.get(stem,value))
    return value


def attribute_pairs(rows, negative_count, semantics='forward', *, key=None, query_value=None, reverse=False, positive_count=None):
    from services.embedding_negative_recommendations import original_id
    buckets = defaultdict(lambda: defaultdict(list))
    explicit = defaultdict(list)
    directions=(True,) if reverse or semantics=='reverse' else (False,True) if semantics=='both' else (False,)
    wanted = normalized_value(key,query_value) if key and query_value else None
    for asset, annotation in sorted(rows, key=lambda row: str(row[0].id)):
        schema, values = attribute_view(annotation.annotation_data)
        labels = schema.get('target_label') or []
        if isinstance(labels, str):
            labels = [labels]
        targets = {TARGET_FAMILIES.get(label.casefold().removeprefix('sam3_'), label.casefold().removeprefix('sam3_')) for label in labels}
        if len(targets) != 1:
            continue
        target = targets.pop()
        image = (str(asset.id), asset.content_hash, original_id(asset))
        for field in schema.get('fields', []):
            field_key = field['key']
            if key and key!=field_key:
                continue
            positive_only = field.get('positive_only', False)
            if positive_only and negative_count:
                continue
            raw = values.get(field_key)
            for value in raw if isinstance(raw,list) else [raw]:
                if not isinstance(value,str) or not value.strip():
                    continue
                if any(token in value or token in field['name'] for token in ('<image>','<video>','<audio>')):
                    continue
                normalized = normalized_value(field_key,value)
                if target=='person' and (phrase:=explicit_person_negation(field_key,value.strip())):
                    if True in directions and not negative_count and (not wanted or normalized==wanted):
                        explicit[f'图中这名人员{phrase}。'].append((image,value))
                    continue
                if not normalized or normalized in UNKNOWN:
                    continue
                options = field.get('options') or []
                if options and value not in options:
                    continue
                if not options and ((field_key in COLOR_KEYS and normalized not in COLORS)
                        or (field_key=='brand' and normalized not in BRANDS)
                        or (field_key=='plate' and not re.fullmatch(r'[\u4e00-\u9fa5][a-z][a-z0-9]{5,6}',normalized))):
                    continue
                display=normalized if field_key=='plate_type' else value
                buckets[target, field_key, field['name'], positive_only][normalized].append((image, display))

    streams=deque()
    for (target, field_key, name, positive_only), values in sorted(buckets.items()):
        subject = TARGET_SUBJECTS.get(target, target)
        for inverted in directions:
            if positive_only and inverted:
                continue
            # Broad categories and color-only descriptions can overlap specific plate types.
            comparable={value:items for value,items in values.items() if value in PLATE_TYPES.values()} if (
                field_key=='plate_type' and (inverted or negative_count)) else values
            if len(comparable)<2 and (inverted or negative_count):continue
            all_values = [item for _,items in sorted(comparable.items()) for item in items]
            streams.append(field_pairs(subject,name,comparable,all_values,inverted,negative_count,positive_count,
                normalized_value(field_key,query_value) if query_value else None))
    for text, matching in sorted(explicit.items()):
        streams.append(sample_pairs(text,matching,(),0,0,0,positive_count))
    while streams:
        stream=streams.popleft()
        pair=next(stream,None)
        if pair is not None:
            streams.append(stream)
            yield pair


def field_pairs(subject,name,values,all_values,inverted,negative_count,positive_count,wanted):
    offset=0
    for value,matching in sorted(values.items()):
        offset+=len(matching)
        if wanted and wanted!=value:continue
        text=f"图中{subject}的{name}{'不是' if inverted else '为'}{matching[0][1]}。"
        # Start after this value's bucket to avoid scanning it for every match.
        if inverted:
            positives=(all_values[(offset+i)%len(all_values)] for i in range(len(all_values)-len(matching)))
            budget=positive_count if positive_count is not None else len(matching)
            yield from sample_pairs(text,positives,matching,0,len(matching),negative_count,budget)
        else:
            yield from sample_pairs(text,matching,all_values,offset,len(all_values)-len(matching),negative_count,positive_count)


def sample_pairs(text,positives,candidates,offset,candidate_count,negative_count,positive_count):
    used = set()
    used_hashes = set()
    cursor=0
    for (asset_id, content_hash, origin), _ in positives:
        if positive_count is not None and len(used) >= positive_count:break
        if origin in used or content_hash in used_hashes:
            continue
        selected = []
        hashes = {content_hash}
        origins = {origin}
        for _ in range(candidate_count if negative_count else 0):
            (candidate_id, candidate_hash, candidate_origin), _ = candidates[
                (offset + cursor % candidate_count) % len(candidates)]
            cursor+=1
            if candidate_origin in origins or candidate_hash in hashes:
                continue
            selected.append(candidate_id)
            origins.add(candidate_origin)
            hashes.add(candidate_hash)
            if len(selected) >= negative_count:
                break
        if selected or not negative_count:
            used.add(origin)
            used_hashes.add(content_hash)
            yield text, asset_id, selected
