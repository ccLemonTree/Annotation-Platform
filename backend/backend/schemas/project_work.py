from collections import deque
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from schemas.object_attributes import AttributeSchema
from schemas.model_service import DEFAULT_DETECTION_CONFIDENCE
from schemas.automation import FollowUploads
from services.script_sandbox import validate_script


class WorkFollowUploads(FollowUploads):
    uploaded_crop_job_id: UUID | None = None


FOLLOW_UPLOAD_OPERATIONS = ('crop', 'caption', 'index', 'detect_attributes', 'attributes', 'quality')
MODEL_WORKFLOW_OPERATIONS = ('crop', 'detect', 'preannotate', 'ai_review', 'filter', 'quality', 'caption', 'attributes')
BUILTIN_WORKFLOW_OPERATIONS = ('branch', 'img_filter', 'box_clean', 'img_transform', 'script')
WorkflowOperationType = Literal[
    'crop', 'detect', 'preannotate', 'ai_review', 'filter', 'quality', 'caption', 'attributes',
    'branch', 'img_filter', 'box_clean', 'img_transform', 'script',
]

OPERATION_TITLES: dict[str, str] = {
    'crop': '检测切图',
    'detect': '目标检测筛选',
    'preannotate': '目标检测预标注',
    'ai_review': 'AI 审核',
    'filter': '大模型筛选',
    'quality': '大模型图片质量分析',
    'caption': '大模型描述',
    'attributes': '目标属性分析',
    'branch': '条件分流',
    'img_filter': '图像基础规则过滤',
    'box_clean': '检测框规则清洗',
    'img_transform': '图像变换与脱敏',
    'script': 'Python 脚本处理',
}


class WorkCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation: Literal['crop', 'caption', 'index', 'detect_attributes', 'attributes', 'quality']
    follow_uploads: bool = False
    model_id: UUID
    attribute_model_id: UUID | None = None
    example_asset_id: UUID | None = None
    example_annotation_version: StrictInt | None = Field(default=None, ge=1)
    asset_ids: list[UUID] = Field(min_length=1, max_length=100000)
    prompt: str = Field(default='', max_length=8000)
    attribute_prompt: str = Field(default='', max_length=8000)
    detection_prompt: str = Field(default='', max_length=8000)
    labels: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=DEFAULT_DETECTION_CONFIDENCE, ge=0, le=1)
    expansion: float = Field(default=.2, ge=0, le=1)
    crops_only: bool = Field(default=True, deprecated=True, description='兼容旧客户端。请在标注准备中单独修改项目标注范围。')
    min_crop_long_edge: StrictInt = Field(default=0, ge=0, le=32768)

    @model_validator(mode='after')
    def targets(self):
        if self.operation != 'crop' and len(self.asset_ids) > 5000:
            raise ValueError('此处理方式单批最多 5000 张')
        if self.follow_uploads and self.operation not in FOLLOW_UPLOAD_OPERATIONS:
            raise ValueError('此处理方式不支持接收新上传图片')
        if self.attribute_prompt and self.operation not in ('attributes', 'detect_attributes'):
            raise ValueError('属性核心提示词仅适用于目标属性分析')
        if self.detection_prompt and self.operation not in ('crop', 'detect_attributes'):
            raise ValueError('检测提示词仅适用于检测切图')
        if self.operation == 'quality' and not self.prompt:
            raise ValueError('请填写图片质量检查要求')
        if self.operation == 'quality' and len(self.prompt) > 2000:
            raise ValueError('图片质量检查要求不超过2000字符')
        if (self.example_asset_id is None) != (self.example_annotation_version is None):
            raise ValueError('正确范例需同时提供图片和标注版本')
        if self.example_asset_id is not None and self.operation not in ('attributes', 'detect_attributes'):
            raise ValueError('正确范例适用于大模型属性预标注')
        if self.operation == 'detect_attributes' and self.attribute_model_id is None:
            raise ValueError('请选择属性分析模型')
        if self.operation != 'detect_attributes' and self.attribute_model_id is not None:
            raise ValueError('只有检测后属性分析需要第二个模型')
        if any(not label.strip() or len(label) > 100 for label in self.labels):
            raise ValueError('裁剪目标必须为有效类别')
        self.labels = list(dict.fromkeys(label.strip() for label in self.labels))
        return self


class WorkflowAttributeTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    config: AttributeSchema


class BranchCase(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=100)
    match_mode: Literal['all', 'any'] = 'all'
    crop_labels: list[str] = Field(default_factory=list, max_length=50)
    box_labels: list[str] = Field(default_factory=list, max_length=50)
    min_boxes: StrictInt | None = Field(default=None, ge=0, le=5000)
    max_boxes: StrictInt | None = Field(default=None, ge=0, le=5000)
    attribute_key: str = Field(default='', max_length=100)
    attribute_values: list[str] = Field(default_factory=list, max_length=50)
    min_width: StrictInt | None = Field(default=None, ge=1, le=32768)
    min_height: StrictInt | None = Field(default=None, ge=1, le=32768)

    @model_validator(mode='after')
    def check_case(self):
        if self.key in ('default', 'else', '__input__'):
            raise ValueError(f'分支标识不能使用保留字 {self.key}')
        if self.min_boxes is not None and self.max_boxes is not None and self.min_boxes > self.max_boxes:
            raise ValueError('最小框数不能大于最大框数')
        has_cond = any([
            self.crop_labels, self.box_labels, self.min_boxes is not None, self.max_boxes is not None,
            bool(self.attribute_key), self.min_width is not None, self.min_height is not None,
        ])
        if not has_cond:
            raise ValueError(f'分流分支「{self.name}」至少需要配置一项匹配条件')
        return self


class BranchConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    first_match: bool = True
    branches: list[BranchCase] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_keys(self):
        keys = [b.key for b in self.branches]
        if len(set(keys)) != len(keys):
            raise ValueError('条件分流中的分支标识不能重复')
        return self


class ImageFilterConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    min_width: StrictInt | None = Field(default=None, ge=1, le=32768)
    max_width: StrictInt | None = Field(default=None, ge=1, le=32768)
    min_height: StrictInt | None = Field(default=None, ge=1, le=32768)
    max_height: StrictInt | None = Field(default=None, ge=1, le=32768)
    min_short_edge: StrictInt | None = Field(default=None, ge=1, le=32768)
    min_long_edge: StrictInt | None = Field(default=None, ge=1, le=32768)
    min_aspect_ratio: float | None = Field(default=None, gt=0, le=100)
    max_aspect_ratio: float | None = Field(default=None, gt=0, le=100)
    min_sharpness: float | None = Field(default=None, ge=0, le=100000)
    min_brightness: float | None = Field(default=None, ge=0, le=255)
    max_brightness: float | None = Field(default=None, ge=0, le=255)
    dedup_phash_distance: StrictInt | None = Field(default=None, ge=0, le=16)

    @model_validator(mode='after')
    def check_bounds(self):
        if all(getattr(self, f) is None for f in type(self).model_fields):
            raise ValueError('图像规则过滤至少需要设置一项阈值条件')
        if self.min_width is not None and self.max_width is not None and self.min_width > self.max_width:
            raise ValueError('最小宽度不能大于最大宽度')
        if self.min_height is not None and self.max_height is not None and self.min_height > self.max_height:
            raise ValueError('最小高度不能大于最大高度')
        if self.min_aspect_ratio is not None and self.max_aspect_ratio is not None and self.min_aspect_ratio > self.max_aspect_ratio:
            raise ValueError('最小宽高比不能大于最大宽高比')
        if self.min_brightness is not None and self.max_brightness is not None and self.min_brightness > self.max_brightness:
            raise ValueError('最低亮度不能大于最高亮度')
        return self


class BoxCleanConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    min_box_width: float = Field(default=0, ge=0, le=32768)
    min_box_height: float = Field(default=0, ge=0, le=32768)
    min_area_ratio: float = Field(default=0, ge=0, le=1)
    max_area_ratio: float = Field(default=1.0, gt=0, le=1)
    edge_margin_px: float = Field(default=0, ge=0, le=4096)
    keep_labels: list[str] = Field(default_factory=list, max_length=100)
    drop_labels: list[str] = Field(default_factory=list, max_length=100)
    rename_map: dict[str, str] = Field(default_factory=dict, max_length=100)
    nms_iou_threshold: float | None = Field(default=None, gt=0, lt=1)
    class_agnostic_nms: bool = False
    pass_when_empty: bool = False
    save_boxes: bool = True

    @model_validator(mode='after')
    def check_box_rules(self):
        if self.min_area_ratio >= self.max_area_ratio:
            raise ValueError('最小框面积占比必须小于最大框面积占比')
        return self


class ImageTransformConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    roi_mode: Literal['none', 'ratio', 'pixels'] = 'none'
    roi_ratio: list[float] | None = Field(default=None, min_length=4, max_length=4)
    roi_margins_px: list[StrictInt] | None = Field(default=None, min_length=4, max_length=4)
    blur_labels: list[str] = Field(default_factory=list, max_length=100)
    blur_all_boxes: bool = False
    blur_radius: StrictInt = Field(default=18, ge=2, le=60)

    @model_validator(mode='after')
    def check_transform(self):
        if self.roi_mode == 'none' and not self.blur_labels and not self.blur_all_boxes:
            raise ValueError('图像变换与脱敏节点请启用 ROI 裁剪或目标框模糊打码')
        if self.roi_mode == 'ratio':
            if not self.roi_ratio:
                raise ValueError('比例 ROI 模式需要提供 [left, top, right, bottom]')
            x1, y1, x2, y2 = self.roi_ratio
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError('ROI 比例须满足 0 <= left < right <= 1 且 0 <= top < bottom <= 1')
        if self.roi_mode == 'pixels':
            if not self.roi_margins_px or any(v < 0 or v > 16384 for v in self.roi_margins_px):
                raise ValueError('像素边距裁剪需要提供 4 个非负像素值 [左, 上, 右, 下]')
        return self


class ScriptConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=16000)
    timeout_seconds: float = Field(default=5.0, ge=0.5, le=15.0)
    save_boxes: bool = True
    save_attributes: bool = True
    save_description: bool = True

    @model_validator(mode='after')
    def check_code(self):
        validate_script(self.code)
        return self


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    node_key: str = Field(default='', max_length=64)
    title: str = Field(default='', max_length=100)
    operation: WorkflowOperationType
    model_id: UUID | None = None
    prompt: str = Field(default='', max_length=8000)
    attribute_prompt: str = Field(default='', max_length=8000)
    attribute_templates: list[WorkflowAttributeTemplate] | None = Field(default=None, min_length=1, max_length=30)
    labels: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=DEFAULT_DETECTION_CONFIDENCE, ge=0, le=1)
    expansion: float = Field(default=.2, ge=0, le=1)
    min_crop_long_edge: StrictInt = Field(default=0, ge=0, le=32768)
    save_input_boxes: bool | None = None
    auto_approve_confidence: float = Field(default=.9, ge=0, le=1)
    branch_config: BranchConfig | None = None
    img_filter_config: ImageFilterConfig | None = None
    box_clean_config: BoxCleanConfig | None = None
    img_transform_config: ImageTransformConfig | None = None
    script_config: ScriptConfig | None = None

    @model_validator(mode='after')
    def validate_operation(self):
        if self.node_key and self.node_key == '__input__':
            raise ValueError('节点标识不能使用保留字 __input__')
        if self.operation in MODEL_WORKFLOW_OPERATIONS:
            if self.model_id is None:
                raise ValueError(f'步骤「{self.title or OPERATION_TITLES[self.operation]}」需要选择模型')
        else:
            if self.model_id is not None:
                raise ValueError(f'内置或脚本步骤「{self.title or OPERATION_TITLES[self.operation]}」无需选择模型')
            required_field = {
                'branch': 'branch_config',
                'img_filter': 'img_filter_config',
                'box_clean': 'box_clean_config',
                'img_transform': 'img_transform_config',
                'script': 'script_config',
            }[self.operation]
            if getattr(self, required_field) is None:
                raise ValueError(f'请完善「{self.title or OPERATION_TITLES[self.operation]}」节点的规则或脚本配置')

        if self.attribute_prompt and self.operation != 'attributes':
            raise ValueError('属性核心提示词仅适用于目标属性分析')
        if self.attribute_templates:
            if self.operation != 'attributes':
                raise ValueError('按类别应用模板仅适用于目标属性分析')
            ids = [template.id for template in self.attribute_templates]
            labels = [label.casefold() for template in self.attribute_templates for label in template.config.target_labels]
            if len(set(ids)) != len(ids) or len(set(labels)) != len(labels):
                raise ValueError('同一类别只能配置一个属性模板')
        if self.operation == 'filter' and not 1 <= len(self.prompt) <= 2000:
            raise ValueError('请填写 1 至 2000 字的筛选条件')
        if self.operation == 'quality' and not 1 <= len(self.prompt) <= 2000:
            raise ValueError('请填写 1 至 2000 字的图片质量要求')
        if any(not label or len(label) > 100 for label in self.labels):
            raise ValueError('请输入有效检测类别')
        return self


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    source_handle: str = Field(default='default', min_length=1, max_length=64)


def resolve_workflow_dag(steps: list[WorkflowStep], edges: list[WorkflowEdge] | None):
    keyed_steps: list[tuple[str, WorkflowStep]] = []
    seen_keys: set[str] = set()
    for idx, step in enumerate(steps):
        key = step.node_key or f'step_{idx}'
        if key in seen_keys:
            raise ValueError(f'工作流节点标识重复：{key}')
        seen_keys.add(key)
        keyed_steps.append((key, step))

    by_key = dict(keyed_steps)
    if edges is None:
        resolved_edges = [
            WorkflowEdge(source=keyed_steps[i][0], target=keyed_steps[i + 1][0], source_handle='default')
            for i in range(len(keyed_steps) - 1)
        ]
    else:
        resolved_edges = list(edges)

    seen_triples: set[tuple[str, str, str]] = set()
    explicit_entries: list[str] = []
    adj: dict[str, list[tuple[str, str]]] = {k: [] for k, _ in keyed_steps}
    indegree: dict[str, int] = {k: 0 for k, _ in keyed_steps}

    for edge in resolved_edges:
        triple = (edge.source, edge.target, edge.source_handle)
        if triple in seen_triples:
            continue
        seen_triples.add(triple)
        if edge.target not in by_key:
            raise ValueError(f'工作流连线目标节点不存在：{edge.target}')
        if edge.source == '__input__':
            if edge.source_handle != 'default':
                raise ValueError('输入起点连线的 source_handle 须为 default')
            explicit_entries.append(edge.target)
            continue
        if edge.source not in by_key:
            raise ValueError(f'工作流连线源节点不存在：{edge.source}')
        if edge.source == edge.target:
            raise ValueError(f'节点 {edge.source} 不能连接自身')
        src_step = by_key[edge.source]
        if src_step.operation == 'branch':
            valid_handles = {b.key for b in src_step.branch_config.branches} | {'else'}
            if edge.source_handle not in valid_handles:
                raise ValueError(f'分流节点 {edge.source} 不存在分支句柄 {edge.source_handle}')
        elif src_step.operation != 'script' and edge.source_handle != 'default':
            raise ValueError(f'节点 {edge.source} 仅支持 default 输出连线')
        adj[edge.source].append((edge.source_handle, edge.target))
        indegree[edge.target] += 1

    order_index = {k: idx for idx, (k, _) in enumerate(keyed_steps)}
    queue = deque(sorted([k for k, deg in indegree.items() if deg == 0], key=lambda k: order_index[k]))
    topo_keys: list[str] = []
    indeg_work = dict(indegree)
    while queue:
        curr = queue.popleft()
        topo_keys.append(curr)
        next_candidates = []
        for _, nxt in adj[curr]:
            indeg_work[nxt] -= 1
            if indeg_work[nxt] == 0:
                next_candidates.append(nxt)
        for nxt in sorted(set(next_candidates), key=lambda k: order_index[k]):
            queue.append(nxt)

    if len(topo_keys) != len(keyed_steps):
        raise ValueError('工作流存在循环连线，请保持有向无环（DAG）结构')

    entry_keys = list(dict.fromkeys(explicit_entries)) if explicit_entries else [k for k in topo_keys if indegree[k] == 0]
    if not entry_keys:
        raise ValueError('工作流缺少起始入口节点')

    reachable: set[str] = set()
    bfs = deque(entry_keys)
    while bfs:
        node = bfs.popleft()
        if node in reachable:
            continue
        reachable.add(node)
        for _, nxt in adj[node]:
            if nxt not in reachable:
                bfs.append(nxt)
    if len(reachable) != len(keyed_steps):
        unreachable = [k for k in topo_keys if k not in reachable]
        raise ValueError(f'以下节点无法从输入图片到达：{", ".join(unreachable)}')

    return topo_keys, by_key, adj, entry_keys, resolved_edges


class WorkflowTemplateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=30)
    edges: list[WorkflowEdge] | None = None
    attribute_schema: AttributeSchema | None = None

    @model_validator(mode='after')
    def check_dag(self):
        resolve_workflow_dag(self.steps, self.edges)
        return self


class WorkflowCreate(WorkflowTemplateCreate):
    name: str = Field(default='批量处理', min_length=1, max_length=100)
    source_job_id: UUID | None = None
    image_kind: Literal['originals', 'crops', 'all', 'uploads'] = 'originals'
    follow_uploads: bool = False

    @model_validator(mode='after')
    def upload_source(self):
        if self.image_kind == 'uploads' and (not self.follow_uploads or self.source_job_id):
            raise ValueError('仅接收新上传图片时须开启自动接收')
        if self.follow_uploads and self.source_job_id:
            raise ValueError('当前图片来源不支持自动接收上传')
        return self


class WorkflowDryRunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=30)
    edges: list[WorkflowEdge] | None = None
    attribute_schema: AttributeSchema | None = None
    image_kind: Literal['originals', 'crops', 'all'] = 'originals'
    source_job_id: UUID | None = None
    sample_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def check_dry_run_dag(self):
        resolve_workflow_dag(self.steps, self.edges)
        return self


class WorkflowAiGenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    prompt: str = Field(min_length=2, max_length=4000)
    model_id: UUID | None = None
    current_workflow: dict | None = None


class WorkflowAiScriptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    prompt: str = Field(min_length=2, max_length=4000)
    model_id: UUID | None = None
    current_code: str = Field(default='', max_length=16000)


class MatchSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_id: UUID
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    image_id: UUID | None = None
    top_k: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def query(self):
        if (self.text is None) == (self.image_id is None):
            raise ValueError('请选择文本或图片作为查询')
        return self


class MatchJudgment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID
    judgment: Literal['correct', 'incorrect', 'unknown']
    base_version: int = Field(ge=1)


class MatchReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID
    approved: bool
    base_version: int = Field(ge=1)


class MatchReviewBatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_id: UUID
    query_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=1000)
    prompt: str = Field(default='', max_length=2000)
    review_prompt: str = Field(default='', max_length=8000)
