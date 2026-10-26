# GPU服务器SSH调试

管理员打开侧栏“SSH调试”（`/gpu-terminal`），或从“GPU资源”中点击当前服务器的“SSH调试”。选择已保存的服务器，点击“连接终端”，即可连续执行命令。支持 top、vim、Tab 补全、方向键、中文输入输出、Ctrl+C，以及随窗口尺寸同步远端 PTY。密码沿用 GPU 连接配置，不在浏览器重新填写。

“发送 Ctrl+C”用于中断前台命令；“清屏”清除显示；“断开连接”关闭当前 shell。刷新、离开页面或连续20分钟没有输入会断开；需要持续运行的任务请使用服务器已有的 tmux 或 nohup。终端按 SSH 账号的真实权限执行命令。

当前支持 Linux，沿用 ssh-manager MCP。只读或受限的 MCP 连接拒绝完整交互终端。每用户最多2个会话，全平台单后端进程最多8个；账号权限、登录有效期和服务器配置变化会关闭会话。不保存终端正文，服务器原有 shell history 规则仍生效。

## 部署

沿用 `GPU_SSH_MANAGER_ENTRY`、`GPU_SSH_NODE_BINARY` 和 `GPU_SERVER_NAME`。在运行后端的主机上，给所配置的 ssh-manager 入口安装可选扩展钩子：

```powershell
python scripts/install_ssh_terminal_extension.py C:/path/to/mcp-ssh-manager/src/index.js backend/backend/.backups/ssh-terminal
```

安装器按原始内容哈希备份入口，重复执行不重复修改；版本锚点不匹配时拒绝修改。只有平台专用 MCP 子进程通过 `PLATFORM_SSH_TERMINAL_EXTENSION` 启用扩展，原有 MCP 进程行为保持不变。更新 ssh-manager 后需重新检查扩展安装。回退时停止平台终端会话，将安装器返回的原始备份恢复到该入口。

反向代理须转发 `/api/gpu-terminal/ws` 的 WebSocket Upgrade，并允许终端长连接；浏览器来源须匹配配置的 CORS 来源或同源 Host。当前 Vite 开发代理已经启用 WebSocket。鉴权通过首帧传递，不把 token 放入 URL。

## 2026-10-16 验证

19项隔离后端测试通过，覆盖鉴权、管理员/租户隔离、权限撤销、输入限制、空闲关闭和会话释放。Node扩展测试及本机临时SSH端点的真实MCP/SSH链路通过，含Unicode、全屏切换、Ctrl+C、窗口同步、错误密码和连接清理。前端双入口构建通过，lint退出0，其他已有页面仍有警告。

绍兴A100真实页面完成 `stty size`、top、Ctrl+C和只读vim检查。390px视口无横向溢出，远端尺寸由36行182列变为32行36列。恢复窗口并退出vim后主动断开，后端记录关闭且专用桥接子进程已释放。截图见 `output/playwright/gpu-ssh-vim-final.png`。开发未启动正式训练，未编辑远端文件或停止业务进程。

当前会话限额按单后端进程计算，多实例部署需先实现共享会话归属与限额。终端刷新后不自动恢复或重放命令。

2026-10-16追加修复：FitAddon直接测量带内边距的外框导致最后一行被裁切，现改为测量内部无内边距容器。浏览器边界断言修复前失败、修复后通过；真实SSH连续输出70行后提示符和光标完整可见，390px窄屏也通过。前端构建与该页面lint通过，截图为`output/playwright/gpu-ssh-bottom-fixed.png`。
