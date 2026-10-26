# GPU资源管理

管理员从侧栏“GPU资源”进入 `/gpu-resources`。支持填写IP、SSH端口、账号和密码添加多台Linux NVIDIA服务器，通过ssh-manager MCP只读获取GPU型号、索引、UUID、驱动、显存、利用率、温度、功耗、MIG状态及计算进程。已有`绍兴a100`连接继续保留。

## 使用

- 点击“新增服务器”，填写名称、IPv4/IPv6地址、端口（默认22）、用户名（默认root）和密码；可先“测试连接”，再保存。未保存测试不创建数据库记录。
- 保存后自动切换到新服务器并采集。选择其他服务器时清空旧画面，旧请求即使返回也不会覆盖新服务器。
- 自建连接支持编辑、启停采集和删除；删除仅移除平台记录及保存的密码，不停止远端进程。原ssh-manager连接由现有配置管理，不在此编辑或删除。
- 编辑密码留空表示保留，重填表示替换，可明确清除；密码不回显，不保存在浏览器存储。修改IP、端口或用户名时必须重填密码，避免旧密码发送到另一个目标。
- 首次打开采集，默认每30秒自动刷新，可以关闭并手动刷新。页面隐藏时暂停自动采集，不并行发送刷新请求。
- 更新时间是成功采集完成的时间；后端按服务器及修订隔离15秒缓存，显示缓存标识。手动刷新也遵守此限制，修改连接或密码会使旧缓存失效。
- 刷新失败保留上次数据并提示过期；首次失败不显示虚构的零卡或零显存。未知指标显示 `--`。
- 进程按GPU UUID关联，同一PID可以使用多张卡。只采集计算进程，不含全部图形进程；采集失败与没有计算进程分别显示。
- 剩余显存采用驱动的 `memory.free`，因驱动预留等原因，不简单用总量减占用计算。低利用率不表示没有模型驻留，剩余显存不是训练可调度配额。
- MIG启用或状态未知时提示；物理卡概览不能作为MIG实例配额依据。

## 配置与接口

主后端环境变量（参考 `.env.example`）：

```dotenv
GPU_SSH_MANAGER_ENTRY=/path/to/mcp-ssh-manager/src/index.js
GPU_SSH_NODE_BINARY=node
GPU_SERVER_NAME=绍兴a100
# 可选独立Fernet密钥
GPU_CREDENTIAL_KEY=
```

入口必须为绝对路径。本机安装Node及ssh-manager自身依赖。已有连接继续由ssh-manager管理；页面新增的密码以Fernet认证加密后存入`gpu_servers`，接口只返回`has_password`。部署到其他机器时重新配置入口，不依赖Codex聊天保持打开。

`GPU_CREDENTIAL_KEY`留空时按用途从现有JWT密钥派生；更换JWT密钥会使旧GPU密码无法解密，需要重填。配置独立密钥后请与数据库一同妥善备份；改变派生方式或轮换凭据密钥也需要重填。密码、密文及底层SSH错误不在响应中回显。

跨网络访问页面时使用HTTPS。

平台通过MCP SDK stdio调用`ssh_execute`，仅执行固定Python3包装的两条`nvidia-smi --query-* --format=csv,noheader,nounits`。动态凭据从标准输入进入桥接器，在MCP子进程中临时定义只读连接，不写明文配置文件、不更改全局ssh-manager连接。页面不能输入命令或PID操作。远端需要Python3及nvidia-smi；没有部署远端代理、修改驱动或调整服务。

所有接口仅管理员使用：未认证401，非管理员403，跨租户自建连接404。HTTP缓存禁用。

| 方法 | 路径（前缀`/api/gpu-resources`） | 用途 |
|---|---|---|
| GET | 空路径 | 兼容原默认ssh-manager连接采集 |
| GET / POST | `/servers` | 列出默认及自建连接 / 保存自建连接 |
| POST | `/servers/test` | 测试未保存的IP/账号/密码，不创建记录 |
| PATCH / DELETE | `/servers/{id}` | 修改 / 删除自建连接 |
| GET | `/servers/{id}/resources` | 按保存连接采集，修改/停用/删除后拒绝迟到结果 |

采集成功返回`server_name`、UTC `collected_at`、`cached`、`gpus`、`processes`、`processes_complete`、`warnings`。单位为MiB、百分比、摄氏度和W，未知数值返回null。参数无效422，未设置密码或配置已变化409，配置/连接/输出异常503，整体采集超时504。

017增量只添加`gpu_servers`表，每租户最多200条自建连接。15秒缓存合并并发采集，缓存中不保留密码，短暂缓存失败避免断连时连续SSH。缓存限单后端，不保存历史；没有训练调度、资源预留、抢占、停止进程或历史曲线。

## 验证入口

- 后端专项：`backend/backend/tests/test_gpu_resources.py`、`test_gpu_servers.py`、`test_gpu_servers_postgres.py`及`test_gpu_connections_bridge.py`。
- MCP桥接：`backend/backend/scripts/gpu_mcp_bridge.mjs`，复用ssh-manager安装的SDK。
- 前端状态测试：`node scripts/verify_gpu_resources.cjs`，检查实际`GpuSnapshot`组件，React/AntD使用内存替身；不是浏览器实测。
- 新连接浏览器夹具：`scripts/verify_gpu_servers.cjs`，全部写操作拦截。
- 实际MCP动态密码链路：`GPU_MCP_INTEGRATION=1`运行`test_gpu_mcp_integration.py`，使用本地临时SSH端点，不接触用户服务器。
- 页面检查：`scripts/verify_gpu_resources_browser.cjs`，通过Playwright CLI执行，业务写入拦截。
- [多服务器验收](../testing/gpu-server-connections-verification-2026-10-16.json)；[原只读GPU页面验收](../testing/gpu-resources-verification-2026-10-15.json)。
