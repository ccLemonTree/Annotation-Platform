from integrations.smarteye.runtime import cached_image, response_bytes
from integrations.smarteye.configuration import get_env
from integrations.smarteye.runtime import save_image_content, get_runtime, actor, execution
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from integrations.smarteye.api.db_utils import (
    create_ai_image_task,
    create_ai_image_task_items,
    delete_ai_image_tasks,
    ensure_ai_image_tables,
    get_ai_image_history,
    get_ai_image_task_detail,
    get_expired_ai_image_tasks,
    update_ai_image_task,
    update_ai_image_task_item,
)
from integrations.smarteye.api.utils import logger


AI_IMAGE_DOWNLOAD_ROOT = Path(str(download_root())) / "ai_gen"


AI_IMAGE_PUBLIC_BASE_URL = get_env("AI_IMAGE_PUBLIC_BASE_URL", "").rstrip("/")
VOLCENGINE_AK = get_env("VOLCENGINE_AK") or get_env("VOLC_ACCESS_KEY") or get_env("ARK_ACCESS_KEY") or ""
VOLCENGINE_SK = get_env("VOLCENGINE_SK") or get_env("VOLC_SECRET_KEY") or get_env("ARK_SECRET_KEY") or ""
VOLCENGINE_REGION = get_env("VOLCENGINE_REGION", "cn-north-1")
VOLCENGINE_SERVICE = get_env("VOLCENGINE_SERVICE", "cv")
VOLCENGINE_HOST = "visual.volcengineapi.com"
VOLCENGINE_VERSION = "2022-08-31"
VOLCENGINE_REQ_KEY = "jimeng_seedream46_cvtob"
AI_IMAGE_RETENTION_DAYS = int(get_env("AI_IMAGE_RETENTION_DAYS", "7"))
AI_IMAGE_CLEANUP_INTERVAL_SECONDS = int(get_env("AI_IMAGE_CLEANUP_INTERVAL_SECONDS", "3600"))
AI_IMAGE_BATCH_SIZE = 10
AI_IMAGE_MAX_OUTPUT_COUNT = 4
AI_IMAGE_POLL_INTERVAL_SECONDS = 3
AI_IMAGE_POLL_TIMEOUT_SECONDS = 600
AI_IMAGE_MAX_REPEAT_COUNT = 500

ai_image_runtime_tasks: Dict[str, Dict[str, Any]] = {}
_cleanup_thread_started = False


def now_local() -> dt.datetime:
    return dt.datetime.now()


def batch_assets(assets: List[dict], batch_size: int = AI_IMAGE_BATCH_SIZE) -> List[List[dict]]:
    if batch_size <= 0:
        batch_size = AI_IMAGE_BATCH_SIZE
    return [assets[i:i + batch_size] for i in range(0, len(assets), batch_size)]


def build_text_repeat_items(repeat_count: int) -> List[dict]:
    count = max(1, min(int(repeat_count or 1), AI_IMAGE_MAX_REPEAT_COUNT))
    return [
        {
            "batch_index": idx + 1,
            "asset_count": 0,
            "assets": [],
        }
        for idx in range(count)
    ]


def summarize_item_statuses(items: List[dict]) -> Dict[str, int | str]:
    total = len(items)
    success_count = sum(1 for item in items if item.get("status") == "success")
    failed_count = sum(1 for item in items if item.get("status") == "failed")
    completed_count = success_count + failed_count

    if total == 0:
        task_status = "pending"
    elif completed_count == 0:
        task_status = "running"
    elif success_count == total:
        task_status = "completed"
    elif failed_count == total:
        task_status = "failed"
    else:
        task_status = "partial_success"

    return {
        "task_status": task_status,
        "success_count": success_count,
        "failed_count": failed_count,
        "completed_count": completed_count,
        "total_count": total,
    }


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sign_request(method: str, path: str, query_params: dict, body: str, headers: dict) -> dict:
    query_string = urlencode(sorted(query_params.items()))
    canonical_headers = (
        f"content-type:{headers['Content-Type']}\n"
        f"host:{headers['Host']}\n"
        f"x-content-sha256:{headers['X-Content-Sha256']}\n"
        f"x-date:{headers['X-Date']}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = "\n".join([
        method,
        path,
        query_string,
        canonical_headers,
        signed_headers,
        headers["X-Content-Sha256"],
    ])

    algorithm = "HMAC-SHA256"
    date_short = headers["X-Date"][:8]
    credential_scope = f"{date_short}/{VOLCENGINE_REGION}/{VOLCENGINE_SERVICE}/request"
    string_to_sign = "\n".join([
        algorithm,
        headers["X-Date"],
        credential_scope,
        _sha256_hex(canonical_request),
    ])

    k_date = _hmac_sha256(VOLCENGINE_SK.encode("utf-8"), date_short)
    k_region = hmac.new(k_date, VOLCENGINE_REGION.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, VOLCENGINE_SERVICE.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"{algorithm} "
        f"Credential={VOLCENGINE_AK}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return headers


def _volc_post(action: str, payload: dict) -> dict:
    if not VOLCENGINE_AK or not VOLCENGINE_SK:
        raise RuntimeError("未配置 VOLCENGINE_AK / VOLCENGINE_SK")

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    headers = {
        "Host": VOLCENGINE_HOST,
        "Content-Type": "application/json",
        "X-Date": timestamp,
        "X-Content-Sha256": content_sha,
    }
    headers = _sign_request(
        method="POST",
        path="/",
        query_params={"Action": action, "Version": VOLCENGINE_VERSION},
        body=body,
        headers=headers,
    )
    response = requests.post(
        f"https://{VOLCENGINE_HOST}/",
        params={"Action": action, "Version": VOLCENGINE_VERSION},
        data=body.encode("utf-8"),
        headers=headers,
        timeout=60,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        error_body = response.text
        logger.error(f"火山引擎 API 错误 [{action}]: {e}, 响应内容: {error_body}")
        raise RuntimeError(f"火山引擎 API 返回错误: {error_body}")
    return response.json()


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".", " ") else "_" for ch in (name or "asset")).strip() or "asset"


def _ensure_task_dirs(task_id: str) -> dict:
    root = AI_IMAGE_DOWNLOAD_ROOT / task_id
    inputs_dir = root / "inputs"
    outputs_dir = root / "outputs"
    meta_dir = root / "meta"
    for path in (root, inputs_dir, outputs_dir, meta_dir):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "inputs": inputs_dir,
        "outputs": outputs_dir,
        "meta": meta_dir,
    }


def _resolve_public_base_url(preferred: str = "") -> str:
    return (preferred or AI_IMAGE_PUBLIC_BASE_URL or "").rstrip("/")


def _build_asset_public_url(public_base_url: str, task_id: str, relative_name: str) -> str:
    if not public_base_url:
        return ""
    return f"{public_base_url}/api/smarteye/ai-image/assets/{task_id}/{relative_name}"


def build_asset_public_url(public_base_url: str, task_id: str, relative_name: str) -> str:
    return _build_asset_public_url(public_base_url, task_id, relative_name)


def save_uploaded_files(task_id: str, files, public_base_url: str = "") -> List[dict]:
    dirs = _ensure_task_dirs(task_id)
    saved_assets = []
    for storage in files:
        if not storage or not storage.filename:
            continue
        filename = _safe_name(storage.filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        save_path = dirs["inputs"] / unique_name
        from config.settings import settings
        if not save_image_content(save_path, storage.stream.read(settings.MAX_UPLOAD_SIZE + 1)):
            continue
        relative_name = f"inputs/{unique_name}"
        preview_url = f"/api/smarteye/ai-image/assets/{task_id}/{relative_name}"
        saved_assets.append({
            "id": uuid.uuid4().hex,
            "name": filename,
            "source_type": "upload",
            "local_path": str(save_path),
            "relative_name": relative_name,
            "preview_url": preview_url,
            "url": _build_asset_public_url(public_base_url, task_id, relative_name),
        })
    return saved_assets


def copy_existing_assets(task_id: str, assets: List[dict], public_base_url: str = "") -> List[dict]:
    dirs = _ensure_task_dirs(task_id)
    imported = []
    for asset in assets:
        source_url = asset.get("url") or ""
        if not source_url:
            continue
        filename = _safe_name(asset.get("name") or source_url.split("/")[-1].split("?")[0] or "asset.jpg")
        suffix = Path(filename).suffix or ".jpg"
        unique_name = f"{uuid.uuid4().hex}{suffix}"
        save_path = dirs["inputs"] / unique_name
        try:
            import re
            match = re.search(r'/api/smarteye/ai-image/assets/([A-Za-z0-9_-]+)/(.+)', source_url)
            if match:
                source_task = get_runtime().get(match.group(1))
                if not source_task or source_task['tenant_id'] != actor.get()['tenant_id']:
                    raise ValueError('素材不存在')
                original = (AI_IMAGE_DOWNLOAD_ROOT / match.group(1) / match.group(2)).resolve()
                if not original.is_relative_to(AI_IMAGE_DOWNLOAD_ROOT.resolve()):
                    raise ValueError('非法素材路径')
                content = original.read_bytes()
            else:
                response = requests.get(source_url, timeout=30)
                response.raise_for_status()
                content = response.content
            if not save_image_content(save_path, content):
                continue
            relative_name = f"inputs/{unique_name}"
            preview_url = f"/api/smarteye/ai-image/assets/{task_id}/{relative_name}"
            imported.append({
                "id": uuid.uuid4().hex,
                "name": filename,
                "source_type": asset.get("source_type", "existing"),
                "local_path": str(save_path),
                "relative_name": relative_name,
                "origin_url": source_url,
                "preview_url": preview_url,
                "url": _build_asset_public_url(public_base_url, task_id, relative_name),
            })
        except Exception as exc:
            logger.warning(f"导入已有图片失败 [{source_url}]: {exc}")
    return imported


def import_url_assets(task_id: str, urls: List[str], public_base_url: str = "") -> List[dict]:
    return copy_existing_assets(
        task_id,
        [{"url": url, "name": url.split("/")[-1].split("?")[0], "source_type": "url"} for url in urls if url],
        public_base_url,
    )


def _prepare_prompt(prompt: str, output_count: int) -> str:
    prompt = (prompt or "").strip()
    if output_count > 1 and f"{output_count}张" not in prompt:
        prompt = f"{prompt}\n请生成 {output_count} 张风格一致但细节略有差异的图片。".strip()
    return prompt


def _build_submit_payload(task: dict, item: dict) -> dict:
    params = task.get("params", {})
    output_count = int(task.get("output_count", 1) or 1)
    payload = {
        "req_key": VOLCENGINE_REQ_KEY,
        "prompt": _prepare_prompt(task.get("prompt", ""), output_count),
        "force_single": output_count == 1,
    }

    # 宽高优先：用户明确指定宽高时按宽高生成，否则用面积
    if params.get("width") and params.get("height"):
        payload["width"] = int(params["width"])
        payload["height"] = int(params["height"])
    elif params.get("size"):
        payload["size"] = int(params["size"])

    if params.get("scale"):
        payload["scale"] = int(params["scale"])
    if params.get("min_ratio"):
        payload["min_ratio"] = float(params["min_ratio"])
    if params.get("max_ratio"):
        payload["max_ratio"] = float(params["max_ratio"])

    if item.get("assets"):
        binary = []
        root = get_runtime().root / 'smarteye'
        for asset in item['assets']:
            path = Path(asset.get('local_path', '')).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('参考图必须来自已认证的本地素材池')
            binary.append(base64.b64encode(path.read_bytes()).decode('ascii'))
        payload['binary_data_base64'] = binary
    return payload


def _poll_result(volc_task_id: str) -> dict:
    deadline = time.time() + AI_IMAGE_POLL_TIMEOUT_SECONDS
    req_json = json.dumps({"return_url": True}, ensure_ascii=False, separators=(",", ":"))
    while time.time() < deadline:
        current = execution.get()
        if current and get_runtime().get(current[0])['status'] in ('cancelled', 'interrupted'):
            raise RuntimeError('任务已取消或中断')
        response = _volc_post(
            "CVSync2AsyncGetResult",
            {"req_key": VOLCENGINE_REQ_KEY, "task_id": volc_task_id, "req_json": req_json},
        )
        data = response.get("data") or {}
        status = data.get("status", "")
        if response.get("code") == 10000 and status == "done":
            return response
        if status in {"not_found", "expired"}:
            return response
        time.sleep(AI_IMAGE_POLL_INTERVAL_SECONDS)
    return {
        "code": 40800,
        "message": "任务轮询超时",
        "data": {"status": "expired", "image_urls": [], "binary_data_base64": []},
    }


def _save_result_images(task_id: str, item_id: str, image_urls: List[str], binary_images: List[str]) -> List[str]:
    dirs = _ensure_task_dirs(task_id)
    result_files = []

    # Content-Type -> 文件后缀映射
    content_type_suffix = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }

    for idx, url in enumerate(image_urls, start=1):
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()
        # 优先用响应的 Content-Type 判断真实格式，避免 URL 路径里的非标准后缀（如 .image）
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        suffix = content_type_suffix.get(content_type)
        if not suffix:
            url_suffix = Path(url.split("?")[0]).suffix.lower()
            suffix = url_suffix if url_suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp") else ".jpg"
        filename = f"{item_id}_{idx}{suffix}"
        save_path = dirs["outputs"] / filename
        if not save_image_content(save_path, response_bytes(response)):
            continue
        result_files.append(str(save_path))

    for idx, content in enumerate(binary_images, start=len(result_files) + 1):
        filename = f"{item_id}_{idx}.png"
        save_path = dirs["outputs"] / filename
        if not save_image_content(save_path, base64.b64decode(content)):
            continue
        result_files.append(str(save_path))

    return result_files


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_runtime_task(task_id: str):
    detail = get_ai_image_task_detail(task_id)
    if not detail:
        return None

    items = []
    for raw_item in detail.get("items", []):
        result_files = raw_item.get("result_files_json", [])
        items.append({
            "item_id": raw_item["item_id"],
            "batch_index": raw_item["batch_index"],
            "asset_count": raw_item["asset_count"],
            "status": raw_item["item_status"],
            "volc_task_status": raw_item.get("volc_task_status", ""),
            "volc_task_id": raw_item.get("volc_task_id", ""),
            "error_message": raw_item.get("error_message", ""),
            "assets": raw_item.get("asset_list_json", []),
            "result_urls": raw_item.get("result_urls_json", []),
            "result_files": result_files,
            "preview_urls": [f"/api/smarteye/ai-image/assets/{task_id}/outputs/{Path(path).name}" for path in result_files],
        })

    progress_summary = summarize_item_statuses(items)
    runtime = ai_image_runtime_tasks.setdefault(task_id, {})
    runtime.update({
        "task_id": task_id,
        "task_type": detail["task_type"],
        "prompt": detail["prompt"],
        "status": detail["task_status"],
        "output_count": detail["output_count"],
        "repeat_count": detail["repeat_count"],
        "batch_size": detail["batch_size"],
        "total_items": detail["total_items"],
        "completed_items": detail["completed_items"],
        "success_items": detail["success_items"],
        "failed_items": detail["failed_items"],
        "message": detail.get("latest_error") or "",
        "params": detail.get("params_json", {}),
        "progress": int((progress_summary["completed_count"] / detail["total_items"]) * 100) if detail["total_items"] else 0,
        "items": items,
        "created_at": detail["created_at"].timestamp() if hasattr(detail["created_at"], "timestamp") else time.time(),
    })
    return runtime


def cleanup_expired_ai_image_tasks() -> int:
    expired = get_expired_ai_image_tasks()
    if not expired:
        return 0

    task_ids = [row["task_id"] for row in expired]
    for row in expired:
        root_dir = row.get("root_dir") or ""
        if root_dir and os.path.exists(root_dir):
            shutil.rmtree(root_dir, ignore_errors=True)
        ai_image_runtime_tasks.pop(row["task_id"], None)

    delete_ai_image_tasks(task_ids)
    return len(task_ids)


def start_cleanup_loop():
    global _cleanup_thread_started
    if _cleanup_thread_started:
        return

    def _loop():
        while True:
            try:
                cleanup_expired_ai_image_tasks()
            except Exception as exc:
                logger.warning(f"AI 生图定时清理失败: {exc}")
            time.sleep(AI_IMAGE_CLEANUP_INTERVAL_SECONDS)

    thread = threading.Thread(target=_loop, daemon=True, name="ai-image-cleanup")
    thread.start()
    _cleanup_thread_started = True


def create_ai_image_task_payload(data: dict) -> dict:
    task_type = data.get("task_type", "text_to_image")
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")

    output_count = max(1, min(int(data.get("output_count", 1) or 1), AI_IMAGE_MAX_OUTPUT_COUNT))
    repeat_count = max(1, min(int(data.get("repeat_count", 1) or 1), AI_IMAGE_MAX_REPEAT_COUNT))
    batch_size = max(1, min(int(data.get("batch_size", AI_IMAGE_BATCH_SIZE) or AI_IMAGE_BATCH_SIZE), 10))

    task_id = uuid.uuid4().hex
    created_at = now_local()
    expires_at = created_at + dt.timedelta(days=AI_IMAGE_RETENTION_DAYS)
    public_base_url = _resolve_public_base_url(data.get("public_base_url", ""))

    assets = data.get("assets", []) if task_type == "image_to_image" else []
    if task_type == "image_to_image" and not assets:
        raise ValueError("图生图至少需要一张参考图")

    if task_type == "text_to_image":
        built_items = build_text_repeat_items(repeat_count)
    else:
        built_items = [
            {
                "batch_index": idx + 1,
                "asset_count": len(batch),
                "assets": batch,
            }
            for idx, batch in enumerate(batch_assets(assets, batch_size))
        ]

    dirs = _ensure_task_dirs(task_id)
    task = {
        "task_id": task_id,
        "task_type": task_type,
        "prompt": prompt,
        "task_status": "pending",
        "output_count": output_count,
        "repeat_count": repeat_count,
        "batch_size": batch_size,
        "total_items": len(built_items),
        "completed_items": 0,
        "success_items": 0,
        "failed_items": 0,
        "root_dir": str(dirs["root"]),
        "params_json": {
            "size": data.get("size"),
            "width": data.get("width"),
            "height": data.get("height"),
            "scale": data.get("scale"),
            "min_ratio": data.get("min_ratio"),
            "max_ratio": data.get("max_ratio"),
            "output_count": output_count,
            "repeat_count": repeat_count,
            "batch_size": batch_size,
            "public_base_url": public_base_url,
        },
        "request_summary_json": {
            "task_type": task_type,
            "asset_count": len(assets),
        },
        "latest_error": "",
        "created_at": created_at,
        "updated_at": created_at,
        "expires_at": expires_at,
        "params": {
            "size": data.get("size"),
            "width": data.get("width"),
            "height": data.get("height"),
            "scale": data.get("scale"),
            "min_ratio": data.get("min_ratio"),
            "max_ratio": data.get("max_ratio"),
            "public_base_url": public_base_url,
        },
        "items": [
            {
                "item_id": uuid.uuid4().hex,
                "task_id": task_id,
                "batch_index": item["batch_index"],
                "asset_count": item["asset_count"],
                "status": "pending",
                "assets": item.get("assets", []),
                "result_urls": [],
                "result_files": [],
            }
            for item in built_items
        ],
    }
    return task


def persist_task(task: dict):
    ensure_ai_image_tables()
    create_ai_image_task(task)
    create_ai_image_task_items([
        {
            "item_id": item["item_id"],
            "task_id": task["task_id"],
            "batch_index": item["batch_index"],
            "asset_count": item["asset_count"],
            "item_status": item["status"],
            "volc_task_status": "",
            "volc_task_id": "",
            "error_message": "",
            "asset_list_json": item.get("assets", []),
            "request_payload_json": {},
            "submit_response_json": {},
            "result_response_json": {},
            "result_urls_json": [],
            "result_files_json": [],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }
        for item in task["items"]
    ])
    ai_image_runtime_tasks[task["task_id"]] = {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "prompt": task["prompt"],
        "status": task["task_status"],
        "progress": 0,
        "message": "任务已创建",
        "output_count": task["output_count"],
        "repeat_count": task["repeat_count"],
        "batch_size": task["batch_size"],
        "total_items": task["total_items"],
        "completed_items": 0,
        "success_items": 0,
        "failed_items": 0,
        "items": task["items"],
        "created_at": task["created_at"].timestamp(),
    }


def run_ai_image_task(task_id: str):
    detail = get_ai_image_task_detail(task_id)
    if not detail:
        return

    task = {
        "task_id": detail["task_id"],
        "task_type": detail["task_type"],
        "prompt": detail["prompt"],
        "output_count": detail["output_count"],
        "repeat_count": detail["repeat_count"],
        "batch_size": detail["batch_size"],
        "params": detail.get("params_json", {}),
    }

    update_ai_image_task(task_id, {"task_status": "running", "latest_error": ""})
    runtime = ai_image_runtime_tasks.setdefault(task_id, {})
    runtime.update({"status": "running", "message": "任务执行中..."})

    for raw_item in detail.get("items", []):
        current = execution.get()
        if current and get_runtime().get(current[0])['status'] in ('cancelled', 'interrupted'):
            break
        item = {
            "item_id": raw_item["item_id"],
            "batch_index": raw_item["batch_index"],
            "asset_count": raw_item["asset_count"],
            "assets": raw_item.get("asset_list_json", []),
        }
        update_ai_image_task_item(item["item_id"], {"item_status": "running"})
        try:
            payload = _build_submit_payload(task, item)
            update_ai_image_task_item(item["item_id"], {"request_payload_json": payload})

            submit_response = _volc_post("CVSync2AsyncSubmitTask", payload)
            update_ai_image_task_item(item["item_id"], {
                "submit_response_json": submit_response,
                "volc_task_id": (submit_response.get("data") or {}).get("task_id", ""),
                "volc_task_status": "in_queue",
            })

            if submit_response.get("code") != 10000 or not (submit_response.get("data") or {}).get("task_id"):
                raise RuntimeError(submit_response.get("message") or "提交任务失败")

            volc_task_id = submit_response["data"]["task_id"]
            result_response = _poll_result(volc_task_id)
            result_data = result_response.get("data") or {}
            result_urls = result_data.get("image_urls") or []
            binary_images = result_data.get("binary_data_base64") or []

            result_files = _save_result_images(task_id, item["item_id"], result_urls, binary_images)
            update_ai_image_task_item(item["item_id"], {
                "item_status": "success",
                "volc_task_status": result_data.get("status", "done"),
                "result_response_json": result_response,
                "result_urls_json": result_urls,
                "result_files_json": result_files,
                "error_message": "",
            })

            _write_json(
                _ensure_task_dirs(task_id)["meta"] / f"{item['item_id']}_result.json",
                result_response,
            )
        except Exception as exc:
            message = str(exc)
            update_ai_image_task_item(item["item_id"], {
                "item_status": "failed",
                "error_message": message,
            })
            logger.error(f"AI 生图子任务失败 [{task_id}/{item['item_id']}]: {message}")

        task_detail = get_ai_image_task_detail(task_id)
        summary = summarize_item_statuses([
            {"status": "success" if it["item_status"] == "success" else "failed" if it["item_status"] == "failed" else "running"}
            for it in task_detail.get("items", [])
        ])
        update_ai_image_task(task_id, {
            "task_status": summary["task_status"],
            "completed_items": summary["completed_count"],
            "success_items": summary["success_count"],
            "failed_items": summary["failed_count"],
            "latest_error": "" if summary["failed_count"] == 0 else "部分批次失败，请查看详情",
        })
        _refresh_runtime_task(task_id)
        current = execution.get()
        if current:
            get_runtime().patch(task_id, ai_image_runtime_tasks.get(task_id, {}), current[1])

    final_runtime = _refresh_runtime_task(task_id)
    if final_runtime and final_runtime["failed_items"] and final_runtime["success_items"]:
        final_runtime["message"] = "任务完成，部分批次失败"
    elif final_runtime and final_runtime["failed_items"] and not final_runtime["success_items"]:
        final_runtime["message"] = "任务失败"
    elif final_runtime:
        final_runtime["message"] = "任务完成"


def get_runtime_task(task_id: str) -> Optional[dict]:
    cleanup_expired_ai_image_tasks()
    return ai_image_runtime_tasks.get(task_id) or _refresh_runtime_task(task_id)


def get_history(page=1, page_size=20, prompt="", status="", start_date="", end_date=""):
    cleanup_expired_ai_image_tasks()
    return get_ai_image_history(page, page_size, prompt, status, start_date, end_date)


def get_task_detail(task_id: str):
    cleanup_expired_ai_image_tasks()
    return get_ai_image_task_detail(task_id)


def build_reuse_assets(task_id: str, item_id: str = "") -> List[dict]:
    detail = get_ai_image_task_detail(task_id)
    if not detail:
        return []

    reusable = []
    for item in detail.get("items", []):
        if item_id and item["item_id"] != item_id:
            continue
        for path_str in item.get("result_files_json", []):
            path = Path(path_str)
            if path.exists():
                relative_name = f"outputs/{path.name}"
                reusable.append({
                    "id": uuid.uuid4().hex,
                    "name": path.name,
                    "source_type": "history",
                    "local_path": str(path),
                    "relative_name": relative_name,
                    "preview_url": f"/api/smarteye/ai-image/assets/{task_id}/{relative_name}",
                    "url": _build_asset_public_url(
                        detail.get("params_json", {}).get("public_base_url", ""),
                        task_id,
                        relative_name,
                    ),
                })
    return reusable
