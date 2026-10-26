"""Explicit source groups and advisory splits; no filename/time inference."""
from collections import Counter
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SOURCE_FIELDS = ('parent_image_id', 'group_id', 'sequence_id', 'device_id')
SourceId = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]


class SplitPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phash_distance: int = Field(default=4, ge=0, le=64, strict=True)
    color_distance: int = Field(default=20, ge=0, le=255, strict=True)
    training_budget_per_class: int | None = Field(default=100, ge=1, strict=True)
    require_source: bool = False
    require_independent: bool = True


class SplitSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parent_image_id: SourceId | None = None
    group_id: SourceId | None = None
    sequence_id: SourceId | None = None
    device_id: SourceId | None = None


def source_metadata(asset):
    info = getattr(asset, 'device_info', None)
    if not isinstance(info, dict):
        return {}
    return {key: str(value).strip() for key in SOURCE_FIELDS
            if isinstance(value := info.get(key), (str, int)) and not isinstance(value, bool) and str(value).strip()}


def _components(ids, links):
    parents = {key: key for key in ids}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for left, right in links:
        parents[root(left)] = root(right)
    groups = {}
    for key in sorted(ids):
        groups.setdefault(root(key), []).append(key)
    return sorted(groups.values())


def source_groups(rows):
    sources = {str(asset.id): source_metadata(asset) for asset, _ in rows}
    owners, links = {}, []
    for asset, _ in sorted(rows, key=lambda row: str(row[0].id)):
        key = str(asset.id)
        tokens = [('content_hash', asset.content_hash), ('parent_image_id', key)]
        if getattr(asset, 'image_id', None):
            tokens.append(('parent_image_id', str(asset.image_id)))
        tokens.extend(sources[key].items())
        for token in tokens:
            links.append((key, owners.setdefault(token, key)))
    groups = _components(sources, links)
    # An original referenced by a crop has evidence even if its own JSONB is empty.
    covered = {key for group in groups if any(sources[key] for key in group) for key in group}
    return groups, dict(sorted(sources.items())), sorted(set(sources) - covered)


def near_duplicate_pairs(fingerprints, policy):
    items = [(key,(int(str(phash),16),color)) for key,(phash,color) in sorted(fingerprints.items())]
    # shortcut: pairwise checks suit small curated datasets; index fingerprints before bulk-scale use.
    for index, (left, (phash, color)) in enumerate(items):
        for right, (other, other_color) in items[:index]:
            if (phash ^ other).bit_count() <= policy.phash_distance and max(abs(a-b) for a, b in zip(color, other_color)) <= policy.color_distance:
                yield left, right


def split_report(rows, labels, policy, splits=None, suggest=False, fingerprints=None):
    groups, sources, missing = source_groups(rows)
    near_pairs = list(near_duplicate_pairs(fingerprints or {}, policy))
    links = [(group[0], key) for group in groups for key in group[1:]] + near_pairs
    combined = _components(sources, links)
    per_image = {str(asset.id): {b['label'] for b in ann.annotation_data['boxes'] if b['label'] in labels}
                 for asset, ann in rows}
    if suggest:
        splits, train_counts = {}, Counter()
        sizes = Counter(train=0, val=0, test=0)
        target = max(1, int(len(rows) * .6))
        for index, group in enumerate(sorted(combined, key=lambda group: (len(group), group))):
            counts = Counter(label for key in group for label in per_image[key])
            missing_holdouts = [name for name in ('val', 'test') if not sizes[name]]
            if len(combined) >= 3 and len(combined) - index <= len(missing_holdouts):
                split = missing_holdouts[0]
            elif sizes['train'] < target and all(policy.training_budget_per_class is None or
                    train_counts[label] + count <= policy.training_budget_per_class for label, count in counts.items()):
                split = 'train'
                train_counts.update(counts)
            else:
                split = min(('val', 'test'), key=lambda name: sizes[name])
            splits.update(dict.fromkeys(group, split))
            sizes[split] += len(group)
    splits = dict(sorted((splits or {}).items()))
    errors, warnings = [], []

    def error(code, message, **extra):
        errors.append(dict(code=code, message=message, **extra))

    if set(splits) != set(sources) or any(value not in ('train', 'val', 'test') for value in splits.values()):
        error('invalid_splits', '划分必须包含全部当前导出图片，且只能使用train/val/test')
    for group in groups:
        if len({splits[key] for key in group if key in splits}) > 1:
            error('source_leakage', '同来源/同内容分组不能跨训练、验证、测试划分', asset_ids=group)
    for left, right in near_pairs:
        if left in splits and right in splits and splits[left] != splits[right]:
            error('near_duplicate_leakage', '近重复图片跨数据划分，请放在同一划分', asset_ids=[left, right])
    if missing:
        warnings.append(f'{len(missing)} 张图片缺少明确来源，无法排除未知父图/设备/连续帧泄漏')
        if policy.require_source:
            error('source_missing', '严格来源策略要求补齐明确来源元数据', asset_ids=missing)
    warnings.append('来源元数据需人工确认完整性；pHash和平均颜色不能保证识别任意裁剪或跨帧近重复，建议不代表真实测试集已验收')
    image_counts = Counter(splits[key] for key in sources if key in splits)
    by_label = {split: {label: sum(splits.get(key) == split and label in image_labels
                    for key, image_labels in per_image.items()) for label in labels} for split in ('train', 'val', 'test')}
    for split in ('train', 'val', 'test'):
        if not image_counts[split]:
            message = f'缺少独立 {split} 图片，请补充不同来源分组'
            warnings.append(message)
            if policy.require_independent:
                error('missing_split', message, split=split)
        absent = [label for label in labels if by_label[split][label] == 0]
        if absent:
            warnings.append(f'{split} 缺少类别图片：' + '、'.join(absent))
    for label, count in by_label['train'].items():
        if policy.training_budget_per_class is not None and count > policy.training_budget_per_class:
            error('training_budget', f'{label} 训练图片 {count} 超过预算 {policy.training_budget_per_class}')
    if not rows:
        error('empty', '没有符合审核范围的已标注图片')
    return dict(version=1, policy=policy.model_dump(), grouping_fields=list(SOURCE_FIELDS),
                sources=sources, groups=combined, missing_source_asset_ids=missing, splits=splits,
                images_by_split=dict(image_counts), images_by_label_by_split=by_label,
                fingerprint_algorithm='phash64_and_rgb_mean', near_duplicate_checked=fingerprints is not None,
                fingerprints={key: dict(phash=str(value[0]), mean_rgb=list(value[1]))
                              for key, value in sorted((fingerprints or {}).items())},
                warnings=warnings, errors=errors, can_freeze=not errors)
