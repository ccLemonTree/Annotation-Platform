# 训练模型下发实施计划

**Goal:** 将已完成训练导出/合并的模型下发到 K8s、Docker 和 Docker Kafka 环境，经校验后等待管理员手动生效，并支持恢复旧版本。

**Architecture:** 复用 ssh-manager MCP 桥接和临时文件中转，新增独立下发记录，不复用训练 GPU 占用状态。文件服务器与控制服务器分别配置；K8s 解析指定工作负载的全部 Pod，Docker 明确选择容器。模型在仓库外暂存，生效时保留旧目录；小模型逐实例加载Triton，大模型/向量重启指定容器或滚动工作负载，再核验全部实例。

**Tech Stack:** FastAPI、SQLAlchemy/PostgreSQL、Python 标准库、ssh-manager、React/Ant Design。

## 已确认的范围与决策

- 用户确认：先传输校验，随后手动点击生效；不能传完自动重启。
- 用户确认：K8s、Docker、Docker Kafka；小模型 Triton TensorRT、大模型 Triton vLLM backend、向量原生 vLLM。
- 用户确认：复用现有能力并新增独立下发记录，保留日志及旧版本，提供恢复入口。
- 不修改 Kafka topic、消费组或业务算法路由；不增加独立发布服务或任务队列。
- 默认管理员操作、租户隔离；凭据留在后端/SSH 配置，保留跳板机连接。
- 首版单次下发一个部署目标，覆盖该目标的全部指定实例；大文件后台顺序中转，页面可轮询，不占用训练 GPU 配额。
- 失败/通信中断不能标为成功；应用重启后未完成操作转为待核对，不自动重放服务变更。
- 推理配置沿用目标现有配置，或显式填写；TensorRT 校验构建/推理 GPU 名称及运行时版本。就绪核验只证明模型加载，不替代业务样本验收。

## 已检查的真实参考环境（只读）

- 绍兴 NFS：`/data/ai/chen/code/huggingface/aiseefor_small_model`，模型含 `config.pbtxt` 和 `1/model.plan`。
- 绍兴大模型：`aiseefor_vllm_model/cangqiong_4b`，`backend: "vllm"`，`1/model.json` 指向 `1/model`。
- 绍兴 K8s：`ai/nexusai-triton-prod` 与 `ai/vllm-triton-prod`；模型仓库为 NFS PVC 的 subPath，Triton explicit 模式、HTTP 18000。
- 杭州移动小模型：Docker `triton-server`，`/ai/chen/code/huggingface/aiseefor_model` 挂到 `/models`。
- 丽水 GPU2：4 个 Kafka worker 对应 4 个 Triton 容器，共享上述 Docker 模型目录。
- 绍兴算法1：向量为原生 vLLM，模型以独立只读 bind mount 挂载，使用 pooling runner。

## 实施与验证

1. 新建隔离测试：输入校验、完整权重/分片、压缩包路径/校验、暂存不触碰在线目录、备份恢复、防止旧记录覆盖新版本、全部实例核验、租户/管理员边界。先确认失败。
2. 新建 `models/model_deployment.py`、`schemas/model_deployment.py`、`services/model_deployments.py`、`services/model_deployment_executor.py` 和数据库迁移；小幅扩展现有 MCP 桥接。测试不得连接业务数据库或改变真实推理服务。
3. 新建 `api/model_deployment_routes.py` 并注册；后台任务记录阶段和各实例结果。应用退出/重启后保留不确定状态，人工重新核对。
4. 新建 `frontend/src/components/ModelDeployment.tsx` 与 API；在已完成任务详情增加模型下发页签。复用现有表单、表格、确认弹窗和错误展示。
5. 运行专项后端测试、相关导出/训练回归、前端构建/静态检查与隔离浏览器流程。真实环境只做连接、目录、挂载和运行配置读取；未经指定测试目标不下发业务模型、不加载/重启生产实例。
