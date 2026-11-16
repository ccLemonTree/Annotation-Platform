# JEV decide 属性标注 Implementation Plan

**Goal:** 接入用户给出的 `/v1/decide` choice 协议，复用目标属性标注、人工修正、审核与导出，保存并展示全部候选概率。

**Architecture:** 新增 `jev_decide` 模型协议，保持 caption 用途以复用现有属性模型快照。每个目标的属性依次请求，受现有项目并发限制；属性结果增加可选 `probabilities` 字典，保存在既有 JSONB，无数据库迁移。人工改值只改变标注结果，模型概率保留为只读证据。

**Tech Stack:** FastAPI / Pydantic / SQLAlchemy / httpx / React / Ant Design。

## 已确认范围及假设

- 用户已选择新增原生协议、复用属性流程、保留概率及人工修正审核导出。
- 只支持配置了候选值的 choice 属性；不假设 JEV 提供自由文本、正确范例或 OpenAI 接口。
- 取最高概率选项作待审建议；并列或选中无法判断项时存 null 和原因，不自动审核。
- 返回选项必须与请求一一对应，概率必须是 0—1 的有限数字。按返回选项配对，禁止静默 zip 截断。
- 不增加属性级并发，沿用现有租户权限、队列、错误处理、配置快照与迟到结果保护。
- 不新增依赖、不修改业务数据或已有模型配置；模型效果另需实图评估。

## 实施与验证

1. 在 `backend/backend/tests/test_jev_decide.py` 写失败测试：协议、逐属性请求、概率配对/校验、自由文本与范例拒绝。运行确认失败，再修改 `schemas/model_service.py`、`schemas/object_attributes.py`、`services/object_attributes.py` 和模型连接检查。
2. 加队列集成测试：检测切图→JEV→待审标注→人工改值→审核→JSONL/CSV导出；异常响应不保存，人工改标不丢概率且不能伪造。最小修改 `services/project_work.py`、`services/annotations.py`、`services/annotation_export.py`。
3. 修改前端协议配置、属性模型筛选、只读概率展示；普通描述入口排除 JEV。复用现有属性编辑器和结果列表，JEV 不显示范例入口。
4. 隔离测试覆盖现有 OpenAI 属性、范例、模型服务及项目队列；运行前端构建及隔离浏览器检查。更新操作说明。
5. 通过 ssh-manager 检查 115 差异和活动任务；仅同步本次差异并备份，空闲时重启后端，验证健康、页面和实际协议请求。留存验收证据，不覆盖私有配置或业务库。

执行命令（在项目根目录）：`python -m pytest backend/backend/tests/test_jev_decide.py -q`（使用后端目录 / PYTHONPATH 按现有测试约定）；前端：`npm run build`。
