# Codex接手记录

## 2026-10-26 模型下发SSH与实时日志

按用户最新约定，在本地开发、隔离测试及构建通过后更新115；无需反复读取相同的远端源码。AGENTS、README和运行说明已同步此约定。

模型下发页面、目标行和记录详情提供ssh-manager服务器终端入口，复用现有PTY/鉴权/会话限额；链接只选中目标，需点击连接。后端验证配置白名单，地址/SSH账号或管理员权限变化时关闭会话，原GPU服务器UUID入口保持兼容。

新增目标/记录只读日志API及面板，覆盖阶段记录、Docker拉取、K8s Ansible最新尝试、目标容器/所属Pod运行日志；每次请求后等待2秒刷新，可暂停/重试/切实例，关闭取消，失败保留旧内容并提示。实例与文件路径由后端保存的目标派生，限制输出、拒绝符号链接、脱敏常见凭据；不创建任务或触发服务操作。

本地相关后端170通过、2跳过（Windows符号链接权限及既有可选集成），新增浏览器7项、原批量下发浏览器流程、构建、定向lint通过；涵盖角色/租户、配置撤销、命令白名单、截断/脱敏和移动端抽屉。日志读取执行器有独立40秒MCP上限、远端命令10秒上限、最多2个并行读取；不与更新动作共用长等待。正式运行只读验收结果保存在115的`.runtime/deployment-monitoring-acceptance.json`。未执行真实下发、镜像分发或推理服务重启。详见[操作说明](模型下发.md)及[本地验收](../testing/2026-10-26-deployment-monitoring.json)。

## 2026-10-26 迁移到115服务器

运行及开发主目录已切换到`/ai/chen/code/workspace/数据管理平台`，页面`http://192.168.1.115:3000`，所有后续修改/调试/测试/构建通过ssh-manager MCP在115执行。Windows仅保留回退副本，前后端已停止。数据库原已在115，未导入覆盖；18,399个文件（11,898,973,209字节）逐文件SHA256一致，迁移前后业务表指纹一致。

远端Python3.12/Node22重建依赖，前端构建、27项后端回归、pip check、Vite配置lint通过。后端systemd仅监听127.0.0.1:18000，前端3000代理，避免占用已有向量服务8000。两个服务开机启动。保留JWT/数据库密码，已保存GPU凭据解密正常；迁移SSH桥接依赖和私有配置，115自连及绍兴A100/绍兴K8s/嘉兴K8s只读SSH均通过。真实登录态、图片内容SHA256、冻结包下载、审核筛选及项目/模型/GPU/训练/下发列表接口通过；外部浏览器登录页及局域网页面200。未执行业务审核/保存、训练、下发或镜像更新。

详情见[运行说明](115服务器开发与运行.md)和[迁移验收](../testing/2026-10-26-server115-migration.json)。回退前必须停远端平台服务并同步切换后新增文件，不能启动两个后端或恢复旧数据库覆盖新记录。

## 2026-10-26 检测工作台审核状态筛选

工作台新增全部状态/待审核/已通过/已驳回，与类别筛选取交集；复用URL review_status，列表页进入、切图、序号、刷新和返回均保持范围。按最新Annotation.review_status判断，未标注不计入待审核。资产列表、selection和neighbors复用条件；无参数保持原有响应，带参数声明支持，旧后端忽略参数时前端阻断。审核/保存后刷新位置，当前图不再符合时保留画面并提示，邻居仍限于筛选集合。

27项相关后端回归、8项新增筛选浏览器检查、18项原导航浏览器回归、构建通过；定向lint仅5条既有工作台警告。真实本地PostgreSQL只读验收：电瓶车进电梯检测待审核1264、已通过8066、已驳回0，与最新标注列表一致；bike交集分别275/11/0。已通过图片作为待审核队列外锚点正常定位；时间比较改为数据库内读取锚点，避免实际数据库带时区值与ORM声明不一致导致500。前端代理接口、422和401均验证。安全检查无后台任务后重载本地后端，业务行指纹不变；无业务标注/审核写入、迁移或新增依赖。详见[验收记录](../testing/2026-10-26-workspace-review-filter.json)。

## 2026-10-26 项目批量删除类别框

用户确认作用于当前项目全部图片中的person框。检测工作台管理员新增“批量删除类别框”：选类别、预览影响图片/框/空标注数量，再确认执行；本地有未保存草稿、绘制中或查看历史时禁用。项目范围忽略工作台标签筛选，仍遵守仅裁剪图配置。执行后刷新当前版本、历史、审核和切图位置，并清除被删除类别的筛选。

复用save_annotation建立新待审核版本，保留原图、其他框、元数据、旧审核记录、图库来源、其他项目及冻结包。预览摘要包含图片范围和受影响版本/审核/数据，先锁资产再锁项目，执行前重算；变化返回409，重复提交不能再改一遍，途中失败整笔回滚。无迁移、新依赖或任务队列。

60项相关后端回归、1项独立PostgreSQL（1280图并发双提交，16.77秒，200/409，清库验证）、8项隔离浏览器流程、构建通过；定向lint仅5条既有工作台警告。重载前无后台任务，业务行指纹保持；真实项目“电瓶车进电梯检测”只读预览：9330张中1264张含1844个person框，725张删除后为空，1264张将重新待审核。登录态预览经前端代理200、未登录401，未发任何业务删除请求。最终由用户在确认框执行。详见[验收记录](../testing/2026-10-26-project-label-removal.json)和[操作说明](检测标注操作说明.md)。

## 2026-10-26 大型YOLO压缩包条目上限

train.zip 4,992,425,636字节已收齐596片，但15,584个条目超过原10,000项上限（目录、图片、TXT分别计数），展开5,230,077,657字节未超过20GiB。默认条目上限及.env.example改为50,000；保留展开/上传容量、路径、链接和逐片SHA256检查。条目数量及展开大小分别报错，窗口显示两项上限。3项失败用例复现后，44项上传/YOLO回归、前端构建、6项浏览器回归通过；定向lint仅1条既有effect清理警告。

确认无后台任务后重载本地后端，重载前后业务表行指纹一致；通过原任务complete接口恢复，未重传任何文件字节。最终完成：小区电瓶车图片集新增7,589张，重复95张，导入标注7,585份，原标注相同跳过1份、不同冲突98条保留原来源，211份孤立标注忽略，0无效/过滤。数据库上传引用、图片集成员及已保存标注均为7,589，登录态详情接口completed/200，暂存自动清理。无迁移、新依赖或其他后台任务重放。详见[验收记录](../testing/2026-10-26-upload-archive-entry-limit.json)。

## 2026-10-26 上传分片连接中断重试

用户反馈“目标检测.zip”428.8MiB在58%时报Network Error。只读核对后确认用户续传已完成：54片、1745张图片及1745份YOLO标注进入“电瓶车进电梯”，无无效项或错误；未重新导入业务文件、未重启后端。原始网络断连原因无法从日志确定，后端分片接口无错误响应。

隔离真实浏览器复现了单个XHR连接重置导致整个队列立即中止。前端仅对失败分片的网络/超时及502/503/504等待1秒、2秒各重试一次；耗尽后保留进度并提示手动续传，暂停立即取消重试，认证及校验错误不重试。仅分片请求抑制临时全局错误提示，401原逻辑保留。6项新增浏览器情形及11项既有续传检查、41项上传/YOLO后端回归、前端构建及定向lint通过。无迁移或新增依赖，详见[验收记录](../testing/2026-10-26-upload-network-retry.json)。

## 2026-10-24 非管理员仅访问标注页面

非 `admin` 角色仅显示“标注与审核”，允许 `/annotations`、`/annotate/:assetId`、`/annotate/project/:projectId`；其他地址替换为标注项目页且不挂载管理页面。登录落点、桌面/手机导航、搜索、项目工作流与标注内部返回链接同步适配。PrivateRoute 启动时请求 `/api/auth/me`，以服务端角色创建共享身份上下文，并刷新既有 user 缓存；失败阻断，未登录回登录页。管理员原流程保留。

构建、定向 lint、18 项专项检查（含19个受限地址、四类标注和人工保存）及三组导航回归通过；只有既有 lint/构建警告。浏览器全部使用隔离 API，无业务或远端写入，无迁移/后端改动。页面访问限制不替代后端接口授权。详见[验收记录](../testing/2026-10-24-annotation-access.md)。

## 2026-10-24 模型下发中心与批量镜像更新

独立入口 `/model-deployments` 包含模型版本库、服务器版本台账、批量下发及镜像记录；训练页面链接可带入来源任务。模型归档到平台后端，包与逐文件SHA256核验后复用，目标按模型槽位或服务记录版本；每目标保留独立结果、手动生效和恢复。镜像支持 Triton/vLLM 及 NexusAI HTTP/Kafka worker，模型及镜像操作共享服务互斥。

K8s 使用各环境自己的 `preload-ai-images.yml`，控制机拉取、4台一批分发到全部实时节点，核对包SHA256和containerd摘要；同批同环境复用。生效前再次核对节点缓存，以固定摘要及 `imagePullPolicy: Never` 更新所选容器。嘉兴88节点、绍兴165节点的配置发现与真实 Ansible 语法检查通过，未执行任何预分发任务或生产变更。

迁移025已应用到本地，原业务表完整行指纹一致。专项后端、前端构建、浏览器隔离流程及真实本地只读API验收见[验收记录](../testing/model-distribution-center-2026-10-24.json)。单后端进程；未做真实生产下发或切换验收。新增节点或清理镜像缓存后须补预分发。详见[操作说明](模型下发.md)。

## 2026-10-21 训练配置保存反馈

TrainingPage为表单校验失败添加自动定位和底部独立错误提示；API错误及422详情不再被15秒列表刷新清除，参数准备纳入try/finally避免保存按钮卡住。scripts/test_training_save.cjs六项回归通过，前端构建和该页oxlint通过。运行后端日志未见用户点击对应的POST/PUT，但浏览器调试连接持续超时，尚未确认当前表单具体无效字段，也未验证真实保存点击；未创建业务任务或启动训练。Vite源码响应已核对包含修复。

## 2026-10-21 RF-DETR按本次训练数据初始化类别数

training_config.build_script在准备/筛选数据后、模型构造前调用镜像内 `RFDETR._detect_num_classes_for_training(dataset)`，显式传入Medium/2XLarge的num_classes，并输出日志；读取失败/空类别停止，不沿用90类预训练头。服务器目录、标准YAML及平台冻结标签筛选均覆盖。78项训练专项通过；绍兴RF-DETR1.9.4实际生成脚本CPU验证：服务器现有3类→[4,512]分类头，冻结YOLO和COCO筛为1类→[2,512]，train仅替换为断言探针，没有启动训练。此修复只影响随后新启动任务，不改历史权重。详见[验收](../testing/rfdetr-class-count-2026-10-21.json)。

## 2026-10-20 训练效果分析与历史模型对比

用户确认全部训练器按可用指标分析，缺少数据明确提示。新增services/training_metrics.py只读指标采集器（stdlib，通过既有ssh-manager MCP桥传送执行），services/training_analysis.py负责租户检查、指标与差值快照及已配置OpenAI对话模型请求；023新增training_analyses表，保存报告、提示词、模型revision、指标文件哈希与证据。训练详情/列表增加“效果分析与对比”，最多选3个更早completed任务，展示曲线/首次/last/各自best/差值；服务器路径不作为数据同一性证明，regular与EMA不合并。模型管理可编辑默认训练分析提示词；单次可覆盖。报告历史摘要和详情分开加载，重新打开可读最近10份；生成失败保留页面证据，不保存假报告。

87项回归、前端构建通过；lint仅原有警告。真实绍兴a100训练7已completed，读取metrics.csv 4494行68项指标、实际class_names=fire/smoke/star。真实Qwen3.8-27B调用及POST分析/GET历史/GET详情/POST证据均200，最新报告ffeb3505-8359-4408-a948-302e43f62027已保存在业务库。历史completed候选为0，跨模型差值、失败重试、已保存重开使用浏览器拦截和隔离DB测试；真实浏览器展示报告/曲线/提示词且390px适配。未启动或停止远端训练/验证，没有重新运行独立测试集。

索引：[接口与操作](../api/训练效果分析.md)、[验收](../testing/training-analysis-2026-10-20.json)。后端已重载、数据库023；操作前重查活动验证与监听进程。私有.backups/training-analysis/live.py的analyze/analyze-api会调用大模型并保存真实报告；inventory/evidence只读，api只读及证据采集。不要输出私有browser-auth.js或模型密钥。

## 2026-10-19 GPU资源页手动停止与释放

用户选择支持具体占用进程或任务，核对后停止释放。GPU页增加 GpuReleaseWorkspace：平台任务表“停止并释放”、进程表“释放显存”，先预览后显式确认，展示服务器/目标/全部受影响GPU；普通进程可选择正常退出超时后强制结束，Docker停止整个容器。原监控组件新增可选操作回调及刷新触发，既有只读调用保持。连接“只读”文案明确为“配置只读”，顶部功能搜索包含释放关键词。

新API在 /api/gpu-resources/servers/{server_id}/release 下：GET tasks、POST preview/confirm；default及已保存连接均可，仅管理员，固定参数、不接受命令。确认签名120秒有效，绑定用户、租户、服务器名称/版本、任务执行或进程启动/主机boot身份。通过既有 training_mcp_bridge 新增固定 gpu_release_executor，全部远端执行经ssh-manager，密码仅stdin且不写确认信息。raw Linux进程使用pidfd防PID复用，SIGTERM等8秒，只有force才SIGKILL；Docker用完整ID停止并核验。Kubernetes/其他未识别容器运行时拒绝直接杀内部进程，MIG/图形/GPU reset不支持。

平台容器标签解析为同租户训练或验证/预览任务，禁止绕过任务状态与预留。训练复用stop，验证/预览复用cancel_run；远端未确认保留预留。终态遗留hold必须远端停止/不存在证明再释放。日志记录操作者及结果；确认结束/失败均使采集缓存失效，刷新列表。default monitor.invalidate在线程中等锁，避免阻塞异步事件循环。

181项后端回归及11项原前端组件检查通过；前端构建通过、lint仅已有警告；真实LAN浏览器拦截停止请求，验证取消/失败/强制选项/刷新/390px。绍兴A100经ssh-manager实测自建两个CPU子进程SIGTERM(-15)与忽略TERM后force(-9)，pidfd有效并回收子进程；未停止业务GPU进程，也未测真实GPU显存分配回收。实际API显示4卡3个计算进程，两个训练进程映射detr1016烟火训练7；平台task及进程预览成功，未发送任何线上confirm。实际浏览器核对训练7确认窗后取消，训练仍running。证据索引 docs/testing/gpu-release-2026-10-19.json、output/gpu-release-*.txt/json、output/playwright/gpu-release-live-confirm.png。

重启前无活动SmartEye/启用调度/活动或占用待核对验证；后端更新已加载，启动器83648、监听91044，前端35980，操作前重查。私有只读检查工具 .backups/gpu-release/live.py 只允许GET或preview、禁止confirm，认证与令牌不应输出。

## 2026-10-19 DINOv3 LTDETR监督检测微调

用户确认使用`dinov3/vits16-ltdetr-coco`，镜像明确为`lightly/train:latest`。新增`dinov3_vits16_ltdetr`训练器，复用已有训练记录、ssh-manager、GPU hold、只读输入、冻结ZIP/图片哈希校验、日志与产物下载，无数据库迁移。类型化steps/batch/梯度累积/lr/骨干学习率/冻结/预热/保存/精度/加载进程/seed；默认1000步、总batch4、预热100、每100步保存。总batch须整除所选GPU，预热小于总步数。完整检测模型通过`model=本地权重路径`加载，调用公开`train_object_detection`，不调用pretrain、不联网下载。

YOLO/COCO冻结版本沿用既有解包/类别过滤，在内存生成Lightly data，仅传train/val，test保留但不参与训练/选模型。服务器数据支持names/train/val的YOLO YAML或Roboflow式train/valid（或val）COCO。CPU无GPU、禁网、只读权重预检查核对镜像公开API、完整权重可加载和vits16-ltdetr型号，显示Lightly版本；探测固定已检查镜像digest。仅骨干权重或其他型号会拒绝。

同步CheckpointInput、checkpoint_predict、共享CheckpointFields和预览来源文案，支持DINOv3完整导出权重的图片效果预览/模型验证；类别从权重读取（支持稀疏类别ID），手填则核对，不改YOLO/RF-DETR行为。未自动部署检测服务。原始100张电瓶车图还需先框标注/审核；没创建用户训练任务。

156项相关后端测试通过，1项因Windows无符号链接权限跳过；前端构建通过，lint44项既有警告且改动页面/组件无警告。Playwright隔离API验证创建、400重试保留表单、COCO/类别选择、修改步数/冻结、390px及DINOv3导出权重预览选择，零pageerror且未触发start/precheck/stop或真实业务写入。浏览器脚本需先生成output/dinov3-catalog.json（从实际catalog导出）。验证脚本规避AntD加载图标退场期间accessible name带loading的问题；不改产品动画/轮询。

本机后端已重载：启动器55728、监听69728，127.0.0.1:8000；前端35980继续3000（操作前重查）。实际OpenAPI和鉴权catalog包含新训练器，health200。重载前后images/image_collections/assets/annotations/export_snapshots/training_runs/training_gpu_holds主键集合哈希一致，既有1个running训练/2项GPU hold保留；未调用远端stop或新增训练容器。证据：output/dinov3-runtime-{before,after}.json、output/dinov3-browser-verification.txt、output/playwright/dinov3-*.png、docs/testing/dinov3-training-2026-10-19.json。

**未验收**：未进入远端准备/拉取Lightly镜像或下载完整权重、未运行真实GPU训练/真实Lightly权重推理/电瓶车精度验证。官方权重URL仅HEAD200。镜像/完整权重手动准备、SHA256及操作步骤见[模型训练](模型训练.md)。平台FastAPI环境无torch/lightly_train，依赖仅在远端镜像中，不要装入本机后端。没有commit或新增数据库迁移。

## 2026-10-19 同步 SmartEye 台账预警数据库下载

最终删除重复移入的11项既有测试后，保留79项专项回归全部通过；完整验收索引为 docs/testing/smarteye-ledger-sync-2026-10-19.json。测试失败遗留的单个 jobs.db 和空目录已按明确文件路径清理。

用户要求同步 SmartEye 原项目台账下载。读取本机 F:/hzxf/auto-img-downloader，干净 HEAD=851364d，平台嵌入基线 ebee9a9。同步 ledger_alarm_source（使用嵌入 get_env 命名空间）、ledger_image_downloader、ledger_watch_downloader、创建监听接口固定 alarm/15秒、LedgerSchedulePage 说明/表单、image_dedup 保留组数统计，并增加/安装 PyMySQL>=1.1,<2。通过三方合并保留 ContextExecutor、DurableTasks、tracked_run、临时原图 associate=False、平台图片集归属、共享入库及监听上下文/持久状态。未修改原项目、平台凭据或调度配置表结构。

私有 .env.smarteye 仅合并缺失的 LEDGER_ALARM_DATABASES 平台项（绍兴749153097988702341、嘉兴756583820907511836），已有值优先，修改前备份到 .backups/smarteye-sync/env-smarteye.before；禁止输出配置或私有认证。查询后关闭 MySQL 连接，预警结果不持久化；按数据库类型权威匹配±15秒台账最近原图，同名串行、逐预警对账、下载和去重文件数分开。定时/监听使用配置间隔作为时间窗口并扣除耗时等待，沿用源项目超时后可能查询空档的限制。

后端专项测试、前端构建及真实LAN页面验收见 output/smarteye-sync-*.txt、scripts/verify_smarteye_ledger_sync_ui.cjs。实际查询绍兴和嘉兴数据库，分别抽1条真实预警完成真实平台登录/台账匹配/原图HTTP下载并严格解码；使用独立临时SQLite和文件，验证结束移除成功夹具，没有写入业务图库、标注或新增调度。证据 output/smarteye-live-roundtrip.json。浏览器对创建调度/监听请求拦截，不启动业务下载；检查固定偏移、仅alarm来源、查询范围标签及对账消息。平台主导航通过SPA进入 /smarteye 可用，开发服务器直接GET /smarteye 会命中同名目录的 index.tsx；本轮UI验收用既有等价入口 /detection-query，未修改Vite路由。

后端重启前确认无活动SmartEye任务、启用调度和活动/占用待确认验证；更新已加载，启动器5516、监听47300，前端35980，操作前重查。未验证长时间持续调度或全部平台；本轮没有启动训练/推理容器。

## 2026-10-19 预览可选择运行中任务的 checkpoint

用户反馈权重列表只有失败任务。实际 /api/training/runs 返回 detr1016烟火训练7（053025f1-a981-43f2-a5cd-57f116045d09）running，产物接口正常返回 checkpoint_best_ema.pth 507931474字节等权重；截图训练6只有 metrics.csv。根因 CheckpointFields 只列终态任务，checkpoint_validation.snapshot 同时拒绝活动任务。

ModelPreviewPage 向共享 CheckpointFields 传 preview，预览列所有非草稿检测任务，中文状态；增加刷新权重列表、无权重提示/切换服务器路径、刷新清除不存在的选中权重。snapshot 新增内部关键字 preview=False，仅预览调用传True；活动任务允许读已保存权重，正式验证保持结束限制，草稿仍拒绝，租户/路径/服务器/模型/哈希和GPU占用校验保持。

43项后端测试通过（含8种状态的预览/正式验证区别及越界路径/模型/服务器拒绝），前端构建通过，修改组件无lint警告。浏览器模拟接口检查运行任务选择、CPU/类别/训练ID/路径提交、刷新失效选择和失败任务空态；原预览流程回归通过。真实LAN页面已选训练7及checkpoint_best_ema.pth并刷新成功；只读snapshot探测固定SHA256成功。未新启推理/训练容器，远端训练仍true、启动时间2026-10-16T15:01:00.635457099Z、重启0。证据output/checkpoint-picker-*.txt，截图output/playwright/checkpoint-picker-live.png，回归脚本scripts/verify_checkpoint_picker_ui.cjs。未重跑真实模型推理。后端重启前无活动/占用待确认验证，启动器10960、监听86688，前端35980；操作前重查。

## 2026-10-19 预览支持压缩包、文件夹与5000图

用户追加要求压缩包/文件夹上传，明确图片上限5000并要求提高100MiB总量。最终按平台MAX_ARCHIVE_UPLOAD_SIZE=10GiB、MAX_ARCHIVE_EXPANDED_SIZE=20GiB、MAX_UPLOAD_SIZE=100MiB，MAX_IMAGES=5000；新增GET /api/model-previews/config供前端读取统一限制。文件夹保留webkitRelativePath、同名不同目录分别展示；ZIP/TAR/TAR.GZ/TGZ只取图片、忽略其他文件。路径过长>255提示缩短目录名，不把文件名用于落盘路径。

POST预览改为限额ASGI接收+Request.form(max_files=5000,max_fields=1)，避免Starlette默认1000文件限制。标准multipart流式溢出写临时盘，不整包read；请求超限用MultiPartException触发解析器关闭临时句柄，并返回413；客户端断开也关闭解析过程。文件总大小严格按实际文件size核对。预览归档预检完整路径/特殊文件/加密/条目/展开量和图片数量后，复用image_uploads导入迭代器；open_archive新增接收已打开的spooled file、每次seek0，旧Path调用行为保持。import_items默认仍全量安全检查，预览对已检查且不可变的上传显式checked=True，避免大型gzip重复扫描；所有线程读取在取消后等待读取结束再关句柄。失败删除本次已写私有图片并回滚，无半批记录，无图库/标注/分类/描述任务。

GET预览详情新增page/page_size（默认20），只查询当前页预测；前端URL保留页码，避免5000条完整结果反复返回或全部渲染。选择文件列表前50条摘要；进度显示上传和检查阶段，客户端请求无总超时、可停止；中断仍需整次重传，有shortcut注释。新增可见文件夹按钮、明确格式与数量/大小提示。开始按钮加稳定aria-label，避免AntD loading图标改变可访问名称。

89项相关后端回归通过；包括真实multipart 5000文件创建与第250页读取，ZIP/TAR/gzip、非法路径、软链接、损坏、空包、超量、单图超限与失败回滚。前端构建通过、lint无新增警告；真实LAN浏览器目录选择保留相对路径、略过TXT，5000结果模拟仅渲染20项并恢复第2页，ZIP/TAR.GZ输入、进度和停止请求通过。真实YOLOv26接口成功处理ZIP、TAR.GZ和文件夹共6张自建图，另一个105906614字节（约101MiB）ZIP成功上传/解包/推理，证明原100MiB门槛已解除。真实浏览器只读打开四种预览结果成功。全部4个自建预览/临时图已清理，原图库/项目图/标注/图片集/验证集/任务ID集合保持；大测试ZIP也删除。原训练容器仍Running=true、启动时间2026-10-16T15:01:00.635457099Z、RestartCount=0。

证据output/model-preview-archives-*.txt、output/preview-archive-folder-ui.txt、output/preview-archives-*.txt和docs/testing/preview-archives-2026-10-19.json。私有夹具/认证在backend/backend/.backups/preview-archives与.backups/model-preview，禁止输出认证。后端启动器40264/监听75432，前端35980，操作前重查。未实传10GiB上传/20GiB展开极限；未做5000次真实模型推理；本轮未重测GPU权重推理或RAR。

## 2026-10-19 上传图片查看模型效果

用户要求独立上传图片调用模型、在图上看检测效果，无需标注、描述或分类；明确两种来源均需支持：已配置检测接口和服务器YOLO/RF-DETR训练权重。新增/model-preview路由和侧栏“模型效果预览”，模型管理/验证/训练有相关入口，使用现有AntD和钴蓝主题。1至20图，合计100MiB；参数置信度/NMS，逐图切换、叠框/原图、刷新恢复、错误重试、取消和删除自己的预览。权重参数复用CheckpointFields，CPU默认、GPU占用检查与模型2XLarge许可要求保留。

后端/api/model-previews复用ValidationSet（project_id=None，source.kind=upload_preview）、ValidationRun（snapshot.preview=True）、ValidationItem与现有单消费者队列及checkpoint执行器，无新表、迁移或依赖。预览不产生truth，不运行指标聚合或误检判断，metrics仅coverage，常规验证集/任务列表和旧任务API隔离预览。普通用户可调用启用的检测接口；权重启动仅管理员；预览GET/图片/重试/取消/删除限定创建者+租户。原图独立previews/{tenant}/{run}/{uuid}.img，校验复用从read_upload抽出的validate_image_upload（旧图库导入行为保持），处理EXIF旋转，创建失败清理私有文件。删除前取消、确认远端停止并重新加锁，停止未知时保留记录/占用；不会清理图库、项目、标注或权重文件。图片叠框字号随图宽缩放，避免小图文字过大；隐藏file input覆盖AntD表单默认display规则。

78项相关测试通过（预览/验证/权重/分片/模型接口/图库）、前端构建通过、lint无新增警告。LAN浏览器mock验证两类来源、叠框几何/置信度/开关、空结果、刷新恢复、失败保留选图和390px。真实YOLOv26接口曾一次上游500，重试成功；用户给出绍兴A100 /ai/chen/training/runs/rfdetr-20261016-225905/053025f1-a981-43f2-a5cd-57f116045d09/train/checkpoint_best_ema.pth 后，按原训练rfdetr_2xlarge、fire/smoke/star、已接受许可证，CPU独立容器真实推理完成并停止。合成640x480图片，零检测，仅证明推理链路不评价准确率。另一个基础预训练.pth因缺args无法from_checkpoint，按失败处理，未改加载器契约。真实LAN页面验证两种已完成结果均可显示原图。

清理了全部自建3个预览及其私有图，原有图库/项目图/标注/图片集/验证集/任务ID集合保持。经ssh-manager逐个核对owner.json和Docker归属标签、停止状态后，删除仅2个自建CPU容器及/ai/chen/validation/runs/{execution}临时目录。既有platform-train-053025f1-a981-43f2-a5cd-57f116045d09保持2026-10-16T15:01:00.635457099Z启动时间、重启0、Running=true，未占其GPU或改权重。

证据output/model-preview-*.txt、output/playwright/model-preview-*.png和docs/testing/model-preview-2026-10-19.json；私有联调工具与认证在backend/backend/.backups/model-preview，不得输出认证内容。后端启动器71944/监听62036，前端35980；LAN页面及后端健康200，操作前重查PID。未执行真实YOLO本地权重或GPU权重推理，YOLO分支沿用已有执行器并由回归覆盖。未支持ZIP/视频。

## 2026-10-19 LAN标注入口randomUUID崩溃

用户从项目prepare点击“标注 / 审核”，DetectionWorkspace挂载WorkActivityTracker时crypto.randomUUID在LAN HTTP不可用，React Router显示Unexpected Application Error。源代码仅此一处randomUUID调用；改用crypto.getRandomValues生成16字节、设置UUID v4版本/variant位并格式化，既有session_id、sequence、重试和计时流程保持，无依赖或后端改动。

真实LAN浏览器先复现异常（output/lan-review-uuid-red.txt），修复后同一项目入口可正常打开；确认secure=false、randomUUID=undefined、getRandomValues=function，两种计时均发出不同标准v4 UUID。所有计时写请求拦截，未写入业务计时/标注/审核；未执行模型推理。scripts/verify_lan_review_uuid.cjs和output/lan-review-uuid-ui.txt为回归证据，失败也先离开工作台再撤销拦截。相关后端tests/test_annotation_analytics.py 21项通过、前端构建通过，证据output/lan-review-*.txt。前端热更新已验证，无后端重启。

## 2026-10-19 局域网上传与空入库结果

LAN HTTP没有crypto.subtle，原前端直接报错；uploadResumable改用utils/sha256，优先Web Crypto、否则按同样SHA-256算法计算既有8MiB分片，保持后端严格校验、断点续传和去重协议，无新增依赖。42组Node原生对照通过（包含无crypto、无subtle、填充边界、8MiB）；真实LAN浏览器确认非安全上下文且无subtle，8MiB结果与localhost原生一致。

只读查明1019弥漫烟雾测试的D1779350685101(1).zip和D1779351288822.zip分别93/94张全部因min_long_edge=720过滤，imported=0、invalid=0。completed仅代表处理结束。上传API响应新增实际min_long_edge；历史持久显示新增/已有/过滤/无效/忽略、YOLO统计及错误，零入库显示警告及恢复步骤，增加“查看图片集”清空其他筛选并进入目标集。没有修改业务图片集720规则或重导业务ZIP；用户需本次阈值填0后重新选择文件，新的有效阈值产生不同续传标识，不会复用旧零入库结果。

42项后端测试通过（分片/YOLO/图片集删除），前端构建通过、lint仅既有警告；四组浏览器检查包含LAN校验/历史、续传、关闭生命周期、目标图片集。真实LAN ZIP（自建唯一内容大小图各一张、分类关闭）720时新增1过滤1，0时新增1重复1，两图可见；清理自有测试集/图后既有Image/Collection/Asset/Annotation/ExportSnapshot ID集合保持。192.168.1.115经ssh-manager请求LAN登录页200。证据output/lan-upload-*.txt、docs/testing/lan-upload-2026-10-19.json。私有认证和自有夹具工具在backend/backend/.backups/lan-upload，禁止输出认证文件。

后端已重载：启动器82624、监听36476；前端35980仍0.0.0.0:3000，入口http://192.168.1.90:3000。操作前重查PID。未验证用户另一台终端重传原ZIP或多GiB包的完整传输。

## 2026-10-19 图片集按文件夹删除

用户明确选择“删除本图片集及独占图片，共享图片保留”。图片管理选择图片集后显示删除按钮；确认前GET /api/images/collections/{id}/delete-preview核对总数、预计删除独占数、保留共享数。DELETE同路径删除图片集和仅属于当前集且未被任何项目Asset引用的LibraryImage，保留其他集/项目使用的图片、标注和冻结包；返回实际删除/保留数。未完成上传（含失败可续传）或此集快照待处理/正在分类时409，提示先完成或清除任务。删除完成上传记录/成员表通过既有FK级联；不删除原始文件，沿用平台存储保留策略，无迁移。

删除按collection→image排序加锁，assign_collection和场景分类排队统一先锁collection，避免与删除反向锁序；实际SQL再次检查其他图片集和项目引用，预览后新附加项目也保留。所有图片条件加tenant隔离。UI成功清除当前图片集筛选、跨页选中项及旧上传集记忆，更新列表；错误留弹窗可重新核对，390px适配。项目图片页不显示图片集删除。

57项后端回归通过，覆盖图片集/归集/入库/合并/原图库/分片上传/YOLO导入，构建通过、lint44既有警告。4组浏览器mock通过取消零写入、数量预览、409重试、独占消失/共享保留、筛选重置与小屏；真实PostgreSQL/API自建3图2集1项目/标注，删除1独占、保留2共享及标注；测试清理后既有Image/Collection/Asset/Annotation/ExportSnapshot ID集合保持。真实浏览器只读核对按钮/预览/取消，无业务写请求。证据output/collection-delete-*.txt，验收docs/testing/collection-delete-2026-10-19.json。私有自建夹具脚本在backend/backend/.backups/collection-delete，禁止输出认证内容。

本机已重载后端（启动器85016/监听85680）和前端35980，仍127.0.0.1:8000及0.0.0.0:3000，LAN入口192.168.1.90:3000返回200；未操作现有训练/标注或业务图片集。操作前重查PID/IP。

## 2026-10-19 恢复服务与局域网访问

恢复时3000/8000均无监听。后端使用项目虚拟环境backend/backend/.venv/Scripts/python.exe运行uvicorn，工作目录backend/backend，保持127.0.0.1:8000；启动器67812、监听83024。前端Vite配置增加host=0.0.0.0、strictPort=true，端口3000，当前进程9112。当前以太网IP192.168.1.90，局域网入口http://192.168.1.90:3000，API/WebSocket沿用同源代理。未修改防火墙、业务数据或训练配置，也未配置开机自启。

验证后端/health 200；本机localhost与192.168.1.90首页、认证/api/auth/me、项目列表均200，证据output/runtime/lan-verification-20261019.json（只读、不含令牌）。Playwright通过LAN地址显示登录页面；通过ssh-manager从本地测试服务器192.168.1.115请求LAN首页200、无令牌API401。启动日志output/runtime/*20261019*.log，私有认证检查脚本在backend/backend/.backups/navigation/verify_lan.py，不要输出认证文件。当前本机HTTP代理会将部分LAN请求返回502，运维验证需curl --noproxy '*'或httpx trust_env=False；实际浏览器与远端直连正常。操作前重查PID与IP。

## 2026-10-18 平台交互与跳转

只读审查14个主要页面；MainLayout增加按功能/任务搜索、关联入口、准确页面标题和document.title；验证集菜单改为模型验证，旧图库历史标题和所属菜单修正。项目共享ProjectWorkflow：图片→准备→标注→审核→导出训练。新增/projects/:projectId/prepare和/deliver，复用原ProjectAnnotationTools/ProjectExport/ImageAnnotationExport；审核页移除准备表单，项目页和标注项目卡增加直达入口。准备组件用Tabs展示处理/后台队列，提交后自动展示队列。项目图片过滤为空时给出切图/添加入口；继续标注空范围可前往准备。

VectorAnnotation模式/历史查询ID/历史页码在URL，编辑图片后返回保留；ImageAnnotationWorkspace上一张/下一张保留审核来源和筛选、返回恢复列表，useBlocker继续拦截未保存修改。向量判定显示自动保存与下一步审核入口。训练项目链接预选平台来源/项目/适用训练器；加载完整项目列表。验证创建预选检测项目；自动标注从URL筛项目；模型配置保留返回准备链接。训练详情和SSH/GPU补对应服务器/验证跳转。概览可进入最近各类型项目，向量项目明确统计不含匹配判定。旧图库队列链接修复为真实历史页。

未改后端、数据库、训练执行、导出审核规则和删除规则。新流程浏览器隔离mock验证含队列切换、模型配置往返、浏览器前后退、未保存保护、项目预选、7页390px和普通用户权限；单图审核来源和既有AI/JSON另有回归脚本。真实页面只读、无业务写请求。验收见docs/testing/platform-navigation-2026-10-18.json；使用方式见[平台操作导航](平台操作导航.md)。本机Vite缓存曾返回旧模块，已仅重启前端为37936，端口3000；后端65324未重启（操作前重查）。私有只读认证在backend/backend/.backups/navigation，禁止输出。

## 2026-10-18 检索匹配大模型审核与 JSON 展示

新增 services/match_review.py，POST /api/projects/{id}/matches/review-jobs；管理员选 OpenAI 视觉模型，对项目全部待审或本页查询排队。复用 ProjectWorkJob/Item，operation=match_review，每查询一个 job 保持既有 job/asset 唯一约束，无迁移。模型独立接收查询/候选图，不发人工标签/分数；以图查询两张独立图片。严格 JSON judgment/reason，双方判正确或双方判错均自动 approved；分歧/uncertain/请求失败写 pending + ai_review.needs_review，人工队列可筛选。失败可重试，记录完整模型/修订/哈希/理由。独立 per-hit 人工/审核基线防并发覆盖，同查询其他项完成不影响本项；取消/改标/审核/模型/图片/参考图/范围变化拦旧结果。人工改标清 AI，人工复核标 resolved。

核心提示词 ChatPrompts.match_review 在模型管理可编辑，旧配置/快照通过默认字段兼容；本批补充要求在审核页填写。MatchReviews 自动轮询展示进度/结论/原因/待人工复核。默认匹配导出要求 approved；显式包含普通 pending 可兼容未审核数据，但 ai_review.needs_review 必须人工批准后才允许导出。原冻结快照不改写。后台队列补 operation 名和结果原因。

JsonView/JsonInput 统一两空格、等宽、内部滚动/限高；训练配置与 JSON 字符串检查、自动审核配置/证据/结果、导出分布/冻结证据、标注历史、模型返回及匹配证据已替换，映射编辑器失焦格式化、无效内容保留。网络传输和下载 JSON/JSONL 不改。

浏览器 mock 覆盖排队、错误重试、轮询、两类一致、分歧筛选/人工解决、完整 JSON、模型提示词/JSON 编辑器及 390px；真实 Qwen 对自建黑/白/蓝三图分别验证正确一致通过、错误一致通过、分歧待人工，分歧未进训练包。真实浏览器读取 2 通过/1 复核和格式化证据，无写请求。临时项目/3 文件/1 job 已清理；40 表业务行/旧裁剪 SHA 保持、原 running 训练 1 个。构建通过/lint44既有警告。启动器74232/监听65324（操作前重查）。私有备份/认证 .backups/match-ai-review，禁止输出。未对业务项目批量跑 AI、未操作训练；合成颜色验证不代表人员属性识别精度。详见[验收](../testing/match-ai-review-json-2026-10-18.json)。

## 2026-10-18 平台标注用于 Swift/WeMM 训练、匹配判定审核

创建任务的 Swift/WeMM 原前后端均禁止 snapshot。新增平台标注冻结入口 POST /api/export/projects/{id}/annotation-snapshots，复用现有图片包/ExportSnapshot/存储和 SHA256 传输；训练配置按 task_type/loss_type 校验 SFT/四种 embedding 格式，运行路径 /output/dataset/train.jsonl，解包校验图片并改写三个独立图片字段为容器绝对路径。训练 UI 可选项目/配对来源/新冻结或已有版本，编辑恢复固定版本；服务器路径和检测训练行为保留。无迁移，仍 022。

向量审核页原只查 Annotation，查询正确/错误在 MatchQuery.hits，故已判定仍显示无标注。新增 MatchReviews 显示每页最多 20 条查询的关系/审核统计及通过/驳回，旧关系视作待审；原图片统计改为明确描述/分类口径。POST /api/projects/{id}/matches/{qid}/review 复用判定锁/租户/图片范围/哈希/版本检查，审核信息存 hits、留事件；改判重置审核。驳回项排除新导出，待审人工判定仍保持可训练规则；已冻结包不变。同 anchor 对同图存在正负冲突时 Swift 匹配训练导出/冻结拒绝，不擅自覆盖历史判定。通用配对来源含审核信息。

130 项后端、4 组浏览器通过；构建通过、lint 44 条既有警告。真实本机临时 3 张 PNG 项目中 2 条旧匹配通过审核、无图片 Annotation，冻结 1 条 InfoNCE 并创建 draft，无 GPU hold；ZIP SHA/解码通过。真实浏览器展示审核结果并恢复实际快照的任务编辑（只读，GPU/服务器元数据拦截避免 SSH）。临时项目/3 文件/1 ZIP/1 draft 清理；业务行和旧裁剪 SHA 不变、原 running 训练 1 个。启动器 70228/监听 75552，操作前重查；私有证据 .backups/platform-training-review，禁止打印认证或数据库快照。无远端传输或 GPU 训练验收。详见[验收](../testing/platform-training-match-review-2026-10-18.json)。

## 2026-10-18 独立删除全部图片入口（无需勾选/不限页）

保留原勾选批量操作，另加独立删除全部按钮：准备区按操作/来源/标注范围、描述列表按标注范围、项目图片页按来源筛选、匹配结果按当前已返回结果。GET /api/assets/selection 位于动态 UUID 路由之前，与列表共用 asset_conditions，返回所有匹配 ID，不使用分页。确认框显示范围和真实总数，复用既有批删 API，按确认快照删除（不包含之后新增图）。空范围不可确认，读取/删除错误可重试。统一 120 秒批删请求超时。不删业务文件或 Milvus 数据。

60 项后端回归通过（含 25 张裁剪图的范围、鉴权、全量删除）；4 组浏览器验证覆盖零勾选、三列表、取消/失败重试、390px 和匹配范围。真实浏览器自建 21 张 PNG 裁剪图及 1 张原图关联：页面 20 行、零勾选，单次请求删除 21 张；原图 200、项目 total=1，裁剪队列及标注和历史结果清空。临时项目及 21 个自建文件已清理，业务行/旧裁剪 SHA 核对保持，原 1 个 running 训练保留。构建通过；lint 44 条既有警告。022 无需新迁移；已重载本机后端，启动器 78940/监听 44872（操作前重查）。私有备份/认证在 backend/backend/.backups/delete-all，禁止打印。详见[验收](../testing/delete-all-images-2026-10-18.json)。

## 2026-10-18 多选图片批量删除

用户要求删除全部已选图片，不能逐张操作。前端新增DeleteSelectedImages，共享确认/错误重试，复用既有POST /api/batch/assets/delete，一次请求发送所有显式选择ID。准备区既有选中项、描述列表新复选框、项目图片页新复选框支持跨页保留，批量成功后清空/回首屏/刷新；匹配结果新卡片复选框和「全选本次结果」，成功后同时移除当前与历史可见hits。切换项目/图片来源/标注范围/当前查询清空对应选择；逐张删除也移除相应选择。操作只删项目Asset及标注，图片库原图/其他项目/物理文件/冻结数据保留。选中的原图项目关联也可删除，弹窗明确按选择操作。不新增「整个筛选结果全部选择」或分块后台删除。

无后端生产代码、数据库或训练变更，不重启服务。浏览器red复现缺少批量按钮；45项后端基线/批量重复ID计数、单删、图片库回归通过，4组专用浏览器验证三处跨页、取消、匹配全选、失败重试/390px，14组既有多模态浏览器通过。构建通过，lint44条既有警告。真实浏览器在自建临时项目一次POST删除2张裁剪图，二者404、原图200、total1/annotated0/reviewed0、历史hits清空；取消无请求。临时项目已清理，业务行及借用图片SHA核对保持，原1个running训练保留。私有快照/认证/验收在backend/backend/.backups/batch-delete（禁止打印）；详见[验收](../testing/batch-image-deletion-2026-10-18.json)。当前后端仍启动器71836/监听67780，操作前重查。

## 2026-10-18 裁剪图删除入口修复

用户反馈切图后无法删除。根因：现有DELETE /api/assets/{id}可正常删除项目Asset，但准备区、描述列表和匹配结果缺少入口，项目图片页仅通用「移除」。新增共享DeleteCropButton在上述四处显示明确删除裁剪图按钮，确认/取消/失败重试，删除后移除选择/结果并刷新分页、统计。仅model_crop提供该按钮，原图入口保持；匹配API query_with_content从当前Asset返回source_type（不信任历史query JSON），支持旧查询正确识别裁剪图。后端删除语义/数据结构保持，底层文件按既有保留策略，不删原图或磁盘文件。

TDD浏览器复现缺失入口、API字段缺失，再实现。原删除接口基线测试通过；41项针对裁剪、删除/计数/标注/历史/索引引用、导出和图片库回归通过；5组专项浏览器验证四入口、取消、失败重试/390px，14组既有多模态浏览器回归通过。构建通过，lint44条既有警告。真实浏览器仅删除自建临时裁剪图，目标404、原图200、项目total=1且annotated/reviewed=0、历史hits清空；实际PG外键/标注清除已核对，借用文件SHA不变，临时项目已清理。业务库行/裁剪SHA与备份核对保持、原1个running训练保留，没有远程启动/停止。无需数据库迁移，022。已重载本机后端，启动器71836/监听67780（操作前重新核对）；私有快照/认证/操作脚本在backend/backend/.backups/crop-delete，禁止打印。详见[验收](../testing/crop-deletion-2026-10-18.json)。未新增批量删除或清理Milvus/磁盘。

## 2026-10-18 ms-swift 大模型与向量训练格式导出

用户补充了messages/positive_messages/negative_messages及各自图片字段的独立对齐规则，并要求positive外层恰好1；同时给出Qwen3-VL SFT格式。本轮在原通用ZIP导出上增加dataset_format=swift_sft|infonce|contrastive|online_contrastive|cosine_similarity，默认portable保持兼容。来源sample_source=descriptions|labels|matches独立选择；描述/标签来源不依赖向量，匹配来源复用已保存文本/以图查询和人工判定。InfoNCE多正样本拆行且只使用明确错误作hard negatives；unknown排除、无正样本查询跳过且空结果拒绝。配对loss用人工正确/错误的1/0（cosine为float），不使用检索score作训练标签，未新建连续评分。

新增train.jsonl/README和manifest.training格式、来源、样本数/跳过数。复用现有审核/skip、项目裁剪范围、文件SHA、来源/版本过滤与ZIP，不修改库结构。SFT指令可编辑，单个<image>可任意放置；assistant为描述，分类来源则为类别JSON数组。非SFT标签来源每类别正配对一行，不自动产生负样本。当前只输出图片；未新增视频/音频、grounding或训练启动器。JSONL中的图片相对解压根目录，需完整解压并cd该目录使用。同查询多正样本注意训练端in-batch假负样本，INFONCE_*由训练端配置。

测试：15项red确认缺失后，新增转换/API格式23项通过，相关导出/裁剪/描述回归共57项通过；构建通过，lint仍44条既有警告。浏览器14组流程验证格式选项、来源、指令、服务端Blob错误、重试下载和390px；导出按钮增加稳定aria-label避免loading图标改变可访问名称。真实API临时项目8组导出（SFT描述/标签、InfoNCE描述/标签/文本及图片匹配、三种配对loss）均校验train.jsonl消息嵌套、单正样本、图片占位/路径数和实际图片解码，临时项目已清理。未发起embedding推理或远程训练。私有备份/验收脚本在backend/backend/.backups/swift-export（禁止打印完整快照/认证），真实业务表和原裁剪SHA核对保持。已重载本机后端，启动器80476/监听71360（操作前重新核对）；022无需新迁移，原running训练仍running。详见[使用说明](分类描述与向量标注.md)、[验收](../testing/swift-training-export-2026-10-18.json)。

## 2026-10-18 描述无需向量、项目私有裁剪和仅标注裁剪图

用户明确：图片描述与大模型描述标注相同，不生成向量；仅文搜图/以图搜图需要向量。向量工作台现在默认图片描述列表，复用单图描述编辑器，只有切换检索才挂载模型/查询控件。准备区默认生成描述，仍可显式建索引。项目设置 `label_schema.annotation_scope=all|crops`，切图请求 `crops_only` 默认 true。原图管理/来源筛选保留；标注队列、继续位置、上下张/跳转、保存防护、批量描述/索引、检索/判定、新建验证集和各导出均按同一 eligibility helper 过滤。仅裁剪时没有检出目标的原图无需标注。

新切图直接创建项目 Asset(image_id=None)，不注册 LibraryImage；路径 project-crops/<project_id>/...，PNG含项目/父图/区域元数据，避免全幅裁剪与原图内容哈希重合。022解除旧裁剪库关联，保留ID/文件/标注；旧LibraryImage记录隐藏且不可再次关联，历史记录不删除。真实13张裁剪迁移并隐藏，项目队列13张、来源原图7张，文件SHA和4674条标注核对保留。已重载本机后端：启动器75852/监听75020（操作前重新核对）。私有备份、激活脚本和真实API测试在 backend/backend/.backups/private-crops，禁止打印完整库快照或认证信息。未启动/停止远程训练；原running任务仍running。

验证：全量后端642通过/77跳过，最新定向76通过；独立PG迁移/历史链/空库初始化12通过。构建通过、lint44条既有警告；13组浏览器模拟交互和真实1440/390px只读检查通过，真实图片已解码、无页面异常。临时项目真实保存无向量的描述并导出仅裁剪ZIP，工作/查询表无新增，测试项目已清理，业务数据再次核对。未重新评测模型识别质量或做规模压测。详见[验收](../testing/private-crops-description-2026-10-18.json)。

## 2026-10-18 向量标注工作台位置修正

用户指出向量检索/匹配判定应位于实际标注工作台。将原ProjectAnnotationTools中的现有检索/判定逻辑提取到VectorAnnotation，通过新路由`/annotate/project/:projectId`的VectorAnnotationWorkspace承载；管理页保留模型裁剪/描述/建索引、队列、导出和审核记录。管理页顶部、项目卡片和项目图片的开始/继续入口均进入向量工作台；已有图片进度也不会把向量项目继续入口引到单图内容编辑器。内容编辑器可返回向量标注，非向量项目直接访问会提示类型不符。

原模型调用、Milvus、后端和数据结构未改，查询/匹配记录沿用。先以浏览器断言复现管理页挂载控件错误，再移动组件。11组模拟交互回归和真实现有向量项目只读验收通过，包含直接刷新、开始/继续、390px和1440px，零真实写请求/页面异常。前端构建通过、lint退出0（44条既有警告）。本轮不重跑后端全量或实际推理。详见[验收](../testing/vector-workspace-placement-2026-10-18.json)。

## 2026-10-17 分类、描述、向量标注与检测切图

用户确认无鉴权Milvus 192.168.1.115:19530/default、平台独立collection；分类单选/多标签按项目选择；导出先默认，后续提供训练格式。没有开发X-AnyLabeling视觉样例检出。标注与审核项目内整合模型裁剪、描述生成、WEMM/Milvus索引和文字/图片检索配对判定，描述/分类视图共用图片标注，正确/错误/无法判断按查询保存。模型裁剪支持所有启用detection配置，不限SAM3，扩框默认每侧20%，保留原图/框/来源。分类/描述复用Annotation版本审核计数，迟到描述不会覆盖人工编辑或审核；匹配判定乐观版本检查。历史图片描述检索入口跳转项目，旧批次可从历史链接查看。

新增021为caption/vector项目类型、project_work_jobs/items和match_queries；复用httpx接MilvusREST，无新依赖。默认ZIP含分类目录或描述JSONL、配对JSONL及来源清单。原图与裁剪同组，未自动划分。详见[使用说明](分类描述与向量标注.md)、[验收](../testing/multimodal-annotation-verification-2026-10-17.json)。单进程串行队列/每批5000；首期2048维。未压测；删除项目不自动清Milvus历史数据（检索不可见）。

后端636通过/76跳过；独立PG迁移、历史链及空库初始化通过。前端构建通过、lint44条既有警告。真实本机API跑通Qwen描述、2张WEMM索引、文本/以图检索各2结果、正负配对ZIP、分类目录ZIP；真实RF-DETR在自建图上无检出，未写负样本。几何/正检出裁剪及迟到结果隔离有独立测试。模拟浏览器8组检查通过，真实1440/390px只读页面无写入和异常。3个临时项目、2张临时图片文件及2条向量已清理，新增队列/查询表归零，清理后再次核对原业务哈希通过。两组自建浏览器会话均已关闭。

本机020→021备份后加法升级，重载启动器64848/监听67020（操作前重查）。旧业务字段校验通过，原1个running/6个终态训练记录保留，没有远程训练启动/停止。私有备份/日志/认证/测试状态位于backend/backend/.backups/multimodal-runtime，禁止打印其中token/完整库快照。老的减少动效下下拉prepare问题未动。

## 2026-10-17 YOLO图库标注与训练使用优化

用户最终确认：导入图片库即可，不绑定检测项目；添加到项目时可选择带入标注。已复用分片上传支持含images/labels/data.yaml或classes.txt的YOLO检测ZIP，图库保留可复用标注来源，两个添加入口可明确选择复制到检测项目，默认只加图片。原图复用、类别按名称匹配，图库来源和各项目副本独立；已有标注不覆盖，冲突计数。管理员在导入时可确认信任，带入项目直接可用于训练/验证（含负样本），保留确认来源；默认待审核。验证集界面/来源字段同步区分导入标注与人工审核。

训练详情增加产物下载（任务子目录普通文件、拒绝链接/越界、ssh-manager SFTP及SHA/大小校验），增加管理员删除记录确认（活动/未知/持有GPU均拒绝，只删平台记录，服务器文件/容器保留）。日志合并stdout/stderr顺序，清理ANSI、回车/光标进度更新，等宽/行号面板支持横向滚动、换行、跟随最新，错误保留。

完整后端618通过/75跳过，020独立PG及真实HTTP续传各1通过，前端构建通过、lint退出0（44条既有警告），6组新增浏览器检查和真实页面只读检查通过。真实A100下载507931474字节checkpoint_best_ema.pth，SHA与服务器一致，发送后临时副本清理；真实日志1050个控制字符清理后200行展示，未启动/停止训练。详见[验收](../testing/import-training-verification-2026-10-17.json)、[上传说明](文件夹与压缩包上传.md)及[训练说明](模型训练.md)。没有在业务库导入或删除测试样本。

本机019→020已备份升级：仅新增图片导入来源JSON、上传模式及默认false信任字段；36张业务表原有业务字段保持（训练监控字段和正常登录时间另计）。20:49:46正常login请求只更新用户1的登录/更新时间，未回退。012迁移原来引用当前ORM，现已冻结历史结构，避免升级链提前创建新列；无需重放旧迁移。本机后台重载后健康、图片、训练、验证集、实际日志/下载接口正常；原1个running和6个终态任务保留。启动器63216、监听70772（操作前重新核对）；私有基线/日志/认证状态位于backend/backend/.backups/import-training-runtime。浏览器最终使用默认动效验收，减少动效下既有下拉prepare异常记录于验收限制，本轮未改共享主题。

## 2026-10-17 直接验证YOLO / RF-DETR训练权重

验证集页改为“验证权重”，可选择已结束的平台训练产物，或服务器现有权重绝对路径；支持YOLO、RF-DETR Medium/2XLarge。RF-DETR按用户提供的公共RFDETR.from_checkpoint + predict接口，类别按训练顺序明确填写；YOLO读取权重类别。默认CPU，可手动读取并选择单GPU。复用原验证集、指标、叠框、报告和历史服务验证API，不需要部署服务或迁移数据库。

创建时固定权重SHA256、镜像digest和服务器修订，使用ssh-manager启动禁网独立容器，权重只读，每批只加载一次。GPU与训练共用事务锁且启动前检查真实占用；停止不确定不释放预留，取消拒绝迟到结果，重启停止旧轮次而不自动重跑。确认停止后只删本轮临时图片/ZIP，保留配置/预测/日志，不修改训练数据和权重。

完整后端587通过/73跳过，最终新增专项10通过、独立PG锁竞争1通过；构建通过，lint退出0（44条既有警告）。10组原验证页面回归、3组权重表单/报告隔离检查通过；真实页面读取服务器/训练并完成桌面与390px检查，仅验证集列表用只读夹具，未创建业务验证任务。绍兴A100实际镜像CPU加载已有2XLarge checkpoint的SHA一致副本，对合成图预测成功并退出0，无GPU设备请求；测试容器/副本/临时文件已清理，原训练保留。YOLO尚未用真实权重推理，未进行独立验证集准确率评测。详见[本轮验收](../testing/checkpoint-validation-verification-2026-10-17.json)和[操作说明](模型验证集.md)。

本机后端已启用新接口，启动器4244、实际监听71344（操作前重新核对进程归属），3000/8000及验证集/训练API正常；数据库保持019。重载前后项目、原图、标注、图片集、模型、验证表和训练GPU hold哈希一致，原运行训练仍running；未发送训练启动/停止命令。私有基线/日志在backend/backend/.backups/checkpoint-validation-runtime。

## 2026-10-16 Qwen 核心提示词开放编辑

模型管理的OpenAI Chat编辑区新增描述/审核核心指令（各1–8000字符）及恢复默认；图片集设置新增scene_prompt；批量描述页新增单批覆盖，已有任务审核输入明确为补充要求。默认值由后端统一提供。复用管理员/租户/修订和任务快照，旧配置默认兼容；描述单次输入优先，审核动态类别/编号/JSON约束追加，原输出校验及自动通过规则保留。

580项完整后端通过（72跳过），69项相关回归通过，019独立PG迁移1项通过；构建通过，lint退出0（44条既有警告）。11组新增隔离浏览器检查、2组入库/11组语义既有回归及2组真实页面只读检查通过。未调用真实Qwen或改写业务提示词；编辑保存用隔离API/数据库验证。见[验收](../testing/qwen-prompts-verification-2026-10-16.json)和[模型操作](模型服务管理.md#qwen-提示词编辑2026-10-16)。

本机018→019已先备份后执行，仅新增model_services.chat_prompts，原8个模型配置及保留业务表哈希不变。重载前查无推理/上传活动；有1个running训练，核对训练worker启动/停止只控制状态监控，不启动/停止容器。新后端启动器16748、实际监听43388，后续操作须重新核对归属；真实配置/默认值/图片集/概览接口200，训练平台状态仍running，未发送训练启动/停止命令。私有备份在backend/backend/.backups/qwen-prompts-*。

## 2026-10-16 工作概览按 A 方案布局落地

按用户第二张参考图将正式概览改为四项指标、当前工作/图片巡览、处理队列/算力概况。复用现有只读接口与图片预览；项目审核进度独立于全局计数，待审入口筛选当前项目 pending，待标注使用项目引用数减已标注数。任务展示最近来源中的进行中任务优先；GPU仅管理员读取，可选服务器和显卡、显示采集时间、不自动轮询。失败/空态不填演示数据，保留成功来源并支持刷新。未修改后端和业务数据。

构建通过；lint退出0（44条既有警告），新增概览文件定向检查无警告。10组隔离概览浏览器检查和49组共享主题回归通过，覆盖四种宽度、图片键盘预览、显卡切换、未知指标、部分失败、重试、空态和普通用户权限。所有API拦截，未触发真实训练/下载/远程监控；真实服务端到端未验收。见[概览验收](../testing/dashboard-layout-verification-2026-10-16.json)。

## 2026-10-16 钴蓝主题与轻量交互动效

用户选定 A 钴蓝工程台，已应用到主平台与 SmartEye：共用暗色变量和 Ant Design darkAlgorithm，导航按数据资产/标注与质量/模型与算力分组，小屏使用抽屉，概览突出当前工作。增加 160ms 按钮/导航反馈和 240ms、6px 页面进场；系统减少动效受支持。标注画布及祖先不做进场/transform，系统偏好变化和导航收起不重挂载画布。业务接口、权限、数据及坐标逻辑未修改。

构建通过，lint 退出0（44条既有警告），5项检测编辑器测试通过。隔离浏览器完成49组主题/布局/交互检查，覆盖主平台13个路由的1440/768/390px、SmartEye六页、表单/弹窗、导航、权限、画布与减少动态效果；额外记录真实 CSS animationstart/animationend。部分管理员接口使用错误状态样本；没有后端写入、真实训练/推理/下载/SSH操作。验收见[钴蓝主题记录](../testing/cobalt-ui-verification-2026-10-16.json)，规范见[DESIGN.md](../../frontend/DESIGN.md)。

## 2026-10-16 训练配置编辑与RF-DETR预检查修复

待启动任务详情新增“编辑配置”，管理员通过PUT `/api/training/runs/{id}`更新，保留任务ID并清空旧预检查；其他状态拒绝修改。RF-DETR支持输入标准data.yaml/data.yml，预检查和训练脚本统一转换为所在目录，拒绝自定义文件名及data.yml被同目录data.yaml覆盖的歧义。

67项训练专项通过。后端18:48重载成功，监听PID28332。真实任务231d46a7-e645-4e27-832d-8e917fdde63f原值保存HTTP200且配置不变，随后完整预检查HTTP200、所有检查通过；读取fire/smoke/star、train/images和valid/images，状态仍draft，未启动正式训练。浏览器控制连接中断，本次未完成实际页面点击验收。

## 2026-10-16 SSH交互终端

管理员 `/gpu-terminal` 已实现并在绍兴A100真实验证。xterm通过首帧鉴权WebSocket连接平台专用MCP子进程；可选PTY扩展复用ssh-manager的连接与配置。支持top、vim、Ctrl+C、中文和窗口同步，权限/租户隔离、撤销、空闲断开、有界输入输出与连接清理已覆盖。19项隔离后端测试、Node扩展与真实MCP本机SSH链路通过；前端构建通过，lint退出0但有其他既有警告。

真实页面完成top/Ctrl+C、只读vim、390px无溢出、远端stty尺寸同步与断开清理。未启动正式训练或修改远端文件。续接时重新核对服务归属；本轮恢复的后端实际PID63508，前端启动PID29192，监听127.0.0.1:8000/3000。扩展原始备份位于`backend/backend/.backups/ssh-terminal/index-896ae003ad8978ca.js`。说明见[SSH调试](GPU服务器SSH调试.md)，截图`output/playwright/gpu-ssh-vim-final.png`。单进程会话限额，刷新不恢复；多实例尚未支持。

核对日期：2026-10-13。本记录依据用户提供的Claude聊天记录、当前代码与文档，以及本次实际检查。它记录接手基线，不代表功能已验收。本文中的代码路径均以项目根目录`F:/hzxf/数据管理平台`为基准，文档导航见[文档目录](../README.md)。

## 已确认的开发边界

- 主项目为项目根目录`F:/hzxf/数据管理平台`，包含数据拉取、数据管理、数据标注三个业务模块。
- SmartEye完整内置，保留独立页面及原有可用功能；用户无需另行启动SmartEye服务。
- 拉取任务可以只下载，也可以直接关联标注项目。关联通过创建图片引用实现，不要求用户再执行一次导入，也不复制已有原图。
- 图片统一使用存储抽象层，先本地存储，后续迁移至EOS。删除项目中的引用不能破坏其他项目或SmartEye仍在使用的原图。
- 第一版单租户，不继续扩展租户功能；现有身份认证仍需保留。
- 首个算法任务为检测：穿红色上衣的人都算目标。训练集与独立测试集分开。
- “百张内”不包括测试集。聊天同时出现“训练集总量100张”和“每类100张左右”；首个单类别任务不受影响，多类别训练的总预算需在训练接入前明确。
- 部分标签导出：选择“人”和“车”时保留图片，只移除“猫”等未选中类别的框。
- 图片质量、自动审核、优化参数先用默认值，但要能在平台配置。自动化标注和审核优先，疑难、冲突及抽检样本转人工；相似度不能单独作为标注正确性的证明。
- 原训练环境主要为Tesla T4，包含K8s与独立Docker；用户2026-10-15明确外接算力为绍兴A100。本轮只接资源监控，真实训练脚本及执行接入仍待提供。远程访问按用户要求使用ssh-manager MCP。
- 模型以实际服务协议与ID为准。2026-10-15绍兴移动算法2的model_service已接YOLO/RF-DETR/SAM3/FastVLM；Embedding实际为`/models/wemm_embedding_2b`，2048维，Qwen3.8-27B接场景和单图描述。Reranker按用户要求暂不使用，仅保留配置；DINOv3未部署。额外RF-DETR SAHI上游500已停用，普通RF-DETR可用。
- 模型下发已扩展为独立版本库及多目标管理，支持K8s/Docker/Docker Kafka模型下发与镜像更新；K8s按本环境Ansible剧本预分发，保留手动生效与恢复；详见[模型下发](模型下发.md)。真实生产模型及镜像切换尚未验收。EOS具体接口仍未提供，不阻塞本地业务流程开发。

## 2026-10-16 训练页面与Docker执行（当前）

用户提供YOLO26、RF-DETR Medium/2XLarge及Swift/WeMM脚本和镜像，重新确认支持“平台冻结导出集+服务器已有路径”，全部示例参数可配置。新增 `/training` 与 `/api/training`，管理员保存、预检查、明确启动/停止，租户内只读配置/日志/产物；RF两种初始化分别生成。Swift使用已有JSONL，检测冻结YOLO/COCO包按原哈希传输校验，适配RF目录，不把test当val。配置不可原地修改，新参数创建新任务。

018只增training_runs与training_gpu_holds；基于GPU UUID唯一占用，断网保留hold，重启只核对已有容器、不重发启动。输出任务UUID隔离，Docker标签校验限定停止对象，禁止抢占外部GPU任务；活动连接禁止修改/删除，存储清理保护训练引用的冻结包。复用ssh-manager及加密连接，训练容器禁网、不自动pull或下载权重，不触及原训练三表。

实查绍兴A100四卡被Qwen占用，检测镜像RF-DETR1.9.4/Ultralytics8.4.137、python3；用户说明Swift镜像正在拉取且示例路径属于其他服务器。未启动正式训练，模板未作真实训练与权重独立加载验收；不声称训练效果或适配器已ready。开发验证与部署状态见[本轮验收](../testing/training-verification-2026-10-16.json)，操作见[模型训练](模型训练.md)。

后续Swift镜像已拉取完成，实查Swift4.5.3/torch2.13.0+cu130，24个CLI字段存在，零可见GPU的兼容检查通过。实际MCP/SFTP冻结包传输、哈希验证及检测镜像CPU的YOLO配置/RF-DETR两种格式train/val/test加载通过。521项完整回归（71跳过）、128项关联专项及最终39项训练专项通过，018的3项隔离PG测试通过，新前端文件lint无警告，双入口构建通过（仍有既有大包提示）。

遵用户选择等待既有预标注4673/4673结束后，于15:51重载主后端；原任务47自动通过、4624转人工、2失败保留未重试，不把“全部处理”当作“全部成功”。3000/8000、训练五模板/空任务列表/服务器及原项目/图片/模型API均200。新监听46196、启动器53196，后续停止前仍须重新核对归属。业务新训练/hold均0，未启动正式训练；临时界面夹具及远端CPU夹具已移除。

16:18补充数据配置生成与标签选择后再次重载，启动器42080、实际监听58396（操作前须重新核对）。用户提供 `/ai/chen/training/datasets/middle/data.yaml` 与 `yolo-data.yaml`，前者为train/images+valid/images，后者为images/train+images/val，两份包含fire/smoke/star并写旧容器根路径；平台可解析YAML或目录并在任务输出中生成新配置，不修改源文件。标签选择按原顺序连续编号，未选框移除而图片保留背景，YOLO与RF-DETR统一生成可读数据，Swift不按检测标签过滤。权重路径按租户/服务器/训练器复用最近保存任务；输出根目录按北京时间默认并自动创建。无数据库迁移。

本轮50项训练/数据专项、142项关联回归及前端构建/定向lint通过；4项真实检测镜像CPU加载检查通过（两份源YAML×YOLO/RF，只选smoke），源配置哈希不变；真实代理API defaults/dataset/runs/projects/images均200。界面读取真实三标签并仅选择smoke，默认时间目录已显示；未保存测试任务、未启动训练，临时远端脚本已移除。验证见[配置增强验收](../testing/training-options-verification-2026-10-16.json)。

16:37用户纠正并明确确认最新边界：服务器已有数据完全沿用原配置，不生成数据配置、不筛标签、不重新编号；仅平台冻结标注数据自动生成训练配置并允许标签选择。已修正脚本、服务端验证和页面：YOLO传原YAML，RF-DETR传原数据集目录；服务器预检查使用实际镜像内框架的路径规则，不擅自修正挂载路径。原配置引用/workspace/dataset时需配对应只读挂载。此前16:18的服务器自动生成/过滤行为已被本条替代，权重记忆和时间输出保留。

56项专项通过（包含实际执行生成脚本、证明原路径直传，及仅平台可筛标签）；前端构建/定向lint通过；真实镜像的YOLO原YAML+只读挂载及RF原目录CPU解析通过。重载前自动化/训练/语义活动均0，重载后健康/训练/项目接口200，远端标签过滤请求422且未创建任务。启动器19800、实际监听16912（操作前核对）；无迁移、无正式训练。记录见[数据来源边界验收](../testing/training-source-boundary-2026-10-16.json)。

## 2026-10-16 GPU服务器连接管理（此前记录）

`/gpu-resources`新增密码连接管理：IP/端口/root默认账号、未保存测试、直接保存、修改/清除密码、启停与删除；保留原ssh-manager默认连接。017新增gpu_servers，按租户隔离的管理员接口；密码Fernet加密，仅has_password回显，改IP/端口/账号须新密码。GPU_CREDENTIAL_KEY可独立配置，否则按用途从JWT密钥派生，相关密钥轮换须重存密码。

Python→Node桥只经stdin传动态凭据，Node MCP子进程临时环境仅定义只读platform_gpu连接，不加载全局连接配置，不写明文配置文件；旧默认路径保留。仍为固定Python3包装nvidia-smi两项查询；按服务器/修订15秒缓存，缓存不存凭据，改配/删除拒绝旧结果。多服务器展示复用GpuSnapshot，父级按ID/修订重建，缺密码/停用不轮询。NVIDIA无MIG能力不作未知报警。

550项完整回归、实际MCP密码/错误密码链路、隔离PG CRUD/并发、11项前端状态及6组夹具通过；真实页面4卡/8进程与新增表单/390px已检查，写入拦截，业务配置新表为空。42表业务数据及4676文件保留。启动器36044、实际监听21444，后续操作须重新核对归属。操作与边界见[GPU管理](GPU资源管理.md)、[验收](../testing/gpu-server-connections-verification-2026-10-16.json)。未接其他真实服务器密码或训练调度，未变更远端工作负载。

## 2026-10-16 模型验证集（此前记录）

侧栏 `/validation`，接口 `/api/validation`。人审真值快照与持久验证任务独立存储；项目当前人审样本或冻结val/test，AI自动批准不作真值。冻结类别、版本、内容哈希；共享原图不复制，原项目删除不级联删除验证集。模型管理检测服务修订/参数固定，管理员创建/取消/重试，租户内只读报告；旧attempt、模型变更/停用、管理员权限变化均防迟到采纳。

P/R/F1、101点AP50/AP50:95、每类指标、混淆矩阵及负样本误检；AP与隔离pycocotools对照，每图每类maxDet100，无面积/crowd专用规则。失败/取消不冒充漏检，明确部分结果；报告用同一批items计算指标和明细，GET不写业务行。创建仅冻结元数据，不锁项目或读取全部原图；执行/预览核对原图。每集最多5000，单后端运行，重启running→failed供手动重试。

015→016冻结增量只增三表；原39表及4676文件SHA不变，新表未写真实任务。新后台监听62840、启动器50044，前端仍3000；以后停止前必须重新核对端口归属。测试与未验收边界见[本轮证据](../testing/model-validation-verification-2026-10-16.json)，操作见[说明](模型验证集.md)。未调用真实训练模型批量验证、未产生真实准确率结论；训练和EOS仍未实现。

## 2026-10-15 批量智能处理、审核、检索与分析（此前记录）

用户确认：预标注只未标注图；人工框独立AI审核，不改几何；每框严格>阈值（默认0.9）且Qwen逐框/整图通过才自动通过。外扩各边默认20%。所有负样本必须管理员最终审核，不确定/超时/输出无效同样转管理员。API /automation 管理员写入，记录模型/参数/类别/内容哈希及标注和人工审核基线；取消、模型停用/变更、人工保存/审核、类别变化均阻止迟到覆盖。旧候选可驳回移出队列且不改新标注。

2026-10-16用户已将Qwen调至每次最多3张图、16384上下文；平台改为1张整图+每轮最多2张独立外扩局部图，局部前文字标明原框编号，不再拼接。零框仅整图，多框分轮；768输出预算、逐框结论和裁剪坐标保留，证据template_version=3。14项审核回归通过，真实Qwen三图合成请求约6.75秒返回两个框的结构化结论，未写入业务标注；检查时本机8000无监听，未启动后端工作器，新代码在后端启动后加载。超过100框/无法确认的图转管理员，独立误通过率/效果校准未做。默认2路处理，单后端恢复running→failed供手动重试，不自动发起历史全库任务。

描述与检索：/image-semantics持久描述、完整2048向量、文本或库内图余弦TopK；模型ID/版本/预处理/维度/内容哈希隔离，最多5000条当前范围精确检索，超限拒绝。质量效率：/analytics按项目日期区分人工与自动事件、返工和最新质量流程，15秒心跳/30秒单段上限、60秒空闲暂停、2分钟离线重试、跨标签页时间并集；旧耗时不估算，流程通过率不是准确率。图片库/项目预览←→跨页，不扫全库。

014→015仅增六张表，先隔离PG后私有备份实库；原33表及4676文件SHA保留。362项全后端（含PG/真实加载器）、4项计时Node、13项预标注/11项检索/9项分析浏览器、真实三入口与切图检查通过，构建/lint退出0仍有警告。新增元数据字段另有10项审核专项回归，未改真实标注或创建线上全库任务。源码加载需要主后端重启，当前已按本轮重启；停止前核对实际监听归属。

操作与明确边界见[智能处理](智能处理与审核.md)、[接口](../api/智能任务接口.md)、[验收](../testing/p3-automation-semantics-analytics-2026-10-15.json)。原基准/高级质量评分/主动学习、真实训练、EOS与多实例仍待完成；Reranker和DINOv3保持既有部署边界。

## 2026-10-15 绍兴A100 GPU资源（此前记录）

新增管理员 `/gpu-resources` 页面及GET `/api/gpu-resources`。设置 `GPU_SSH_MANAGER_ENTRY`、`GPU_SSH_NODE_BINARY`、`GPU_SERVER_NAME`；当前服务器名为ssh-manager里的 `绍兴a100`。复用该项目已安装的MCP SDK，由Node stdio桥接固定 `ssh_execute` 只读采集；不新增SSH库，不读写浏览器密码，不部署远端代理。部署到其他主机需要重配入口与ssh-manager连接。

按UUID显示四张A100及计算进程，同一PID跨卡不去重。显存使用驱动实际free，未知值/MIG/进程采集失败明确区分；不判断可调度或可抢占。后端15秒缓存与并发合并、失败短缓存；前端30秒可选刷新，隐藏暂停、卸载取消、不重叠，失败保留上次数据且标陈旧。仅管理员接口可访问，普通用户菜单隐藏、直达不发采集；没有任何停止进程/命令执行的浏览器入口。

300项全后端回归、11项Node前端状态测试、10项真实/拦截浏览器检查、双入口构建通过；lint退出0但仍有警告。业务库仍014，33表（忽略正常登录时间）及4676文件SHA256与本轮基线一致。后端已按本轮代码重启，停服务前仍需实时核对归属。O016只记部分：真实训练、资源预留/配额/释放、多实例和历史采集仍未做。操作见[GPU资源](GPU资源管理.md)，证据见[本轮验收](../testing/gpu-resources-verification-2026-10-15.json)。

## 2026-10-15 P0收尾、P2划分和模型管理（此前记录）

013静态增量补齐训练三表和UUID项目外键，旧训练API类型同步但仍未注册执行。014新增独立model_services配置表，保留训练models表语义。空库脚本scripts/bootstrap_database.py要求显式目标、空库与事务内DDL/stamp；可选显式管理员用户名/邮箱及BOOTSTRAP_ADMIN_PASSWORD环境变量创建首个账号，不给默认密码、不改已有库。专项PG验证后业务库012→014，原29表（含版本表）备份、28业务表及4676文件SHA保持；后来真实登录仅改users.last_login_at/updated_at。

新增/storage-maintenance管理员页面与API：全租户、SmartEye实际runtime和冻结manifest引用核查，在线execute固定409。离线`python -m services.storage_cleanup archive|restore`需要显式目标、全部写入进程停用和确认；数据库禁连防护、引用重查、SHA验证、同卷隔离清单/恢复，绝不在本轮清理真实文件。强杀CLI可能需要按操作文档恢复ALLOW_CONNECTIONS，这是离线维护工具，不能在线使用。见[存储操作](../storage-cleanup-p0.md)。

ProjectExport新增明确来源编辑、传递合组、预览/建议、每类训练预算和pHash/颜色阈值，冻结重新校验并保存split_evidence；新页面默认require_source=true，缺来源阻止冻结，API旧请求仍兼容。不可保证任意裁剪跨帧近重复，真实独立测试集/校准待验收。见[P2专项](../testing/p2-04-dataset-splits.md)。

侧栏/models管理独立模型配置，新增/编辑/删除/启停/测试/单图试运行；普通用户只读和预测，管理员可改。multipart检测/描述与vLLM聊天/Embedding分开适配，OpenAI模型使用准确ID。工作台预测预览不写标注，未知类别需映射，应用只替换可撤销草稿，0框不能自动判负样本；切图/历史/配置修改会丢弃迟到结果。注册9配置、8模型实际推理通过，SAHI额外配置上游500停用；Reranker仅配置、DINOv3未部署。场景配置可复制兼容模型地址/ID后独立保存，不动态覆盖旧集。见[模型操作](模型服务管理.md)。

最终验收见[整合证据](../testing/foundation-datasets-models-verification-2026-10-15.json)。不把单图模型接入当P3多轮自动审核、批量队列、持久描述/向量检索、质量评分或P4训练完成；EOS与实际独立测试集仍未验收。主服务8000/前端3000已按本轮代码启动，停止前仍需实时核对归属。

## 2026-10-15 继续标注、序号跳转与负样本（此前记录）

本轮仅修改主前端：annotationProgress.ts按localStorage当前用户的tenant_id、id及项目ID保存最后成功打开的assetId和文件名；ContinueAnnotation.tsx用于项目管理、标注项目卡片、项目图片和项目审核四个入口，继续前GET资产核对项目，404清理失效位置，其他失败保留供重试。存储禁用时提示，不阻断工作台；尚未打开过项目不显示按钮。浏览器记录不跨设备，不缓存标注草稿。

工作台原计数改为两侧切图按钮之间的序号输入框，回车调用既有GET `/api/assets?project_id=…&page=序号&page_size=1`，稳定排序与邻图一致，沿用useBlocker自动保存。查询/保存失败保留草稿和实际序号，异步请求在退出或更换图片后失效。N及底部按钮清空框并确认负样本，进入撤销栈，保存后新版本待审核；输入/弹窗、历史只读、绘制/保存中禁用。

7项前端单元测试、浏览器拦截回归及6项真实只读检查通过；双入口构建通过，lint退出0且仍有警告。真实项目513→1000→513、两个页面继续位置和审核返回筛选通过；1366×768/1280×600无需整页滚动，390×844无横向溢出。测试中登录凭据过期，私有刷新后重跑；标注写入均拦截，用户同期新增的两条标注/事件保持，后续独立基线9表指纹一致。未改后端、未迁移，仍为012。见[本轮证据](../testing/workspace-progress-verification-2026-10-15.json)。

## 2026-10-15 标注切图修复（此前记录）

批量关联图片的created_at相同，旧列表只按时间排序，导致47页返回4676条却只有3395个不同ID。工作台每次切图扫描全部分页，找不到当前图时显示0/4676且两侧按钮禁用。现列表统一按created_at、id倒序，新增有认证和项目权限校验的GET `/api/assets/{asset_id}/neighbors`，使用同一排序返回position、total、previous_id、next_id。

工作台单独读取位置，取消全项目列表扫描；加载时显示“定位中…”，失败保留图和草稿并提供“重试切图读取”。自动保存及409保护保持；前后按钮和快捷键共用邻图ID，首尾按钮禁用。无需迁移，仍为012。

197项完整后端回归通过（8PG、6SDK），8项浏览器拦截流程、双入口构建通过，lint退出0且有警告。真实4676张逐页核对全部唯一，截图图片513/4676，前后切图实测成功且未写标注；9张业务表指纹保持。模型推理队列排空后重启主后端，8000/3000可用；进程归属仍需操作前实时核对。见[验收证据](../testing/workspace-navigation-verification-2026-10-15.json)。百万级项目与并发增删时的固定快照未验收，位置随项目内容变化。

## 2026-10-15 图片库与项目批量选图（此前记录）

两个入口支持20／50／100张分页、跨页选择、共用完整筛选和“选择当前筛选全部图片”。修改筛选清空选择，读取全选失败保留原选择。GET `/api/images/selection`复用列表条件，不分页返回ID；exclude_project_id校验项目权限并排除已用图片，旧NULL引用不会误排除全部。抽屉表格单独滚动，分页和提交保持可见。

添加项目、归集、分类共用1000张顺序批次；每个成功批次移出勾选，失败停止并保留剩余供重试。成功批次不回滚，刷新不保留选择；分类仍按所选图片集规则，结果随原图共享。未改原图/标注、数据库结构或接口单请求上限，仍为012。

本轮全回归190通过/6暂跳过，随后6项真实SDK单独通过，共196项不同测试（8PG）；批次自检、13项浏览器流程、构建及pip check通过，lint退出0且保留警告。2005张模拟图片及11次写请求全部拦截；另在真实页面全选4676张再清空，无业务写入。8张业务表指纹相同，现有1项目、0引用、1图片集、4676图及10上传回执保留，分类均完成。主后端8000/前端3000已运行，停进程仍需重新核对。

见[验收](../testing/image-bulk-selection-verification-2026-10-15.json)、[操作](图片库与项目使用说明.md)和[计划](../plans/2026-10-15-image-bulk-selection.md)。实业务数千张添加、百万级ID选择、EOS及多实例未验收；网络超时但服务端已提交的分类请求重试可能再次排队，项目/归集仍按原幂等去重。发现一个无连接旧codex_p1临时PG库，未确认归属，保留未删除；当前测试创建的隔离库正常清理。

## 2026-10-15 上传目标、合并与场景并发（此前记录）

两处上传入口默认已有图片集：当前筛选→localStorage里上次使用且仍存在的名字→已有第一集；无已有集留空，不隐式日期命名。显式输入新名称仍保留。开始时记录选择，不在首次页面挂载时覆盖浏览器记忆。5项浏览器检查覆盖刷新、优先级、已删除名称和空状态入口。

实库“1015”已并入原“绍兴嘉兴误报-1015”（保留原UUID）：4676原图及分类结果保留，成员并集4676，原集补2567张；10条上传回执保留，源8条迁移collection_id/续传键，短期入库引用不删。源/目标规则相同，分类快照仅迁移集合引用/版本，成功分类元数据保留历史来源。24张其他表逐表指纹一致，文件大小/mtime及图片元数据核对保留，没有对文件宣称重新逐字节哈希。

合并前8包全部完成；先锁未领取队列并等待已有推理完成，再停核对过父进程的本项目后端，合并事务后重启。Windows端口状态延迟导致第一次检查失败、事务回滚，已恢复服务后重试成功。私有备份为`.backups/collection-merge-scenes/database-merge.json`及校验，不外传；恢复集合时不要全表回滚合并后新增的分类结果。未增加通用合并API/UI或新迁移，仍为012。

`SCENE_CLASSIFICATION_CONCURRENCY`默认4（1–16），全局有界TaskGroup共享HTTP客户端；单后端恢复约束不变。真实20秒完成16→39张（约2.44倍），4路窗口失败增量0；原有13张HTTP请求失败保留，未自动重试。195项全回归（8PG/6SDK）、构建/pip check通过，lint仍有警告；新增默认选择与原上传时序/续传浏览器检查均拦截写入。见[验收](../testing/collection-merge-scenes-verification-2026-10-15.json)及[计划](../plans/2026-10-15-collection-target-merge-scenes.md)。刷新前端后查看唯一图片集，旧页面的本地筛选/待传File对象不会跨刷新保留。

## 2026-10-15 文件夹与压缩包分片上传（此前记录）

追加关闭重开修复：原弹窗effect清理会abort，且每次打开强制清空所有行状态，导致两个已完成包也显示等待。现在关闭只隐藏；组件卸载/明确暂停才abort，重开保留队列、目标和完成结果，修改图片集/门槛才重置所选行。测试覆盖未开始选择、上传中、入库中和完成后的关闭重开以及隐藏时继续下个文件；浏览器写入拦截，未改后端或真实图片。本地页面刷新/离开仍需重选，见[验收](../testing/upload-dialog-lifecycle-verification-2026-10-15.json)。此前189项后端全回归为上传实施记录，本次前端修复未重跑。

图片库新上传窗口支持图片、文件夹、ZIP/TAR/tar.gz/tgz；按8MiB分片并用原生Web Crypto逐片校验，断网/刷新后重选同文件、图片集和尺寸门槛只补缺片。文件夹子层级归同一图片集。后端会话/游标持久化，单进程后台逐图事务入库，复用现有read_upload/register_image/assign_collection；每集过滤、同图去重与共享场景保留。上传暂存/记录7天清理，删除会话不删除原图，处理完可关闭窗口。

主库012已在私有备份后应用：新增image_uploads/image_upload_items，旧26张业务/配置表逐行比较不变，已有文件哈希不变。升级时用户已新增1项目、1图片集；保留3用户、99平台和401告警类型及模型设置，图片0。私有迁移证据`.backups/resumable-upload-runtime/`，不要泄露记录或浏览器Token。旧历史清理脚本带011表白名单，不能在012下直接重跑，更不能自动恢复旧样本。

最终189项完整回归通过（8PG、6真实SDK），pip check和双入口构建通过；lint退出0但仍有警告，构建保留大包提示。9项浏览器检查拦截全部上传写入，另实读新运行窗口；11个运行读取和401/404边界通过。主后端当前8000，前端3000；进程号可能变化，停服务先核对监听与父进程。

说明见[文件夹与压缩包上传](文件夹与压缩包上传.md)、[证据](../testing/resumable-upload-verification-2026-10-15.json)与[计划](../plans/2026-10-15-resumable-upload.md)。默认单包10GiB、展开20GiB/10000条；单图沿用MAX_UPLOAD_SIZE，当前运行值100MiB，示例默认10MiB。localhost/HTTPS必需，远程普通HTTP无Web Crypto；仅单后端/本地存储，10GiB实包、真实代理、多实例和EOS未验收。未增加RAR/7z、项目自动关联、自动检测框或训练功能。

## 2026-10-15 历史业务数据已清空（此前记录）

用户明确选择清空全部历史业务数据。19个项目、2个图片集、6张原图、12个项目引用、10个标注版本、33条事件、2个冻结快照，以及业务任务/缓存表已清空；活动存储的9个文件已归档，当前活动库与文件均为空。3个账号、租户、模型默认设置、数据库011版本、SmartEye的99个平台及401个告警类型配置逐表指纹核对保留。

清理前私有备份位于项目主后端的 `.backups/history-reset-2026-10-15/`，包含可按原PG表类型恢复的JSON记录及两份哈希核对过的文件副本（复制备份和原目录归档）。这是清空活动业务，不是删除备份或不可恢复擦除。后端已重新启动，空列表/工作概览/API认证与5项页面空状态检查通过，见[清理记录](../testing/history-reset-2026-10-15.json)。

下方test-img、历史图片、旧项目UUID和6张图片等均为清理前验收记录；依赖这些实际数据的旧浏览器脚本不能再直接作为当前空库验收，不要自动恢复旧样本。后端隔离测试不依赖这些业务数据。之后可从新建图片集、上传/拉取图片和创建标注项目重新开始。

## 2026-10-14 图片集独立规则与共享场景（此前记录）

每个图片集独立保存分辨率、分类开关、模型地址/名称和场景名单；平台配置仅作新集模板，已有集不随模板变化。可先创建空图片集再配置，上传默认按目标集，SmartEye可指定目标集或按各平台图片集规则归类，本次阈值可覆盖，包含等号的长边规则保留。

按用户后续补充：同集同内容只留一张，同图跨集继续共享原图和场景。原图首次自动排队时固定来源集规则；重复图复用成功分类，手动按所选集重新分类更新该图在所有所属集的结果。场景仍不修改检测框/标注版本/冻结包；失败保留原图与旧标签。

011经真实隔离PG与私有备份升级，为现有“历史图片”保存独立配置；19项目、12引用、6原图、10标注版本、33事件、2冻结包及旧场景结果保持，原图/冻结哈希不变。176项全后端回归（7项PG、6项SDK）、5项编辑器、双入口构建和pip check通过；运行接口验证了指定集默认过滤、同批同图只返回一条及已有场景复用。浏览器写入拦截，未新增业务测试图片/图片集，真实子平台/EOS/Docker仍未验收。见[操作说明](图片场景与入库规则.md)、[实施计划](../plans/2026-10-14-collection-ingest.md)和[图片集配置验收](../testing/collection-ingest-verification-2026-10-14.json)。

## 2026-10-14 场景分类与入库规则（此前记录）

用户最终要求长边大于等于720，上传/SmartEye按`max(宽,高)>=min_long_edge`过滤，本次可覆盖，0关闭。管理员配置模型地址/名称、场景名单与默认阈值，两个入口共用。成功入库后由持久队列调用`http://10.11.12.123:31030/v1`的`Qwen3.8-27B`，多标签显示/筛选，失败保留图片并可重试，重复成功分类复用；场景与图片集/项目类别独立。

010已备份升级，一张现有图实际分类为“工厂、高位摄像头”。170项全后端回归包含6项PG和6项真实SDK，新功能9项浏览器检查、图片库7项、图片集4项及统一页面23项回归通过，双入口构建及pip check通过。19项目、12引用、6原图、10标注版本、33事件、2冻结包保持，正常登录时间及新增场景元数据/设置例外；原图与冻结哈希不变。详见[操作说明](图片场景与入库规则.md)和[本轮证据](../testing/image-ingest-verification-2026-10-14.json)。

后台分类当前单后端进程、顺序处理；待分类重启继续，中断处理标记失败后手动重试。模型配置更新不触发历史图片全量重跑。P3检测/Grounding预标注、完整描述、Embedding/Reranker、自动审核仍未完成；EOS、真实子平台拉取和Docker未在本轮验收。

## 2026-10-14 工作台适应窗口（此前记录）

针对画布和下方确认控件需要滚屏的问题，桌面工作台（宽度≥992px）改为填充窗口剩余高度，压缩重复标题区，画布完整适应，右侧框/属性/历史面板独立滚动。窗口大小改变保留缩放，仍按原图像素标注；窄屏继续自然纵向排列。

修复前1366×768窗口页面高948px，画布底部873px、确认控件895px；修复后3种桌面尺寸及长60框面板检查通过。与用户截图相同图片在1692×947窗口页面高度947px，整图与下方控件可见。68项浏览器、5项编辑器、10项只读API和双入口构建通过。未改后端/结构，测试写入拦截，原图和冻结包哈希保留。见[窗口布局验收](../testing/workspace-viewport-verification-2026-10-14.json)。

## 2026-10-14 标注与审核按项目组织（此前记录）

标注与审核入口先显示分页的项目卡片和进度，再进入指定项目的最新标注。列表以图片预览、文件名、框数、版本、审核状态和更新时间替代长ID。状态、页码和每页数量保留在URL；修改状态回到第一页，读取失败可就地重试，旧请求迟到不覆盖新筛选。审核统一在现有画框工作台看图完成，取消旧JSON弹窗和列表快捷审核；切图及返回保留原标注项目与筛选，从项目图片进入时仍返回项目图片。

本轮83项浏览器检查、5项编辑器测试、10项只读API和双入口构建通过，lint退出0且仍有警告。测试审核/保存/关联写入均拦截。期间现有test-img新增3张图片的标注，当前最新标注5条、版本总行9条；保留这些更新，不能声称标注表与开始时相同。6原图、2冻结包哈希和图片库/图片集记录不变。没有改后端/迁移，也未重跑此前150项全后端回归。见[本轮验收](../testing/project-review-verification-2026-10-14.json)。

## 2026-10-14 项目与图片导航简化（此前记录）

图片管理固定为独立图片库`/assets`；项目管理“管理图片”进入`/projects/<项目UUID>/images`，标题、面包屑和侧栏明确当前项目。项目内从图片库添加改为抽屉，已有图片禁选，成功／取消不跳转，失败保留选择。图片库添加后留在原页，显示目标项目及明确查看入口。新建项目直接进入其图片页，标注工作台返回同一项目；旧项目查询链接兼容跳转。

本轮双入口构建、5项编辑器测试、107项浏览器检查及10项只读运行请求通过，lint退出0但仍有警告。浏览器写入全部拦截。当前19项目、6张原图、12项目引用、6标注版本、2冻结包；现状记录与原图／包哈希比较不变，没有回退到10月13日的6引用。后端与结构未改，本轮未重跑此前150项全后端回归。见[导航验收](../testing/project-navigation-verification-2026-10-14.json)及[图片库操作](图片库与项目使用说明.md)。

## 2026-10-13 命名图片集（此前记录）

新增独立图片集与成员关系，上传可指定1013/1014等名称、选已有名称追加；图片库按名称筛选并展示数量，已有图可多选加入图片集。SmartEye检测/台账/监听/智搜/工具及详情下载明确带平台名，AI/缺失来源用明确默认名。同内容在多个图片集保留归属，原图不复制，项目标签/标注不变。

009经独立PG回填及并发幂等分类后执行：6张现有图片加入“历史图片”；项目、图片、标注及两个冻结包的记录/哈希保留。150项完整回归（4项PG、6项SDK）、4项命名浏览器检查、23项UI回归、10项运行请求和双入口构建通过。浏览器写入拦截，实际读现有图片。额外修复独立图片上传继承JSON头的问题，浏览器检查真实multipart边界、文件名和图片集字段。

操作见[图片库说明](图片库与项目使用说明.md)，证据见[命名图片集验收](../testing/image-collections-verification-2026-10-13.json)。真实上游和EOS仍未验收，图片集改名/删除/目录层级未扩展。

## 2026-10-13 独立图片库与项目引用（此前记录）

按用户纠正：先上传／拉取，后选择用于标注项目。新增images目录，assets保留为项目使用记录并引用image_id；同图可用于多个项目，标注／审核各自独立。图片管理默认不选项目，提供名称／来源／首次入库批次／时间筛选、多选添加及项目使用视图，上传不要求项目。SmartEye仅下载也登记目录，直接标注则自动建立项目引用。项目删除不删除目录原图。

008经隔离PG旧结构迁移、并发重复添加及完整回归后应用；实际19项目、6图片、6引用、6标注版本和2冻结包保留，原图／ZIP哈希不变。全局概览统计独立图片，项目统计和审核继续按引用。146项完整回归（4项PG、6项SDK）、43项日期/兼容追加回归、7项图片库与23项UI浏览器检查、13项运行请求及双入口构建通过。操作与边界见[图片库说明](图片库与项目使用说明.md)，证据见[图片库验收](../testing/image-library-verification-2026-10-13.json)。

## 2026-10-13 类别同步与切图自动保存（此前记录）

项目管理的编辑表单新增明确的旧→新类别映射，覆盖当前配置及最新标注使用的旧名称。保存项目时，同一事务锁资产再锁项目，校验base_label_schema，将受影响的最新框同步改名；复用save_annotation生成待审核新版本，保留几何、旧版本和冻结包。不映射就删除正在使用的类别会被拒绝，新增/排序不改框。

工作台上一张/下一张及A/D、方向键切图先自动保存；没有修改不写入，500/409/未确认负样本时留在原图并保留草稿。刷新或关闭标签页仍需手动保存，不保证自动写入。

test-img经私有备份后通过主API同步2图5框“人”→“person”，最新v4/v2待审核，原图和两个冻结包哈希不变，其他项目不变。142项完整后端回归、5项编辑状态测试、6项切图/3项映射/25项编辑/23项界面浏览器检查及双入口构建通过。浏览器写入测试均被拦截；真实修复另行核对。见[操作说明](检测标注操作说明.md)及[本轮验收](../testing/label-sync-autosave-verification-2026-10-13.json)。

## 此前进度：2026-10-13 标注交互补充后

当前工作台基于原React/SVG实现增加缩放/平移、快捷键、重叠框选择、100步本地撤销重做。参考F:/hzxf/labeling，参考仓库未修改，没有引入Konva或Vue。新代码：frontend/src/components/DetectionCanvas.tsx及detectionEditor.ts，调用方仍为DetectionWorkspace。

类别在项目管理的新建/编辑表单中配置，仍写label_schema.labels；schemas/project.py补列表校验。test-img开始检查时配置“人”，最后项目标签已变为“person”（updated_at=2026-10-13T14:01:22Z），已有“人”框和快照未变。配置变化并非浏览器拦截测试写入，保留当前设置；已取消未配置项目的硬编码默认。历史类别保留显示，新保存必须符合当前配置；撤销仅本地操作，保存后操作栈清空，服务器版本继续保留。

状态/几何5项、相关后端66项、标注浏览器25项、类别设置2项、全页界面23项通过。浏览器写入被拦截，不修改真实数据；后台使用主虚拟环境重启加载新校验，未做迁移。详情见docs/development/检测标注操作说明.md及本轮JSON验收记录。

## 2026-10-13 UI统一

ui-skills MCP 的 baseline-ui/fixing-accessibility 已实际读取并用于实施。共享主题位于 frontend/src/ui；主导航、登录/概览、列表工具栏、检测工作台及 SmartEye 六页统一。SmartEye仍使用同源iframe，不需要另起服务，横向菜单保留所有可用功能；Modal/Drawer/AutoComplete复用AntD，图片预览共享组件。台账创建弹窗的输入点击误关闭已先复现后修复。验收范围见 frontend/DESIGN.md 和 docs/testing/ui-verification-2026-10-13.json。

用户已确认模型服务暂未部署；暂不能进行真实检测/Grounding预标注联调。此次无后端或数据库迁移，未提交真实拉取/生成/标注更新；P2记录如下。

## 2026-10-13 P2加载器验收

YOLO/COCO真实加载器验收已完成：采用独立验收Python借用本机CPU Torch 2.6.0、Torchvision 0.21.0，仅在验收环境安装Ultralytics 8.4.176及pycocotools 2.0.11；运行API依赖不变。不加载模型/训练，禁用可选字体下载，真实数据集解析及图片张量读取保持原SDK。

SDK测试先发现并复现YOLO缺val和path='.'依赖工作目录的问题。新YAML移除path，缺失train/val/test用空列表；130项完整回归通过，包括4项PG、6项真实加载器。test-img现有两张已审核图片共五个“人”框由SDK实际读取，核对本轮前后核心行哈希不变；新增一份仅训练组的新格式快照，旧快照哈希保留。旧冻结包仍为其原格式，不能自动改写，若需新YAML应重新冻结。

P2-03按Ultralytics及COCO通用加载器验收；用户专用脚本/实际训练仍待P4。P2-04父图来源分组/泄漏校准、真实独立测试集及P3模型服务地址仍待提供/完善。复现方式见[导出文档](../api/检测标注与导出.md)，证据见[加载器结果](../testing/p2-loader-verification-2026-10-13.json)。

## 2026-10-13 P2实施后（历史时点）

用户最新指定test-img项目标注“人”，不限定红色上衣；保留六张原图，只为该项目补labels=['人']。SVG工作台、负样本确认、版本保护、历史只读、审核/驳回、单条/批量计数及事件已实现。YOLO/COCO实际图片包与manifest、标签过滤、显式划分/冻结/重复下载、类别/设备/质量/来源/状态分布已实现。缺省元数据明确为未知/未评分；冻结做保守近重复跨组检查，不代表任意裁剪无泄漏。

主库006→007已备份12张原表并确认旧行哈希不变；增加2个版本索引与annotation_events/export_snapshots。当前19项目、3用户、6资产保留（用户新增test-img后共19项目）。一张真实图有2个人框、v1—v3历史，最新v3审核通过，其他5张待标注；v2冻结YOLO包在v3修改后字节不变。124项回归包括4项独立PG用例，前端构建/pip check通过；25次运行API和两份浏览器实际图片包的哈希/坐标验证通过。ZIP CPU/读写在工作线程，不阻塞主请求循环。

P2-01/02/05按上传图片范围完成；P2-03目标训练加载器与P2-04裁剪父图分组、规则校准、真实独立测试集仍待验收/完善。自动模型标注、质量生成和自动核验仍按P3，训练按P4；当前单张训练快照不等于可评估数据集。无引用原图及ZIP清理继续保守保留，lint及主包大小有警告。没有提交推送或改原SmartEye目录。

入口：[检测标注与导出](../api/检测标注与导出.md)、[P2证据](../testing/p2-verification-2026-10-13.json)、[当前TODO](TODO.md)。新工作台为frontend/src/pages/DetectionWorkspace.tsx，版本服务services/annotations.py，图片包服务services/detection_export.py。更新接口必须带base_version；COCO默认ZIP，旧纯JSON调用追加package=false。

## 2026-10-13 P1实施后（历史时点）

最新状态以[当前TODO](TODO.md)及[P1证据](../testing/p1-verification-2026-10-13.json)为准；本记录后面的P0与接手检查保留原时点。

正式SmartEye源码与六组原页面已内置：主FastAPI统一认证后承载48条原/新增路由，主React同一次构建；不再单独启动SmartEye。仅下载/关联项目可选，成功图片共享主LocalStorage哈希原图并即时建立Asset。原目录未修改。主库005→006后旧9表行哈希不变，18项目/3用户保留；只读复制99平台和401告警配置到独立smarteye schema，旧历史任务/图片未迁移。

103项pytest全部通过，包括3项显式启用的独立PostgreSQL用例与14项原解析/去重回归；pip check及主前端/SmartEye构建通过。浏览器六页面、用途/项目选择、任务面板已检查，最终重启后13次运行API检查通过。任务进度/轮次/监控日志持久保存，手动重试复用图片，取消及旧轮次迟到图片不能关联；独立调度不互相停止，重启保持配置但默认停用。

P1-01/03/04/05按本地与隔离测试范围完成；P1-02代码已内置，真实子平台登录/查询/日志/长时监控、验证码模型与付费AI效果仍待验收。当前限单后端进程和本地同卷硬链接，EOS/Docker未实测；AI取消不撤销远端计费，统一任务面板是取消状态依据。无引用原图及ZIP暂保留，需关注磁盘容量。下步按TODO进入P2检测画框、版本/审核计数及真实训练导出；不把JSON标注回读当检测工作台验收。

操作入口：[SmartEye内置说明](../../backend/backend/integrations/smarteye/README.md)、[接口清单](../api/SmartEye内置接口.md)。代码位于`backend/backend/integrations/smarteye/`与`frontend/smarteye/`，历史假数据服务文件保留但不再注册到主路由。

## 2026-10-13 P0实施后（历史时点）

最新状态以[当前TODO](TODO.md)及[P0验证证据](../testing/p0-verification-2026-10-13.json)为准，下表及文末的“未修改/未测试”描述仅记录接手时情况。

主后端已固定使用`backend/backend/.venv`和PowerShell7启动脚本；前端build、89文件Python语法、pip check通过。id/UUID/状态/标签/批量契约及错误码已对齐，标注/SmartEye认证与资源范围已补齐；图片统一存储、认证读取、共享删除和PostgreSQL并发上传/删除回归通过。注册采用默认单租户，JWT密钥来自本地配置，旧登录可能需要重新登录。

61项pytest全部通过，含显式启用的2项独立PostgreSQL用例；运行服务合成图片/JSON标注/审核/删除24次检查通过，测试项目已清理，原18项目、3用户保留。10表备份恢复及哈希核对完成，现库为`postgres`、005，仅新增哈希唯一约束，没有恢复过时表。已建立源文件ZIP基线及本地Git，尚未提交/推送。

P0-01/02/04/06已按范围验收；P0-03最终任务模型/初始化和P0-05真实SmartEye共享存储仍部分完成。真实SmartEye任务列表仍500；完整内置、可靠任务、画框工作台、版本统计和YOLO/COCO仍需P1/P2。Docker仅修配置，未运行；EOS/模型/训练未实测。物理无引用图片暂保留，后续需要清理机制。

先前“运行响应缺少X-Process-Time”的结论来自大小写敏感查找，已撤回；本轮不区分大小写的检查确认响应头正常。

## 代码入口与参考资料

| 内容 | 当前定位 | 说明 |
|---|---|---|
| 主前端 | `frontend/` | React、TypeScript、Ant Design；`npm run dev`，配置端口3000 |
| 主后端 | `backend/backend/main.py` | FastAPI；当前使用`backend/start_server.ps1`固定目录与项目.venv；详见主后端README |
| 旧后端及前端 | `backend/main.py`、`backend/frontend/` | 前者只注册SmartEye导入路由；后者是Vue脚手架，与主React前端并存，暂未删除 |
| 额外嵌套代码 | `backend/backend/backend/` | 部分旧集成实现，当前主路由未注册；已修复语法阻断，保留待后续整合 |
| SmartEye工作目录 | `F:/hzxf/auto-img-downloader` | Flask入口`app.py`，React前端`download_img-main/`；有未提交修改，迁移时保留原目录 |
| SmartEye正式源码参考 | `F:/hzxf/smarteye-code` | 当前本地克隆提交`ebee9a9`；用户指定仓库为https://gitee.com/suLemonTree/auto-img-downloader，本次未联网更新 |

两份SmartEye源码的`app.py`、侧栏及台账下载核心代码在本次抽查中一致；台账文件原始字节差异为换行符差异。这不代表两份目录全部一致。原工作目录HEAD为`8cbfd97`，不能仅根据该提交号判断工作文件版本。

需求以用户最近确认的决定为准；[需求梳理v0.3](../requirements/数据管理平台_需求梳理_v0.3.md)、[实现方案v0.1](../design/数据管理平台_实现方案_v0.1.md)保留长期需求。[SmartEye集成架构](../design/架构设计-SmartEye集成方案.md)、[数据库详细设计](../design/数据库详细设计_v1.0.md)、[API规范](../api/API接口规范_v1.0.yaml)、[测试计划](../testing/测试计划_v1.0.md)中的Vue、多租户、导入步骤及接口表需要随实现校正。

## 接手时检查结果（修复前基线）

| 检查 | 本次结果与影响 |
|---|---|
| 后端运行 | `http://127.0.0.1:8000/health`、`/openapi.json`均返回200；健康接口没有数据库探测，不能据此认定所有API正常 |
| 前端构建 | `npm run build`失败：DashboardPage有未使用导入、`Project.total_assets`类型缺失；DetectionQuery有两个未使用导入和一个未使用参数，共5处TypeScript错误 |
| 前端静态检查 | `npm run lint`退出0，但存在未使用变量和React Hook等警告，不等同于构建或业务通过 |
| 前后端字段 | 运行中OpenAPI的项目、资产、标注响应主键均为`id`；前端分别使用`project_id`、`asset_id`、`annotation_id`。项目选择、列表操作、删除和审核等调用需要一起对齐；SmartEye的ImportRequest仍将project_id定义为整数，项目主键已为UUID |
| SmartEye接入 | 现有平台列表实际返回小红书、抖音、微博、知乎；服务调用mock下载，检测查询生成模拟记录。真实SmartEye功能尚未集成 |
| SmartEye任务 | `GET /api/smarteye/tasks?limit=1`实际返回500。TODO核对时只读确认当前库为postgres、迁移004，SmartEye任务/下载及训练/模型共5张ORM表不存在；主路由仍使用已删除的任务表，需与最终模型统一 |
| 标注功能 | 页面主要是列表、JSON详情及审核按钮，尚无绘制检测框的工作台。未登录请求`GET /api/annotations`返回200，路由缺少身份依赖；审核状态筛选参数也未被后端接收 |
| 存储与删除 | 上传直接写UPLOAD_DIR，绕过get_storage；单资产删除直接os.remove(blob_path)，没有检查其他引用。需在图片复用上线前修复 |
| EOS与导出 | EOSStorage当前按S3协议实现，真实EOS兼容性未验证。JSON/CSV/COCO空项目响应200，但COCO多框与类别映射定向测试失败；YOLO返回400，尚无有效数据包与冻结数据集流程 |
| Python语法 | 扫描86个后端Python文件，1个解析失败：`backend/backend/backend/services/smarteye_import_service.py:48`中`storage_config = platform.storage_config or`不完整；该文件当前不在主应用路由中 |
| 本地依赖 | 首次受限环境中默认Python看不到FastAPI等用户目录依赖；TODO核对时完整权限环境确认`D:/anaconda/python.exe`具备所需包，服务从`backend/backend/`使用该Python运行。项目venv仍未完整，依赖版本与requirements尚需统一；不可将先前的环境观察当作当前代码缺包事实 |
| 版本管理 | 主项目根目录及当前前后端目录未发现Git仓库；正式修改前应建立可恢复的版本基线，保留现有代码与数据 |

[PROJECT_DELIVERY.md](../archive/2026-10-13/PROJECT_DELIVERY.md)、[SMARTEYE_INTEGRATION.md](../archive/2026-10-13/SMARTEYE_INTEGRATION.md)及多个历史完成报告中的“生产就绪”“全部联调通过”不能作为本次验收依据。`test_all_apis.py`主要检查接口状态，甚至将标注接口404计为通过，没有覆盖真实拉取、上传、编辑、导出完整流程。

原SmartEye源码实际包含六个主要页面：检测查询、台账查询、台账下载、智搜下载、AI生图、执行工具任务；还包含台账详情/日志、图片去重、多平台独立调度器与监控任务API。迁移时复用这些现有实现，不能用模拟查询页替代。侧栏中只有占位链接的系统概览、设备列表等，不能算作已有可用功能。

## 后续开发顺序与验收

用户已指定后续开发按TODO执行。以[当前TODO](TODO.md)的P0—P5及其验收条件为准；原114项状态与本次证据均在其中，下列四步保留为总体方向。

1. 固定主入口和可恢复的版本基线，修复构建及API字段契约。验收：前端可构建，使用真实响应完成项目选择、图片列表和标注列表；有效UUID不再因接口错配返回422。
2. 核对现库迁移版本、表结构与模型，统一图片写入、读取和删除路径，补齐标注API认证。验收：一图被多个位置引用时只存一份，删除其中一个引用后其他位置仍能读取；未登录不能读取或修改标注。
3. 按原源码功能清单内置SmartEye，先打通真实查询/下载及“直接用于标注”，继续保留台账、智搜、AI生图、工具与独立调度功能。验收：统一启动入口；平台来自真实配置；实际产生可读取图片，关联项目无需二次导入或复制。
4. 实现检测标注工作台、选标签导出及必要版本记录。验收：拉取或上传一张图片→画框→保存→审核→重新打开→导出YOLO/COCO；未选标签框不导出，图片仍保留。再接模型自动标注/审核与训练。

TODO核对新增的直接证据还包括：存储pytest为2通过5失败，零拷贝测试收集失败；存储`../`路径可越界；管理员依赖访问不存在的is_superuser属性；主应用没有图片读取路由。登录、Token失效检查和部分读取接口通过。原报告中的模块完成标记需要按这些证据更新，完整结果见[核对记录](../testing/todo-audit-2026-10-13.json)。

首次接手检查仅新增本记录；后续文档整理调整了文档位置、入口README和本地链接。两次工作均未修改业务逻辑、重启服务或运行数据库迁移。没有运行会创建数据的API脚本或可能drop_all的测试fixture；真实拉取、模型推理、EOS、训练、浏览器交互和性能尚未验收。

迁移003会删除并重建SmartEye任务/下载表，004会删除这些表；现有测试数据库fixture也包含drop_all。后续先核实目标、备份及数据保留要求，再实施迁移和数据库测试。
