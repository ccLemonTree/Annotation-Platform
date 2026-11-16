"""Storage for immutable frozen-manifest JSON."""
import json
from io import BytesIO

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from services.storage import get_storage


def manifest_summary(manifest):
    return dict(training=manifest.get('training'), version_label=manifest.get('version_label'),
                asset_count=len(manifest.get('assets', [])))


async def write_manifest(manifest, path):
    raw = await run_in_threadpool(json.dumps, manifest, ensure_ascii=False, separators=(',', ':'))
    with BytesIO(raw.encode('utf-8')) as stream:
        await get_storage().save_file(stream, path)


async def read_manifest(path, legacy=None):
    if path:
        try:
            with await get_storage().get_file(path) as stream:
                legacy = await run_in_threadpool(json.load, stream)
        except (OSError, ValueError):
            raise HTTPException(503, '读取冻结清单文件失败') from None
    if not isinstance(legacy, dict):
        raise HTTPException(503, '冻结清单缺失或格式无效')
    return legacy
