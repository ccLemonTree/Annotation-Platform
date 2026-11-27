import { Input, InputNumber, Select, Switch } from 'antd';
import type { ParameterField } from '../api/training';

export const parameterLabels: Record<string, string> = {
  size: '模型尺寸', optimizer: '优化器', imgsz: '训练图像尺寸（像素）', workers: '数据加载进程数', amp: '混合精度', eval_period: '验证间隔（轮）',
  epochs: '训练轮数', batch: '总批次大小', patience: '早停耐心轮数', save_period: '保存间隔（轮）', device: '容器内GPU编号', seed: '随机种子',
  batch_size: '每卡批次大小', grad_accum_steps: '梯度累计步数', lr: '学习率', devices: '训练设备数',
  task_type: '任务类型', loss_type: '损失函数', tuner_type: '微调方式', lora_rank: 'LoRA秩', lora_alpha: 'LoRA缩放',
  torch_dtype: '计算精度', num_train_epochs: '训练轮数', per_device_train_batch_size: '每卡批次大小', gradient_accumulation_steps: '梯度累计步数',
  learning_rate: '学习率', max_length: '最大序列长度', lazy_tokenize: '延迟编码', dataloader_num_workers: '数据加载进程数',
  logging_steps: '日志间隔（步）', save_steps: '保存间隔（步）', save_total_limit: '最多保留检查点数', eval_strategy: '评估策略',
  val_dataset: '服务器验证JSONL路径', dataloader_drop_last: '丢弃不足批次', freeze_vit: '冻结视觉编码器', freeze_aligner: '冻结对齐层',
  steps: '总训练步数', backbone_freeze: '冻结DINOv3骨干', backbone_lr_factor: '骨干学习率比例', warmup_steps: '学习率预热步数', precision: '计算精度', num_workers: '数据加载进程数',
};

export function ParameterInput({ name, field, value, checked, onChange, id }: { name: string; field: ParameterField; value?: unknown; checked?: boolean; onChange?: (value: unknown) => void; id?: string }) {
  const schema = field.anyOf?.find(v => v.type !== 'null') || field;
  const control = { id, onChange };
  if (schema.const !== undefined) return <Select {...control} value={value} options={[{ value: schema.const, label: String(schema.const) }]} />;
  if (name === 'devices') return <Select {...control} value={value as string | number} options={[{ value: 'auto', label: '自动使用已分配GPU' }, ...Array.from({ length: 16 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))]} />;
  if (schema.enum) return <Select {...control} value={value} allowClear={name !== 'size'} options={schema.enum.map(option => ({ value: option, label: String(option) }))} />;
  if (schema.type === 'boolean') return <Switch {...control} checked={checked} />;
  if (schema.type === 'integer' || schema.type === 'number') return <InputNumber {...control} value={value as number} style={{ width: '100%' }} min={schema.minimum ?? schema.exclusiveMinimum} max={schema.maximum} step={schema.type === 'integer' ? 1 : name.includes('lr') || name.includes('rate') ? 0.00001 : 0.1} />;
  return <Input {...control} value={value as string} placeholder={name === 'device' ? 'auto 或 0,1' : undefined} />;
}
