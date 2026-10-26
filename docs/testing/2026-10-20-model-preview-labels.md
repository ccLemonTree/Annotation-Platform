# 模型效果预览标签与参数验收

- 预览增加检测标签，回车确认，支持中英文分号或逗号分隔；初始使用模型配置类别，修改只作用于本次任务。标签与映射冻结到任务，不修改模型注册配置。
- `/detect/grounding` 的 VLX-Seek、GroundingPI 不提供检测置信度，也不执行 NMS：页面隐藏两个阈值控件并说明；服务调用不发送 conf / nms 或每类别 conf；后台不按占位分数筛选。SAM3、SAHI 保持已有不发送 NMS 的行为。
- 平台不再把未校准占位 score 返回为检测置信度；结果叠框只展示类别，表格显示“未提供”。历史预览按冻结模型识别占位分数，不再显示为 50%。普通检测模型、训练权重仍校验并显示有效分数。
- 无置信度模型不能创建依赖分数排序和阈值的指标验证任务，接口返回明确说明；上传预览可正常使用。

验证：

1. 后端 model_services、model_preview、validation、automation 共 86 项通过；适配器 5 项通过。包含缺失/null 占位分数、高阈值保留框、标签边界、配置不变、指标入口拒绝及现有队列回归。
2. 前端构建通过；此次预览相关文件 lint 无警告。ModelPrediction 原有三项 hooks 警告保留。
3. 真实浏览器使用受控响应完成普通检测、GroundingPI 标签与无效参数省略、历史占位分数隐藏、刷新恢复、窄屏、权重切换与失败保留文件验证；没有写标注或模型配置。
4. 真实公网服务 + 平台上传队列：标签 apple，conf=0.99 / nms=0.01；GroundingPI 直连及平台均 2 框，VLX 均 1 框；平台预测无 score。原图读取通过，两次临时预览均删除，Asset=4889 / Annotation=4861 / LibraryImage=4876，全部模型配置与 revision 不变。记录：`output/model-preview-grounding-live.json`。
5. 远端适配器更新前备份：`/ai/chen/deploy/grounding-models/grounding_service.py.before-preview-labels-20261020`，原 SHA256 `537ea0caba11cb3b8ec2480d29c9d1d22fa01a2919137416e31d17f55e314230`；仅重启 platform-vlx-seek / platform-groundingpi。jev-person-test 启动时间仍为 2026-10-19T03:18:36.2943841Z。

未执行数据集精度评测、置信度校准或压力测试；模型能力按当前接口契约识别，新增其他无置信度接口时需扩展该契约。
