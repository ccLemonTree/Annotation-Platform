# GPU SSH terminal

Goal: Administrator-only real Linux PTY terminal, shared saved GPU connections, top/vim/Ctrl+C, real output and resize. User explicitly selected full interactive terminal; development uses read-only remote probes.

Architecture: xterm.js + fit addon -> authenticated same-origin WebSocket -> per-session Node MCP client -> existing ssh-manager with an opt-in application-owned PTY tool extension -> native SSH PTY. Credentials stay backend/stdin. No remote agent, training launch, database migration, local direct SSH library, or global tool behavior change.

Decisions: Command execution panel rejected by user. Remote PTY daemon rejected as unnecessary. Existing MCP sessions wrap completion markers and disable echo, so cannot support full-screen applications. Add a tiny optional extension hook to installed ssh-manager; installer backs up original bytes, checks expected source anchors, and patches idempotently. Only platform terminal child enables this hook. Locked dependency versions in frontend package lock. No terminal transcript storage; existing remote shell history behavior remains.

1. Tests first: PTY data/resize/control keys/policy/bounded output/close; WebSocket origin, JWT/admin/tenant, revoked rights/revision, concurrency, client disconnect, expiry and invalid messages; installer idempotence.
2. Implement app-owned MCP extension and stdio bridge. Reuse native getConnection/getServerConfig, refuse restricted/readonly. Bounded buffers and flow control. No password or terminal output forwarded into application logs.
3. Implement WebSocket authentication in first frame (never URL), short auth timeout, per-user/global session limits, permissions and server revision rechecks, idle disconnect. Always close owned MCP children on disconnect/shutdown; no auto reconnect or re-execute.
4. Add lazy SSH debug route/sidebar and GPU page link. Explicit connect/disconnect, server identity, terminal focus, Ctrl+C and clear; responsive fit, cleanup on route/server changes. Reuse current AntD components.
5. Focused tests + build/lint. Local owned SSH integration proves PTY, unicode, alternate screen, Ctrl+C, resize, wrong password and cleanup. Install backed-up opt-in hook. Real Shaoxing check uses read-only commands/top and readonly vim without modifying files, models or other processes. Before backend reload inspect active jobs; verify live page and socket after loading.

Files: backend/api/gpu_terminal_routes.py, services/gpu_terminal.py, scripts/terminal_mcp_extension.mjs, scripts/terminal_mcp_bridge.mjs; scripts/install_ssh_terminal_extension.py; associated backend/Node tests; frontend SSH page/component/styles, route/navigation, GPU resource link and Vite WebSocket proxy. Existing GPU resource/training bridges remain separate and unchanged.

Assumptions: Current single backend process, at most 2 sessions per user / 8 total, idle timeout 20 minutes, bounded frames and acknowledgement-based output backpressure. JWT expiry/revoked roles/server revisions close live terminals. Browser refresh disconnects its owned shell; long-running work requires the user's existing tmux/nohup workflow.
