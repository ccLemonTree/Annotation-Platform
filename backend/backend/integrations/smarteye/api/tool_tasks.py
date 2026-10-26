from integrations.smarteye.runtime import cached_image, response_bytes
from integrations.smarteye.runtime import DurableTasks, tracked_run, save_image_content
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
"""
工具任务模块 - 批量台账图片下载
支持自定义平台、设备、时间范围的批量下载任务
"""
import os
import time
import uuid
import threading
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import as_completed
from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor
from typing import Dict, Any, List, Optional

from integrations.smarteye.api.utils import logger
from integrations.smarteye.api.smart_search import login_platform
from integrations.smarteye.api.db_utils import get_smart_search_platforms
from integrations.smarteye.api.image_dedup import ImageDeduplicator

# 禁用代理

# 配置
DOWNLOAD_PATH = str(download_root())
TOOL_TASK_CONCURRENCY = 10  # 下载并发数
TOOL_TASK_TIMEOUT = 15
TOOL_TASK_RETRY = 2

# 任务状态存储
tool_tasks = DurableTasks("tool")
_lock = threading.Lock()


def _build_headers(base_url: str, token_info: dict) -> dict:
    """构建请求头"""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    return {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json;charset=UTF-8",
        "gray_version": "zuihou",
        "host": parsed.netloc,
        "origin": base_url.rstrip('/'),
        "referer": f"{base_url.rstrip('/')}/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "applicationid": "1",
        "authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
        "tenantid": token_info.get("tenantId", ""),
        "token": token_info.get("token", ""),
        "repeatalarm": "true",  # 台账查询必需
    }


def fetch_page_data(base_url: str, token_info: dict, device_id: str,
                    start_time: str, end_time: str, page: int, page_size: int = 100):
    """请求单页数据"""
    url = f"{base_url.rstrip('/')}/api/schedule/capture/stat/v1/log"
    headers = _build_headers(base_url, token_info)
    payload = {
        "current": page,
        "size": page_size,
        "model": {
            "deviceId": device_id,
            "startTime": start_time,
            "endTime": end_time,
        },
    }

    try:
        rsp = requests.post(url, headers=headers, json=payload,
                           timeout=TOOL_TASK_TIMEOUT, verify=False)
        rsp.raise_for_status()
        data = rsp.json()
        if data.get("code") != 0:
            logger.warning(f"接口返回非 0: {data}")
            return []

        records = data.get("data", {}).get("records", [])
        total = data.get("data", {}).get("total", 0)
        return records, total
    except Exception as e:
        logger.error(f"请求第 {page} 页失败: {e}")
        return [], 0


def download_single_image(img_url: str, save_path: str, task_id: str, collection_name=None) -> bool:
    """下载单张图片"""
    if cached_image(save_path):
        return True
    try:
        for attempt in range(TOOL_TASK_RETRY + 1):
            try:
                rsp = requests.get(img_url, timeout=TOOL_TASK_TIMEOUT,
                                  stream=True, verify=False)
                rsp.raise_for_status()

                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                if not save_image_content(save_path, response_bytes(rsp), collection_name=collection_name):
                    return False

                # 更新进度
                with _lock:
                    if task_id in tool_tasks:
                        tool_tasks[task_id]["downloaded"] += 1

                return True
            except Exception as e:
                if attempt < TOOL_TASK_RETRY:
                    time.sleep(1)
                    continue
                logger.warning(f"下载失败 {img_url}: {e}")
                return False
    except Exception as e:
        logger.error(f"下载异常 {img_url}: {e}")
        return False


def run_tool_task(task_id: str, platform_name: str, device_id: str,
                  start_time: str, end_time: str, max_pages: int = 0):
    """执行工具任务主逻辑"""
    try:
        with _lock:
            tool_tasks[task_id]["status"] = "running"
            tool_tasks[task_id]["message"] = "正在登录平台..."

        # 登录平台
        platforms = get_smart_search_platforms(only_active=False)
        platform = next((p for p in platforms if p["platform_name"] == platform_name), None)
        if not platform:
            raise ValueError(f"未找到平台: {platform_name}")

        base_url = platform["base_url"]
        login_result = login_platform(platform)
        if not login_result.get("success"):
            raise ValueError(f"登录失败: {login_result.get('msg')}")

        token_info = login_result

        with _lock:
            tool_tasks[task_id]["message"] = "正在拉取数据..."

        # 分页获取所有图片记录
        all_records = []
        page = 1
        page_size = 100

        while True:
            if max_pages > 0 and page > max_pages:
                break

            records, total = fetch_page_data(base_url, token_info, device_id,
                                            start_time, end_time, page, page_size)
            if not records:
                break

            all_records.extend(records)

            with _lock:
                tool_tasks[task_id]["message"] = f"已拉取 {len(all_records)} 条记录..."

            if page * page_size >= total:
                break

            page += 1
            time.sleep(0.3)  # 限流

        # 提取图片 URL
        download_tasks = []
        save_dir = Path(DOWNLOAD_PATH) / "tool_tasks" / task_id

        for record in all_records:
            back_path = record.get("backPath")
            capture_path = record.get("capturePath")
            if not back_path or not capture_path:
                continue

            from urllib.parse import urlparse, unquote
            name = Path(unquote(urlparse(capture_path).path).replace('\\', '/')).name
            save_path = save_dir / (name or f'{uuid.uuid4().hex}.jpg')
            download_tasks.append((back_path, str(save_path)))

        with _lock:
            tool_tasks[task_id]["total"] = len(download_tasks)
            tool_tasks[task_id]["downloaded"] = 0
            tool_tasks[task_id]["message"] = f"开始下载 {len(download_tasks)} 张图片..."

        # 并发下载
        success_count = 0
        with ThreadPoolExecutor(max_workers=TOOL_TASK_CONCURRENCY) as executor:
            futures = {
                executor.submit(download_single_image, url, path, task_id, platform_name): (url, path)
                for url, path in download_tasks
            }

            for future in as_completed(futures):
                if future.result():
                    success_count += 1

        # 打包 ZIP
        with _lock:
            tool_tasks[task_id]["message"] = "正在进行图片去重..."

        # 图片去重
        dedup_stats = {}
        try:
            deduplicator = ImageDeduplicator(threshold=10)
            dedup_stats = deduplicator.deduplicate_directory(
                str(save_dir),
                duplicate_folder=str(save_dir / "重复图片"),
                delete=False
            )
            logger.info(f"[工具任务] {task_id} 去重完成: {dedup_stats}")
        except Exception as e:
            logger.warning(f"[工具任务] {task_id} 去重失败: {e}")

        with _lock:
            tool_tasks[task_id]["message"] = "正在打包..."

        import zipfile
        zip_path = save_dir.parent / f"{task_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(save_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(save_dir)
                    zf.write(file_path, arcname)

        # 构建完成消息
        complete_msg = f"完成！成功下载 {success_count}/{len(download_tasks)} 张图片"
        if dedup_stats:
            complete_msg += f", 去重: {dedup_stats.get('total', 0)}→{dedup_stats.get('unique', 0)} (移除 {dedup_stats.get('moved', 0)} 张)"

        with _lock:
            tool_tasks[task_id]["status"] = "completed" if success_count or not download_tasks else "failed"
            tool_tasks[task_id]["message"] = complete_msg
            tool_tasks[task_id]["zip_path"] = str(zip_path)
            tool_tasks[task_id]["completed_at"] = datetime.now().isoformat()
            tool_tasks[task_id]["dedup_stats"] = dedup_stats

        logger.info(f"工具任务 {task_id} 完成")

    except Exception as e:
        logger.error(f"工具任务 {task_id} 失败: {e}", exc_info=True)
        with _lock:
            tool_tasks[task_id]["status"] = "failed"
            tool_tasks[task_id]["message"] = f"任务失败: {str(e)}"


def create_tool_task(platform_name: str, device_id: str, start_time: str,
                     end_time: str, max_pages: int = 0) -> str:
    """创建并启动工具任务"""
    task_id = str(uuid.uuid4())

    with _lock:
        tool_tasks[task_id] = {
            "task_id": task_id,
            "platform_name": platform_name,
            "device_id": device_id,
            "start_time": start_time,
            "end_time": end_time,
            "max_pages": max_pages,
            "status": "pending",
            "message": "任务已创建",
            "total": 0,
            "downloaded": 0,
            "created_at": datetime.now().isoformat(),
            "zip_path": None,
        }

    from integrations.smarteye.app import task_executor
    task_executor.submit(tracked_run, run_tool_task, task_id,
        platform_name, device_id, start_time, end_time, max_pages)

    return task_id


def get_tool_task_status(task_id: str) -> Optional[dict]:
    """获取任务状态"""
    with _lock:
        return tool_tasks.get(task_id)


def list_tool_tasks() -> List[dict]:
    """列出所有任务"""
    with _lock:
        return list(tool_tasks.values())
