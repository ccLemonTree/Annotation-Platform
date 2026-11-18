"""Isolated Python script sandbox for image and annotation workflow steps."""
import ast
import asyncio
import json
import math
import os
import struct
import sys
import tempfile
from fastapi import HTTPException

MAX_SCRIPT_LENGTH = 16000
MAX_Derived_CROPS = 50
MAX_IPC_BYTES = 32 * 1024 * 1024
ALLOWED_MODULES = {'math', 'json', 're', 'statistics', 'collections', 'itertools', 'PIL', 'imagehash'}
ALLOWED_PIL_SUBMODULES = {'PIL', 'PIL.Image', 'PIL.ImageFilter', 'PIL.ImageStat', 'PIL.ImageOps', 'PIL.ImageDraw'}
FORBIDDEN_NAMES = {
    'eval', 'exec', 'compile', 'open', 'input', 'breakpoint', 'help', 'dir', 'vars',
    'locals', 'globals', 'getattr', 'setattr', 'delattr', 'memoryview', 'type', '__import__',
}

DEFAULT_SCRIPT_TEMPLATE = '''def process(ctx):
    """处理单张图片与标注上下文。
    ctx 包含:
      - ctx["image"]: PIL.Image (RGB)
      - ctx["width"], ctx["height"]: 图片宽高
      - ctx["boxes"]: 检测框列表 [{"label", "x", "y", "width", "height", "score"}]
      - ctx["attributes"]: 属性字典, ctx["description"]: 描述文本
      - ctx["crop_label"]: 裁剪来源类别（若为裁剪图）
    返回字典可包含: passed(bool), reason(str), boxes(list), attributes(dict), description(str), image(PIL.Image), crops(list), branch(str)
    """
    boxes = [b for b in ctx["boxes"] if b["width"] >= 16 and b["height"] >= 16]
    return {
        "passed": True,
        "boxes": boxes,
        "reason": f"保留 {len(boxes)} 个有效目标框",
    }
'''


def validate_script(code: str) -> None:
    text = (code or '').strip()
    if not text:
        raise ValueError('请编写 Python 处理脚本')
    if len(text) > MAX_SCRIPT_LENGTH:
        raise ValueError(f'脚本长度不能超过 {MAX_SCRIPT_LENGTH} 字符')
    try:
        tree = ast.parse(text, mode='exec')
    except SyntaxError as exc:
        line = f'第 {exc.lineno} 行' if exc.lineno else '语法'
        raise ValueError(f'脚本{line}错误：{exc.msg}') from None

    has_process = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'process':
            total_args = len(node.args.posonlyargs) + len(node.args.args)
            if total_args != 1 or node.args.vararg or node.args.kwarg:
                raise ValueError('入口函数签名须为 def process(ctx):')
            has_process = True

    if not has_process:
        raise ValueError('脚本必须定义入口函数 def process(ctx):')

    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)):
            raise ValueError('脚本入口请使用同步函数，不支持 async/await')
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError('脚本不允许使用 global 或 nonlocal')
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in ALLOWED_MODULES:
                    raise ValueError(f'沙箱不允许导入模块 {alias.name}')
                if root == 'PIL' and alias.name not in ALLOWED_PIL_SUBMODULES:
                    raise ValueError(f'沙箱仅允许导入安全 PIL 子模块，不允许 {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise ValueError('沙箱不允许相对导入')
            mod = node.module or ''
            root = mod.split('.')[0]
            if root not in ALLOWED_MODULES:
                raise ValueError(f'沙箱不允许从 {mod or "未知模块"} 导入')
            if root == 'PIL' and mod not in ALLOWED_PIL_SUBMODULES:
                raise ValueError(f'沙箱不允许从 {mod} 导入')
            for alias in node.names:
                if alias.name == '*' or alias.name.startswith('_'):
                    raise ValueError('沙箱不允许使用通配符导入或导入私有成员')
                if mod == 'PIL' and f'PIL.{alias.name}' not in ALLOWED_PIL_SUBMODULES:
                    raise ValueError(f'沙箱不允许导入 PIL.{alias.name}')
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('_'):
                raise ValueError(f'沙箱禁止访问私有或系统属性 {node.attr}')
        elif isinstance(node, ast.Name):
            if node.id.startswith('__') or node.id in FORBIDDEN_NAMES:
                raise ValueError(f'沙箱禁止使用受限标识符 {node.id}')


_WORKER_RUNNER = r'''
import builtins, collections, io, itertools, json, math, re, socket, statistics, struct, sys
import imagehash
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

try:
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (800 * 1024 * 1024, 800 * 1024 * 1024))
except Exception:
    pass

def _blocked(*_a, **_kw):
    raise PermissionError("脚本沙箱禁止网络与外部文件访问")

socket.socket = _blocked
socket.create_connection = _blocked

_MODULES = {
    "math": math,
    "json": json,
    "re": re,
    "statistics": statistics,
    "collections": collections,
    "itertools": itertools,
    "imagehash": imagehash,
    "PIL": sys.modules["PIL"],
    "PIL.Image": Image,
    "PIL.ImageFilter": ImageFilter,
    "PIL.ImageStat": ImageStat,
    "PIL.ImageOps": ImageOps,
    "PIL.ImageDraw": ImageDraw,
}

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level:
        raise ImportError("禁止相对导入")
    if name in _MODULES:
        if fromlist:
            for item in fromlist:
                sub = f"{name}.{item}"
                if name == "PIL" and sub not in _MODULES:
                    raise ImportError(f"不允许导入 {sub}")
        return _MODULES[name]
    raise ImportError(f"沙箱不允许导入模块 {name}")

_SAFE_BUILTINS = {
    k: getattr(builtins, k) for k in (
        "abs", "all", "any", "bool", "bytes", "bytearray", "callable", "chr",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hash", "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
        "map", "max", "min", "next", "oct", "ord", "pow", "range", "repr",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "ZeroDivisionError", "RuntimeError", "StopIteration", "True", "False", "None",
    ) if hasattr(builtins, k)
}
_SAFE_BUILTINS["__import__"] = _safe_import

stdin = sys.stdin.buffer
stdout = sys.stdout.buffer

def _read_exact(n):
    buf = bytearray()
    while len(buf) < n:
        chunk = stdin.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def _encode_img(img):
    if not isinstance(img, Image.Image):
        raise ValueError("返回的图像必须是 PIL.Image 对象")
    converted = img.convert("RGB")
    w, h = converted.size
    if w < 1 or h < 1 or w > 16384 or h > 16384:
        raise ValueError("生成的图像尺寸超出有效范围")
    out = io.BytesIO()
    converted.save(out, format="PNG")
    return out.getvalue(), w, h

while True:
    hdr = _read_exact(8)
    if hdr is None:
        break
    meta_len, img_len = struct.unpack(">II", hdr)
    meta_raw = _read_exact(meta_len)
    img_raw = _read_exact(img_len)
    if meta_raw is None or img_raw is None:
        break
    resp_blobs = []
    logs = []
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
        def _log_print(*args, **kwargs):
            if len(logs) < 20:
                logs.append(" ".join(str(a) for a in args)[:300])
        env_builtins = dict(_SAFE_BUILTINS, print=_log_print)
        ns = {"__builtins__": env_builtins, "Image": Image, "ImageFilter": ImageFilter, "ImageStat": ImageStat, "ImageOps": ImageOps, "ImageDraw": ImageDraw}
        exec(compile(meta["code"], "<workflow_script>", "exec"), ns, ns)
        fn = ns.get("process")
        if not callable(fn):
            raise ValueError("未找到可调用的 process(ctx) 函数")
        with Image.open(io.BytesIO(img_raw)) as opened:
            pic = opened.convert("RGB")
        ctx = {
            "image": pic,
            "width": meta["width"],
            "height": meta["height"],
            "original_name": meta.get("original_name", ""),
            "source_type": meta.get("source_type", ""),
            "crop_label": meta.get("crop_label"),
            "boxes": meta.get("boxes", []),
            "attributes": meta.get("attributes", {}),
            "description": meta.get("description", ""),
        }
        out = fn(ctx)
        if isinstance(out, bool):
            out = {"passed": out}
        if not isinstance(out, dict):
            raise ValueError("process(ctx) 必须返回 dict 或 bool")
        payload = {
            "ok": True,
            "passed": bool(out.get("passed", True)),
            "reason": str(out.get("reason", ""))[:500],
            "logs": logs,
        }
        if "boxes" in out and out["boxes"] is not None:
            if not isinstance(out["boxes"], list) or len(out["boxes"]) > 5000:
                raise ValueError("boxes 必须是不超过 5000 项的列表")
            payload["boxes"] = out["boxes"]
        if "attributes" in out and out["attributes"] is not None:
            if not isinstance(out["attributes"], dict):
                raise ValueError("attributes 必须是字典")
            payload["attributes"] = out["attributes"]
        if "description" in out and out["description"] is not None:
            payload["description"] = str(out["description"])[:8000]
        if "branch" in out and out["branch"] is not None:
            b = out["branch"]
            payload["branch"] = [str(x) for x in b] if isinstance(b, list) else [str(b)]
        derived_meta = []
        if out.get("image") is not None:
            raw_b, dw, dh = _encode_img(out["image"])
            resp_blobs.append(raw_b)
            derived_meta.append({"width": dw, "height": dh, "label": str(out.get("crop_label") or meta.get("crop_label") or "script")[:100]})
        elif out.get("crops") is not None:
            if not isinstance(out["crops"], list) or len(out["crops"]) > 50:
                raise ValueError("crops 列表最多返回 50 张子图")
            for entry in out["crops"]:
                if isinstance(entry, Image.Image):
                    raw_b, dw, dh = _encode_img(entry)
                    label = str(meta.get("crop_label") or "script_crop")[:100]
                    box = None
                elif isinstance(entry, dict) and "image" in entry:
                    raw_b, dw, dh = _encode_img(entry["image"])
                    label = str(entry.get("label") or meta.get("crop_label") or "script_crop")[:100]
                    box = entry.get("box")
                else:
                    raise ValueError("crops 每项须为 PIL.Image 或包含 image 字段的字典")
                resp_blobs.append(raw_b)
                derived_meta.append({"width": dw, "height": dh, "label": label, "box": box})
        if derived_meta:
            payload["derived"] = derived_meta
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)}"[:400], "logs": logs}
        resp_blobs = []
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    framing = [struct.pack(">II", len(body), len(resp_blobs)), body]
    for blob in resp_blobs:
        framing.append(struct.pack(">I", len(blob)))
        framing.append(blob)
    stdout.write(b"".join(framing))
    stdout.flush()
'''


def normalize_boxes(raw_boxes: list, width: int, height: int) -> list[dict]:
    normalized = []
    for row in raw_boxes:
        if not isinstance(row, dict):
            raise HTTPException(400, '脚本返回的检测框元素必须是字典')
        label = str(row.get('label') or '').strip()
        if not label or len(label) > 100:
            raise HTTPException(400, '脚本返回的检测框缺少有效 label')
        try:
            x = float(row['x'])
            y = float(row['y'])
            w = float(row['width'])
            h = float(row['height'])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, '脚本返回的检测框需包含数值型 x, y, width, height') from None
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue
        x1 = max(0.0, min(float(width), x))
        y1 = max(0.0, min(float(height), y))
        x2 = max(0.0, min(float(width), x + w))
        y2 = max(0.0, min(float(height), y + h))
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:
            continue
        box = {'label': label, 'x': round(x1, 2), 'y': round(y1, 2), 'width': round(x2 - x1, 2), 'height': round(y2 - y1, 2)}
        if row.get('score') is not None and not isinstance(row.get('score'), bool):
            score = float(row['score'])
            if math.isfinite(score):
                box['score'] = max(0.0, min(1.0, score))
        normalized.append(box)
    return normalized


class _SandboxSlot:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        current_loop = asyncio.get_running_loop()
        if self.proc is not None and self.proc.returncode is None and self.loop is current_loop and not current_loop.is_closed():
            return self.proc
        if self.proc is not None:
            await self.kill()
        clean_env = {'PYTHONIOENCODING': 'utf-8'}
        if os.name == 'nt':
            for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC'):
                if os.environ.get(key):
                    clean_env[key] = os.environ[key]
        self.loop = current_loop
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, '-E', '-s', '-c', _WORKER_RUNNER,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=clean_env,
            cwd=tempfile.gettempdir(),
        )
        return self.proc

    async def kill(self):
        proc, proc_loop = self.proc, self.loop
        self.proc = None
        self.loop = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.kill()
                if proc_loop is not None and not proc_loop.is_closed() and proc_loop is asyncio.get_running_loop():
                    await proc.wait()
            except Exception:
                pass

    async def execute(self, meta: dict, content: bytes, timeout: float) -> dict:
        async with self.lock:
            proc = await self._ensure_proc()
            meta_bytes = json.dumps(meta, ensure_ascii=False).encode('utf-8')
            header = struct.pack('>II', len(meta_bytes), len(content))
            try:
                async def _communicate():
                    assert proc.stdin and proc.stdout
                    proc.stdin.write(header + meta_bytes + content)
                    await proc.stdin.drain()
                    resp_hdr = await proc.stdout.readexactly(8)
                    body_len, blob_count = struct.unpack('>II', resp_hdr)
                    if body_len > MAX_IPC_BYTES or blob_count > MAX_Derived_CROPS:
                        raise ValueError('脚本输出超出大小上限')
                    body_raw = await proc.stdout.readexactly(body_len)
                    payload = json.loads(body_raw.decode('utf-8'))
                    blobs = []
                    total_bytes = body_len
                    for _ in range(blob_count):
                        (b_len,) = struct.unpack('>I', await proc.stdout.readexactly(4))
                        total_bytes += b_len
                        if total_bytes > MAX_IPC_BYTES:
                            raise ValueError('脚本生成的图像总大小超出 32MB 上限')
                        blobs.append(await proc.stdout.readexactly(b_len))
                    return payload, blobs

                payload, blobs = await asyncio.wait_for(_communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                await self.kill()
                raise HTTPException(400, f'脚本执行超时（超过 {timeout:.1f} 秒），请检查是否存在耗时循环') from None
            except Exception as exc:
                await self.kill()
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(400, f'脚本运行中断：{str(exc)[:200]}') from None

            if not payload.get('ok'):
                raise HTTPException(400, f'脚本执行出错：{payload.get("error", "未知错误")}')
            if payload.get('derived'):
                for item, raw_b in zip(payload['derived'], blobs):
                    item['content'] = raw_b
            return payload


_POOL_SIZE = 2
_slots: list[_SandboxSlot] = []
_rr = 0


async def run_script(
    code: str,
    content: bytes,
    *,
    width: int,
    height: int,
    original_name: str = '',
    source_type: str = '',
    crop_label: str | None = None,
    boxes: list[dict] | None = None,
    attributes: dict | None = None,
    description: str = '',
    timeout_seconds: float = 5.0,
) -> dict:
    global _rr
    try:
        validate_script(code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    if not _slots:
        _slots.extend(_SandboxSlot() for _ in range(_POOL_SIZE))
    slot = _slots[_rr % len(_slots)]
    _rr += 1
    meta = {
        'code': code,
        'width': width,
        'height': height,
        'original_name': original_name,
        'source_type': source_type,
        'crop_label': crop_label,
        'boxes': boxes or [],
        'attributes': attributes or {},
        'description': description or '',
    }
    output = await slot.execute(meta, content, max(0.5, min(15.0, float(timeout_seconds))))
    target_w, target_h = width, height
    if output.get('derived') and len(output['derived']) == 1:
        target_w, target_h = output['derived'][0]['width'], output['derived'][0]['height']
    if 'boxes' in output:
        output['boxes'] = normalize_boxes(output['boxes'], target_w, target_h)
    return output


async def shutdown_sandbox():
    for slot in _slots:
        await slot.kill()
    _slots.clear()
