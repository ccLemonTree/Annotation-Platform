"""Install an opt-in MCP PTY extension hook, preserving a byte-for-byte backup."""
from pathlib import Path
import hashlib
import sys

MARKER='// Platform optional PTY extension (no effect unless explicitly enabled).'
ANCHOR='// Start the server\nasync function main() {'
HOOK='''// Platform optional PTY extension (no effect unless explicitly enabled).
if (process.env.PLATFORM_SSH_TERMINAL_EXTENSION) {
  const { pathToFileURL } = await import('node:url');
  const { registerTerminalTools } = await import(pathToFileURL(process.env.PLATFORM_SSH_TERMINAL_EXTENSION).href);
  registerTerminalTools(server, { z, getConnection, getServerConfig,
    touch: () => { for (const key of connectionTimestamps.keys()) connectionTimestamps.set(key, Date.now()); } });
}

'''


def patch_source(raw):
    text=raw.decode('utf-8')
    if MARKER in text:return raw
    newline='\r\n' if '\r\n' in text else '\n'
    normalized=text.replace('\r\n','\n')
    if normalized.count(ANCHOR)!=1 or any(symbol not in normalized for symbol in (
        'async function getConnection(', 'async function getServerConfig(', 'const connectionTimestamps = new Map()', 'const server = new McpServer(')):
        raise ValueError('ssh-manager版本不兼容，未修改入口')
    return normalized.replace(ANCHOR,HOOK+ANCHOR).replace('\n',newline).encode('utf-8')


def install(entry,backup_dir):
    entry=Path(entry).resolve();original=entry.read_bytes();patched=patch_source(original)
    if patched==original:return dict(changed=False,entry=str(entry))
    backup_dir=Path(backup_dir);backup_dir.mkdir(parents=True,exist_ok=True)
    backup=backup_dir/('index-'+hashlib.sha256(original).hexdigest()[:16]+'.js')
    if backup.exists() and backup.read_bytes()!=original:raise ValueError('备份内容冲突，未修改入口')
    backup.write_bytes(original)
    temporary=entry.with_suffix('.platform-pty.tmp');temporary.write_bytes(patched);temporary.replace(entry)
    return dict(changed=True,entry=str(entry),backup=str(backup))


if __name__=='__main__':
    import json
    print(json.dumps(install(sys.argv[1],sys.argv[2]),ensure_ascii=False))
