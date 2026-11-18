import type { ModelService } from '../../api/modelServices';
import type { WorkflowEdge, WorkflowOperation, WorkflowStep } from '../../api/projectWork';
import { DEFAULT_DETECTION_CONFIDENCE } from '../../api/modelServices';

export const WORKFLOW_OPERATIONS: Record<WorkflowOperation, string> = {
  crop: '检测切图',
  detect: '目标检测筛选',
  preannotate: '目标检测预标注',
  ai_review: 'AI 审核',
  filter: '大模型筛选',
  quality: '大模型图片质量分析',
  caption: '大模型描述',
  attributes: '目标属性分析',
  branch: '条件分流',
  img_filter: '图像基础规则过滤',
  box_clean: '检测框规则清洗',
  img_transform: '图像变换与脱敏',
  script: 'Python 脚本处理',
};

export type NodeCategory = 'model' | 'builtin' | 'branch' | 'script';

export const MODEL_OPERATIONS: Set<WorkflowOperation> = new Set([
  'crop', 'detect', 'preannotate', 'ai_review', 'filter', 'quality', 'caption', 'attributes',
]);

export function getNodeCategory(op: WorkflowOperation): NodeCategory {
  if (op === 'branch') return 'branch';
  if (op === 'script') return 'script';
  if (MODEL_OPERATIONS.has(op)) return 'model';
  return 'builtin';
}

export const CATEGORY_LABELS: Record<NodeCategory, string> = {
  model: '模型推理',
  builtin: '内置功能',
  branch: '条件分流',
  script: 'Python 脚本',
};

export const NODE_MENU_GROUPS: { group: string; items: { operation: WorkflowOperation; label: string; hint: string }[] }[] = [
  {
    group: '内置功能（零代码 · 极速规则）',
    items: [
      { operation: 'img_filter', label: '图像基础规则过滤', hint: '按分辨率、清晰度、亮度或 pHash 去重筛图' },
      { operation: 'branch', label: '条件分流', hint: '按裁剪类别、目标框数或属性值路由到不同分支' },
      { operation: 'box_clean', label: '检测框规则清洗', hint: '按尺寸/面积占比、边缘截断、重命名或 NMS 清洗框' },
      { operation: 'img_transform', label: '图像变换与脱敏', hint: '固定区域 ROI 去水印或对目标框高斯模糊打码' },
    ],
  },
  {
    group: '自定义代码（安全沙箱）',
    items: [
      { operation: 'script', label: 'Python 脚本处理', hint: '操作图像像素与标注上下文，支持 AI 一句话自动写代码' },
    ],
  },
  {
    group: 'AI 模型推理',
    items: [
      { operation: 'crop', label: '检测切图', hint: '检出目标并裁剪为独立子图传递给下游' },
      { operation: 'preannotate', label: '目标检测预标注', hint: '检测目标并将框写入待审核标注' },
      { operation: 'detect', label: '目标检测筛选', hint: '仅保留检出目标的图片进入下一步' },
      { operation: 'ai_review', label: 'AI 审核', hint: '视觉大模型核验待审核正样本框' },
      { operation: 'attributes', label: '目标属性分析', hint: '按统一或分目标模板提取结构化属性' },
      { operation: 'caption', label: '大模型描述', hint: '生成图像内容自然语言描述' },
      { operation: 'filter', label: '大模型筛选', hint: '用自然语言条件筛选符合场景的图片' },
      { operation: 'quality', label: '大模型图片质量分析', hint: '检查画质合格性（不合格图从本项目删除）' },
    ],
  },
];

export const DEFAULT_PYTHON_SCRIPT = `def process(ctx):
    """处理单张图片与标注上下文（运行于无网安全沙箱）。
    ctx 字段: image(PIL.Image), width, height, boxes, attributes, description, crop_label
    """
    boxes = [
        b for b in ctx["boxes"]
        if b["width"] >= 16 and b["height"] >= 16
    ]
    return {
        "passed": True,
        "boxes": boxes,
        "reason": f"保留 {len(boxes)} 个有效目标框",
    }
`;

export const SCRIPT_SNIPPETS: { label: string; code: string }[] = [
  {
    label: '过滤边缘残缺与过小检测框',
    code: `def process(ctx):
    w, h = ctx["width"], ctx["height"]
    margin = 6
    kept = []
    for b in ctx["boxes"]:
        if b["width"] < 24 or b["height"] < 24:
            continue
        if b["x"] < margin or b["y"] < margin or (b["x"] + b["width"]) > (w - margin) or (b["y"] + b["height"]) > (h - margin):
            continue
        kept.append(b)
    return {"passed": len(kept) > 0, "boxes": kept, "reason": f"清洗后保留 {len(kept)}/{len(ctx['boxes'])} 个框"}
`,
  },
  {
    label: '图像清晰度与过暗/过曝检测',
    code: `from PIL import ImageFilter, ImageStat

def process(ctx):
    gray = ctx["image"].convert("L")
    brightness = ImageStat.Stat(gray).mean[0]
    small = gray.copy()
    small.thumbnail((512, 512))
    kernel = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    sharpness = ImageStat.Stat(small.filter(kernel)).var[0]
    ok = sharpness >= 60 and 20 <= brightness <= 240
    return {
        "passed": ok,
        "reason": f"清晰度 {sharpness:.1f} · 亮度 {brightness:.1f}",
    }
`,
  },
  {
    label: '对指定目标框高斯模糊打码生成衍生图',
    code: `from PIL import ImageFilter

def process(ctx):
    out = ctx["image"].copy()
    w, h = ctx["width"], ctx["height"]
    masked = 0
    for b in ctx["boxes"]:
        if b["label"] in ("person", "face", "plate", "car"):
            x1, y1 = max(0, int(b["x"])), max(0, int(b["y"]))
            x2, y2 = min(w, int(b["x"] + b["width"])), min(h, int(b["y"] + b["height"]))
            if x2 > x1 and y2 > y1:
                patch = out.crop((x1, y1, x2, y2)).filter(ImageFilter.GaussianBlur(radius=20))
                out.paste(patch, (x1, y1))
                masked += 1
    return {"passed": True, "image": out, "reason": f"已对 {masked} 个区域模糊脱敏"}
`,
  },
];

let keyCounter = 1;
export function nextNodeKey(existing: WorkflowStep[]): string {
  const used = new Set(existing.map(s => s.node_key));
  while (used.has(`node_${keyCounter}`)) keyCounter += 1;
  return `node_${keyCounter++}`;
}

export function createDefaultStep(operation: WorkflowOperation, existing: WorkflowStep[], models: ModelService[] = []): WorkflowStep {
  const node_key = nextNodeKey(existing);
  const title = WORKFLOW_OPERATIONS[operation];
  if (operation === 'branch') {
    return {
      node_key,
      title,
      operation,
      branch_config: {
        first_match: true,
        branches: [
          { key: 'b_person', name: '人员分支', match_mode: 'all', crop_labels: ['person', '人'] },
          { key: 'b_vehicle', name: '车辆分支', match_mode: 'all', crop_labels: ['car', 'vehicle', '车'] },
        ],
      },
    };
  }
  if (operation === 'img_filter') {
    return {
      node_key,
      title,
      operation,
      img_filter_config: { min_short_edge: 128, min_sharpness: 60, min_brightness: 15, max_brightness: 245 },
    };
  }
  if (operation === 'box_clean') {
    return {
      node_key,
      title,
      operation,
      box_clean_config: {
        min_box_width: 16,
        min_box_height: 16,
        min_area_ratio: 0.001,
        max_area_ratio: 0.98,
        edge_margin_px: 0,
        keep_labels: [],
        drop_labels: [],
        rename_map: {},
        nms_iou_threshold: 0.5,
        pass_when_empty: false,
        save_boxes: true,
      },
    };
  }
  if (operation === 'img_transform') {
    return {
      node_key,
      title,
      operation,
      img_transform_config: {
        roi_mode: 'ratio',
        roi_ratio: [0, 0.08, 1, 1],
        blur_labels: [],
        blur_all_boxes: false,
        blur_radius: 18,
      },
    };
  }
  if (operation === 'script') {
    return {
      node_key,
      title,
      operation,
      script_config: {
        code: DEFAULT_PYTHON_SCRIPT,
        timeout_seconds: 5,
        save_boxes: true,
        save_attributes: true,
        save_description: true,
      },
    };
  }

  const isDet = operation === 'crop' || operation === 'detect' || operation === 'preannotate';
  const needOpenai = operation === 'filter' || operation === 'quality' || operation === 'ai_review';
  const candidate = models.find(m =>
    m.enabled !== false &&
    m.kind === (isDet ? 'detection' : 'caption') &&
    (needOpenai ? m.protocol === 'openai_chat' : operation === 'attributes' ? ['openai_chat', 'jev_decide'].includes(m.protocol) : m.protocol !== 'jev_decide'),
  );
  return {
    node_key,
    title,
    operation,
    model_id: candidate?.id ?? undefined,
    confidence: isDet ? DEFAULT_DETECTION_CONFIDENCE : undefined,
    expansion: operation === 'crop' ? 0.2 : undefined,
    min_crop_long_edge: operation === 'crop' ? 0 : undefined,
    save_input_boxes: operation === 'crop' ? false : undefined,
    auto_approve_confidence: operation === 'ai_review' ? 0.9 : undefined,
    prompt: operation === 'filter' ? '保留主体清晰可见、符合项目目标的图片' : operation === 'quality' ? '画面清晰、主体完整无严重遮挡或模糊' : '',
  };
}

export function normalizeGraph(stepsInput: WorkflowStep[], edgesInput?: WorkflowEdge[] | null): { steps: WorkflowStep[]; edges: WorkflowEdge[] } {
  const used = new Set<string>();
  const steps = stepsInput.map((step, idx) => {
    let key = step.node_key?.trim() || `step_${idx + 1}`;
    while (used.has(key)) key = `${key}_${idx + 1}`;
    used.add(key);
    return { ...step, node_key: key, title: step.title?.trim() || WORKFLOW_OPERATIONS[step.operation] || step.operation };
  });
  const keySet = new Set(steps.map(s => s.node_key!));
  let edges: WorkflowEdge[];
  if (!edgesInput) {
    edges = steps.slice(0, -1).map((s, idx) => ({
      source: s.node_key!,
      target: steps[idx + 1].node_key!,
      source_handle: 'default',
    }));
  } else {
    edges = edgesInput.filter(e => (e.source === '__input__' || keySet.has(e.source)) && keySet.has(e.target) && e.source !== e.target);
  }
  return { steps, edges };
}

export function summarizeStep(step: WorkflowStep, models: ModelService[]): { subtitle: string; warning?: string } {
  const op = step.operation;
  if (MODEL_OPERATIONS.has(op)) {
    const model = models.find(m => m.id === step.model_id);
    if (!step.model_id) return { subtitle: '未选择推理模型', warning: '请选择模型' };
    if (!model) return { subtitle: `模型 ${step.model_id.slice(0, 8)}`, warning: '模型不可用' };
    const extra = op === 'crop'
      ? `外扩 ${Math.round((step.expansion ?? 0.2) * 100)}%`
      : ['detect', 'preannotate'].includes(op)
        ? `置信度 ${step.confidence ?? DEFAULT_DETECTION_CONFIDENCE}`
        : step.prompt
          ? step.prompt.slice(0, 22) + (step.prompt.length > 22 ? '…' : '')
          : '';
    return { subtitle: [model.name, extra].filter(Boolean).join(' · ') };
  }
  if (op === 'branch') {
    const branches = step.branch_config?.branches || [];
    return { subtitle: branches.length ? `${branches.length} 条条件分支 + 默认兜底` : '未配置分支', warning: branches.length ? undefined : '请添加条件' };
  }
  if (op === 'img_filter') {
    const cfg = step.img_filter_config || {};
    const parts: string[] = [];
    if (cfg.min_short_edge) parts.push(`短边≥${cfg.min_short_edge}`);
    if (cfg.min_long_edge) parts.push(`长边≥${cfg.min_long_edge}`);
    if (cfg.min_width || cfg.min_height) parts.push(`≥${cfg.min_width || 1}×${cfg.min_height || 1}`);
    if (cfg.min_sharpness != null) parts.push(`清晰度≥${cfg.min_sharpness}`);
    if (cfg.min_brightness != null || cfg.max_brightness != null) parts.push(`亮度 ${cfg.min_brightness ?? 0}~${cfg.max_brightness ?? 255}`);
    if (cfg.dedup_phash_distance != null) parts.push(`pHash去重≤${cfg.dedup_phash_distance}`);
    return { subtitle: parts.join(' · ') || '基础画质与尺寸规则', warning: parts.length ? undefined : '需填阈值' };
  }
  if (op === 'box_clean') {
    const cfg = step.box_clean_config || {};
    const parts: string[] = [];
    if (cfg.min_box_width || cfg.min_box_height) parts.push(`≥${cfg.min_box_width || 0}×${cfg.min_box_height || 0}px`);
    if (cfg.edge_margin_px) parts.push(`边距${cfg.edge_margin_px}px`);
    if (cfg.nms_iou_threshold) parts.push(`NMS ${cfg.nms_iou_threshold}`);
    if (cfg.rename_map && Object.keys(cfg.rename_map).length) parts.push(`重命名${Object.keys(cfg.rename_map).length}项`);
    return { subtitle: parts.join(' · ') || '检测框几何与类别清洗' };
  }
  if (op === 'img_transform') {
    const cfg = step.img_transform_config;
    const parts: string[] = [];
    if (cfg?.roi_mode === 'ratio' && cfg.roi_ratio) parts.push(`比例ROI [${cfg.roi_ratio.join(',')}]`);
    if (cfg?.roi_mode === 'pixels' && cfg.roi_margins_px) parts.push(`裁边 [${cfg.roi_margins_px.join(',')}px]`);
    if (cfg?.blur_all_boxes) parts.push('全目标模糊脱敏');
    else if (cfg?.blur_labels?.length) parts.push(`打码: ${cfg.blur_labels.join('、')}`);
    return { subtitle: parts.join(' · ') || 'ROI 裁剪与模糊脱敏' };
  }
  const code = step.script_config?.code || '';
  const lineCount = code.trim() ? code.trim().split('\n').length : 0;
  return { subtitle: lineCount ? `Python 沙箱 · ${lineCount} 行代码` : '待编写 process(ctx)', warning: lineCount ? undefined : '请编写脚本' };
}

export interface LayoutNode {
  key: string;
  isInput?: boolean;
  step?: WorkflowStep;
  stepIndex: number;
  col: number;
  x: number;
  y: number;
  width: number;
  height: number;
  handles: { id: string; label: string }[];
}

export interface LayoutEdge {
  id: string;
  source: string;
  target: string;
  source_handle: string;
  label?: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  midX: number;
  midY: number;
}

export const NODE_WIDTH = 236;
export const NODE_HEIGHT = 96;
export const COL_GAP = 84;
export const ROW_GAP = 26;
export const PAD_X = 28;
export const PAD_Y = 28;

export function computeGraphLayout(steps: WorkflowStep[], edges: WorkflowEdge[], showInputAnchor = true): {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
} {
  const byKey = new Map(steps.map((s, idx) => [s.node_key!, { step: s, idx }]));
  const indeg = new Map<string, number>(steps.map(s => [s.node_key!, 0]));
  const adj = new Map<string, WorkflowEdge[]>(steps.map(s => [s.node_key!, []]));

  for (const e of edges) {
    if (e.source !== '__input__' && byKey.has(e.source) && byKey.has(e.target)) {
      indeg.set(e.target, (indeg.get(e.target) || 0) + 1);
      adj.get(e.source)!.push(e);
    }
  }

  const explicitEntries = edges.filter(e => e.source === '__input__' && byKey.has(e.target)).map(e => e.target);
  const entryKeys = explicitEntries.length
    ? Array.from(new Set(explicitEntries))
    : steps.filter(s => (indeg.get(s.node_key!) || 0) === 0).map(s => s.node_key!);
  if (!entryKeys.length && steps.length) entryKeys.push(steps[0].node_key!);

  const colOffset = showInputAnchor ? 1 : 0;
  const colMap = new Map<string, number>();
  for (const k of entryKeys) colMap.set(k, colOffset);

  // Relax longest DAG path (bounded by steps.length to guard against accidental cycles in draft state)
  for (let pass = 0; pass < steps.length; pass++) {
    let changed = false;
    for (const s of steps) {
      const u = s.node_key!;
      const uCol = colMap.get(u) ?? colOffset;
      if (!colMap.has(u)) colMap.set(u, uCol);
      for (const e of adj.get(u) || []) {
        const nextCol = uCol + 1;
        if ((colMap.get(e.target) ?? 0) < nextCol) {
          colMap.set(e.target, nextCol);
          changed = true;
        }
      }
    }
    if (!changed) break;
  }

  const columns = new Map<number, string[]>();
  for (const s of steps) {
    const k = s.node_key!;
    const c = colMap.get(k) ?? colOffset;
    if (!columns.has(c)) columns.set(c, []);
    columns.get(c)!.push(k);
  }

  const maxRows = Math.max(1, ...Array.from(columns.values(), list => list.length));
  const contentHeight = maxRows * NODE_HEIGHT + (maxRows - 1) * ROW_GAP;
  const maxCol = Math.max(colOffset, ...Array.from(columns.keys()));
  const totalWidth = PAD_X * 2 + (maxCol + 1) * NODE_WIDTH + maxCol * COL_GAP + 44;
  const totalHeight = Math.max(210, PAD_Y * 2 + contentHeight);

  const layoutNodes: LayoutNode[] = [];
  const nodePos = new Map<string, LayoutNode>();

  if (showInputAnchor) {
    const inputNode: LayoutNode = {
      key: '__input__',
      isInput: true,
      stepIndex: -1,
      col: 0,
      x: PAD_X,
      y: Math.round((totalHeight - NODE_HEIGHT) / 2),
      width: NODE_WIDTH - 24,
      height: NODE_HEIGHT,
      handles: [{ id: 'default', label: '输入' }],
    };
    layoutNodes.push(inputNode);
    nodePos.set('__input__', inputNode);
  }

  for (const [col, keys] of Array.from(columns.entries()).sort((a, b) => a[0] - b[0])) {
    const colH = keys.length * NODE_HEIGHT + (keys.length - 1) * ROW_GAP;
    const startY = Math.round((totalHeight - colH) / 2);
    const x = PAD_X + col * (NODE_WIDTH + COL_GAP) - (showInputAnchor ? 24 : 0);
    keys.forEach((key, rowIdx) => {
      const info = byKey.get(key)!;
      const step = info.step;
      const handles = step.operation === 'branch'
        ? [
            ...(step.branch_config?.branches || []).map(b => ({ id: b.key, label: b.name })),
            { id: 'else', label: '默认/其他' },
          ]
        : [{ id: 'default', label: '输出' }];
      const ln: LayoutNode = {
        key,
        step,
        stepIndex: info.idx,
        col,
        x,
        y: startY + rowIdx * (NODE_HEIGHT + ROW_GAP),
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        handles,
      };
      layoutNodes.push(ln);
      nodePos.set(key, ln);
    });
  }

  const allVisualEdges: WorkflowEdge[] = [
    ...(showInputAnchor ? entryKeys.map(target => ({ source: '__input__', target, source_handle: 'default' })) : []),
    ...edges.filter(e => e.source !== '__input__'),
  ];

  const layoutEdges: LayoutEdge[] = [];
  for (const e of allVisualEdges) {
    const src = nodePos.get(e.source);
    const tgt = nodePos.get(e.target);
    if (!src || !tgt) continue;
    const handleIdx = Math.max(0, src.handles.findIndex(h => h.id === e.source_handle));
    const handleCount = src.handles.length;
    const yOffset = handleCount <= 1
      ? src.height / 2
      : Math.round(((handleIdx + 1) / (handleCount + 1)) * src.height);
    const x1 = src.x + src.width;
    const y1 = src.y + yOffset;
    const x2 = tgt.x;
    const y2 = tgt.y + Math.round(tgt.height / 2);
    const handleMeta = src.handles.find(h => h.id === e.source_handle);
    const label = src.step?.operation === 'branch' ? handleMeta?.label || e.source_handle : undefined;
    layoutEdges.push({
      id: `${e.source}:${e.source_handle}->${e.target}`,
      source: e.source,
      target: e.target,
      source_handle: e.source_handle,
      label,
      x1,
      y1,
      x2,
      y2,
      midX: Math.round((x1 + x2) / 2),
      midY: Math.round((y1 + y2) / 2),
    });
  }

  return { nodes: layoutNodes, edges: layoutEdges, width: totalWidth, height: totalHeight };
}
