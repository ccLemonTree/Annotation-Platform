# AI 自动编排与可视化批量处理工作流

用于项目「批量处理」（`/annotations?project_id=<id>&prepare=1`）及租户级「工作流模板」（`/projects/workflow-templates`），支持通过自然语言由 AI 自动编排有向无环图（DAG）工作流，混排 AI 模型、免模型内置功能、条件分流与受限沙箱 Python 脚本。

## 1. 节点类型

- **AI 模型推理节点（8 类）**：`crop`（检测切图）、`detect`（目标检测筛选）、`preannotate`（目标检测预标注）、`ai_review`（AI 审核）、`filter`（大模型筛选）、`quality`（大模型图片质量分析）、`caption`（大模型描述）、`attributes`（目标属性分析）。需选择在线启用的对应模型服务。
- **内置功能节点（4 类，零模型算力消耗）**：
  - `branch`（条件分流）：按裁剪来源类别、检出框类别/数量范围、属性值匹配路由至命名分支，未命中项进入 `else` 默认兜底分支。
  - `img_filter`（图像基础规则过滤）：支持宽高、长短边、宽高比、拉普拉斯边缘方差清晰度、平均亮度及任务内 pHash 近重复汉明距离过滤。
  - `box_clean`（检测框规则清洗）：支持最小框宽高、面积占比区间、四边截断容差、保留/剔除类别、类别重命名与 NMS 去重；人工标注或已审核记录自动保护不覆盖。
  - `img_transform`（图像变换与脱敏）：支持比例或像素边距 ROI 裁剪（去水印）及目标框高斯模糊打码，结果保存为项目衍生图 `Asset`，绝不覆盖共享原图。
- **Python 安全沙箱脚本节点（`script`）**：
  - 入口函数固定为同步 `def process(ctx):`，支持读取 `ctx["image"]`（`PIL.Image` RGB）、`width`、`height`、`boxes`、`attributes`、`description`、`crop_label`，返回字典可含 `passed`、`reason`、`boxes`、`attributes`、`description`、`image` 或 `crops`、`branch`。
  - **沙箱边界**：保存与执行前通过 AST 白名单静态检查（仅放行 `math`, `json`, `re`, `statistics`, `collections`, `itertools`, `PIL`, `imagehash`；禁止私有下划线属性、`open`/`eval`/`exec` 等），运行在剥离全部宿主机环境变量（`env={}`）、禁网、禁文件写入且带单图超时保护（默认 5 秒）的受限子进程中。

## 2. 编排、试运行与兼容边界

- **自动布局画布**：画布按 DAG 层级自动对齐排版，点击连线或末端 `+` 号插入步骤或下游分支，点击节点在右侧抽屉配置参数或使用 AI 生成单节点脚本；支持切换紧凑步骤列表视图与画布缩放。
- **AI 自动编排**：使用「模型管理」中配置的文本/代码大模型（`llm`）或 OpenAI 视觉对话模型（`caption`），支持“全新生成整图”与“在当前图上修改”，生成结果经服务端 Pydantic 拓扑无环校验、真实在线 `model_id` 校验与脚本 AST 校验后返回前端，支持一键撤回。
- **样本试运行（Dry-run）**：抽取 1~3 张项目图片在内存中按拓扑顺序真实执行并返回节点耗时、输入输出张数、叠框/衍生缩略预览及脚本 `print` 日志，**零数据库业务写入**。
- **队列与导出兼容**：底层复用 `ProjectWorkJob` / `ProjectWorkItem` 持久化流式队列（通过 `workflow_routes` 驱动多分支转发），保留单节点重试、整图取消、`follow_uploads` 自动接收新上传及抓图、按任意节点 `work_job_id` 查看结果与冻结导出数据集；历史线性工作流记录无缝兼容读取。
