from integrations.smarteye.runtime import cached_image, response_bytes
from integrations.smarteye.configuration import get_env
from integrations.smarteye.runtime import DurableTasks, tracked_run, actor, save_image_content
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
"""
多平台台账图片自动下载器
支持多个独立调度器并行运行，每个调度器有独立的平台/告警/间隔配置
"""
import os
import re
import json
import time
import uuid
import ssl
import zipfile
import threading
import requests
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import FIRST_COMPLETED, as_completed, wait
from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from requests.adapters import HTTPAdapter
from urllib3 import poolmanager

from integrations.smarteye.api.utils import logger
from integrations.smarteye.api.db_utils import get_smart_search_platforms, get_devices_by_alarm_type_name, upsert_devices
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.smart_search import login_platform
from integrations.smarteye.api.ledger_alarm_source import query_alarm_candidates
from integrations.smarteye.api.db_cache import (
    init_connection_pool,
    get_ledger_watermark, update_ledger_watermark, ensure_cache_tables
)
from integrations.smarteye.api.image_dedup import ImageDeduplicator

# 本模块请求内网地址，禁用系统代理

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LEDGER_QUERY_WINDOW_HOURS = int(get_env("LEDGER_QUERY_WINDOW_HOURS", "4"))
LEDGER_MAX_DEVICES_PER_TYPE = int(get_env("LEDGER_MAX_DEVICES_PER_TYPE", "5000"))
LEDGER_MAX_PAGES_PER_DEVICE = int(get_env("LEDGER_MAX_PAGES_PER_DEVICE", "20"))  # 每台设备最多 20 页 = 2000 条，覆盖 8 小时窗口
LEDGER_PLATFORM_CONCURRENCY = 3
LEDGER_DEVICE_CONCURRENCY = int(get_env("LEDGER_DEVICE_CONCURRENCY", "30"))
LEDGER_DOWNLOAD_CONCURRENCY = int(get_env("LEDGER_DOWNLOAD_CONCURRENCY", "20"))
LEDGER_DOWNLOAD_DELAY = 0.05
LEDGER_ENABLE_WATERMARK = get_env("LEDGER_ENABLE_WATERMARK", "true").lower() == "true"  # 是否启用水位线
LEDGER_ENABLE_DEDUP = get_env("LEDGER_ENABLE_DEDUP", "true").lower() == "true"  # 是否启用去重
LEDGER_DEDUP_THRESHOLD = int(get_env("LEDGER_DEDUP_THRESHOLD", "10"))  # 去重相似度阈值
DOWNLOAD_PATH = str(download_root())

# 初始化数据库连接池和缓存表

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
ledger_image_tasks = DurableTasks("ledger")
_recent_tasks: List[dict] = []
_task_progress_lock = threading.Lock()


def _download_progress_state(completed: int, total: int, success: int, failed: int) -> dict:
    total = max(total, 0)
    completed = min(max(completed, 0), total)
    pending = total - completed
    progress = 70 if total == 0 else 70 + int(completed / total * 18)
    return {
        "stage": "downloading",
        "progress": progress,
        "download_total": total,
        "download_completed": completed,
        "download_pending": pending,
        "download_success": success,
        "download_failed": failed,
        "message": (f"图片下载 {completed}/{total}，剩余 {pending}，"
                    f"成功 {success}，失败 {failed}"),
    }


def _update_download_progress(task_id: str, submitted: int = 0,
                              finished: int = 0, success: int = 0,
                              failed: int = 0, publish: bool = False) -> None:
    if not task_id:
        return
    with _task_progress_lock:
        task = ledger_image_tasks.get(task_id)
        if not task:
            return
        task["download_total"] = task.get("download_total", 0) + submitted
        task["download_completed"] = task.get("download_completed", 0) + finished
        task["download_success"] = task.get("download_success", 0) + success
        task["download_failed"] = task.get("download_failed", 0) + failed
        task["download_pending"] = max(
            task["download_total"] - task["download_completed"], 0
        )
        if publish:
            task.update(_download_progress_state(
                task["download_completed"], task["download_total"],
                task["download_success"], task["download_failed"],
            ))


# ---------------------------------------------------------------------------
# SchedulerInstance — one per scheduler
# ---------------------------------------------------------------------------

class SchedulerInstance:
    def __init__(self, sched_id: str, name: str = ""):
        self.sched_id = sched_id
        self.name = name or sched_id
        self.enabled = False
        self.interval_minutes = 120
        self.task_duration_hours = 0
        self.started_at: Optional[str] = None
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.last_task_id: Optional[str] = None
        self.current_task_id: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.platform_ids: Optional[List[str]] = None
        self.alarm_type_filter: Optional[List[str]] = None
        self.small_model_mode = "crop"
        self.is_running = False  # current task running?
        self.recent_tasks: List[dict] = []

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def to_dict(self) -> dict:
        return {
            "sched_id": self.sched_id,
            "name": self.name,
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "task_duration_hours": self.task_duration_hours,
            "started_at": self.started_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_task_id": self.last_task_id,
            "last_result": self.last_result,
            "platform_ids": self.platform_ids,
            "alarm_type_filter": self.alarm_type_filter,
            "small_model_mode": self.small_model_mode,
            "is_running": self.is_running,
            "recent_tasks": [{
                "task_id": t["task_id"],
                "status": t["status"],
                "message": t["message"],
                "created_at": t.get("created_at"),
            } for t in self.recent_tasks[:10]],
        }


# ---------------------------------------------------------------------------
# Scheduler registry
# ---------------------------------------------------------------------------
_schedulers: Dict[str, SchedulerInstance] = {}
_schedulers_lock = threading.Lock()


def _get_or_create_scheduler(sched_id: str = None) -> SchedulerInstance:
    """Get existing or create new scheduler."""
    if not sched_id:
        sched_id = f"sched_{uuid.uuid4().hex[:16]}"
    with _schedulers_lock:
        if sched_id not in _schedulers:
            _schedulers[sched_id] = SchedulerInstance(sched_id)
        return _schedulers[sched_id]


# ---------------------------------------------------------------------------
# WeakSSL
# ---------------------------------------------------------------------------
class _WeakSSLAdapter(HTTPAdapter):
    _ssl_context = ssl.create_default_context()
    _ssl_context.set_ciphers('DEFAULT:@SECLEVEL=1')
    _ssl_context.check_hostname = False
    _ssl_context.verify_mode = ssl.CERT_NONE

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        self.poolmanager = poolmanager.PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, ssl_context=self._ssl_context, **kwargs
        )


def _make_no_proxy_session() -> requests.Session:
    """返回一个绕过系统代理的 Session（兼容大/小写环境变量代理）。"""
    s = requests.Session()
    s.mount("https://", _WeakSSLAdapter())
    # 覆盖大小写代理 key，requests 在 Windows 下会读大写环境变量
    s.proxies = {"http": "", "https": "", "HTTP": "", "HTTPS": ""}
    s.trust_env = False  # 不读取任何系统代理环境变量
    return s


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def parse_analyse_res(analyse_res: str) -> Optional[dict]:
    if not analyse_res:
        return None
    text = analyse_res.strip()
    if not text:
        return None
    if '\\u' in text:
        try:
            text = text.encode().decode('unicode_escape')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    # 丽水平台大模型格式: [MQ][[{"label":"烟雾检测-大模型", ...}]],...
    # 原格式不是逗号分隔 JSON，不能把完整 analyseRes 当作模型目录名。
    if text.startswith('[MQ]'):
        model_match = re.search(r'"label"\s*:\s*"([^"\\]*大模型[^"\\]*)"', text)
        if model_match:
            return {
                "model_type": "大模型",
                "model_name": model_match.group(1),
                "alarm_type_id": None,
                "labels": [],
                "bboxes": [],
            }

        decoder = json.JSONDecoder()
        index = 4
        entries = []
        while index < len(text):
            while index < len(text) and text[index] in ' ,\r\n\t':
                index += 1
            if index >= len(text):
                break
            try:
                value, index = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                break
            stack = value if isinstance(value, list) else [value]
            while stack:
                item = stack.pop()
                if isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, dict) and item.get("bbox"):
                    entries.append(item)

        if not entries:
            return None
        return {
            "model_type": "小模型",
            "model_name": None,
            "alarm_type_id": None,
            "alarm_type_ids": [str(item.get("alarmTypeId", "")) for item in entries],
            "labels": [str(item.get("alarmTypeId", "")) for item in entries],
            "bboxes": [bbox for item in entries for bbox in item["bbox"]],
        }

    # 格式: "文字部分,{小模型JSON},{大模型JSON}"
    # 用第一个 ,{ 切分，文字在前，剩余全部是 JSON 段
    verified_labels = None
    split_idx = text.find(',{')
    if text.startswith('{'):
        decoder = json.JSONDecoder()
        try:
            _, json_end = decoder.raw_decode(text)
            text_part = ""
            json_segments = [text[:json_end]]
            verify_index = text.find('[VERIFY]', json_end)
            if verify_index >= 0:
                verified, _ = decoder.raw_decode(text[verify_index + len('[VERIFY]'):])
                if not isinstance(verified, list):
                    return None
                verified_labels = {
                    item.get("label") for entry in verified if isinstance(entry, dict)
                    for item in entry.get("result", []) if isinstance(item, dict) and item.get("isAlarm") is True
                }
                if not verified_labels:
                    return None
        except (json.JSONDecodeError, TypeError):
            return None
    elif split_idx < 0:
        text_part = text
        json_segments = []
    else:
        text_part = text[:split_idx].strip()
        # 后面可能有多个 JSON 对象，逐个解析
        remainder = text[split_idx + 1:]  # 去掉最前面的逗号，从 { 开始
        json_segments = _split_json_objects(remainder)

    # 快速跳过纯"无"记录（无检测结果）
    if text_part.rstrip('.') in ('无', '') and all(s in ('{}', '') for s in json_segments):
        return None

    result = {"model_type": "大模型" if verified_labels is not None else None,
              "model_name": None, "alarm_type_id": None, "labels": [], "bboxes": []}

    # 大模型：文字部分含"大模型"，提取 alarmTypeId（格式： "> id > 119"）
    if '大模型' in text_part:
        result["model_type"] = "大模型"
        alarm_id_match = re.search(r'>\s*id\s*>\s*(\d+)', text_part)
        if alarm_id_match:
            result["alarm_type_id"] = alarm_id_match.group(1)
        clean_name = re.sub(r'\s*>\s*id\s*>\s*\d+', '', text_part).strip()
        result["model_name"] = clean_name or text_part

    # 小模型：JSON 段里找有 bbox 的标签
    for seg in json_segments:
        if not seg or seg == '{}':
            continue
        try:
            bbox_data = json.loads(seg)
            for label, data in bbox_data.items():
                if verified_labels is not None and label not in verified_labels:
                    continue
                if isinstance(data, dict) and 'bbox' in data:
                    bboxes = data['bbox']
                    if isinstance(bboxes, list) and len(bboxes) > 0:
                        result["model_type"] = result["model_type"] or "小模型"
                        result["labels"].append(label)
                        result["bboxes"].extend(bboxes)
        except (json.JSONDecodeError, TypeError):
            pass

    if result["bboxes"]:
        seen_bboxes = set()
        unique_bboxes = []
        for bbox in result["bboxes"]:
            key = json.dumps(bbox, sort_keys=True, ensure_ascii=False)
            if key not in seen_bboxes:
                seen_bboxes.add(key)
                unique_bboxes.append(bbox)
        result["bboxes"] = unique_bboxes
        result["labels"] = list(dict.fromkeys(result["labels"]))

    return result if result["model_type"] else None


def _split_json_objects(s: str) -> list:
    """把 '{...},{...}' 形式的字符串拆分成各个 JSON 对象字符串列表。"""
    objects = []
    depth = 0
    start = None
    for i, ch in enumerate(s):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(s[start:i + 1])
                start = None
    return objects


def crop_image_from_bbox(full_image_path: str, bbox: dict, output_path: str, collection_name=None) -> bool:
    try:
        from PIL import Image
        img = Image.open(full_image_path)
        iw, ih = img.size
        ltx = max(0, int(bbox.get('ltx', 0)))
        lty = max(0, int(bbox.get('lty', 0)))
        w = int(bbox.get('width', 0))
        h = int(bbox.get('height', 0))
        if w <= 0 or h <= 0:
            return False
        rbx = min(ltx + w, iw)
        rby = min(lty + h, ih)
        if rbx <= ltx or rby <= lty:
            return False
        cropped = img.crop((ltx, lty, rbx, rby))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        from io import BytesIO
        output = BytesIO()
        cropped.save(output, format="JPEG")
        return bool(save_image_content(output_path, output.getvalue(), collection_name=collection_name))
    except Exception as e:
        logger.warning(f"裁图失败: {e}")
        return False


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _build_headers(base_url: str, token_info: dict) -> dict:
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
        "repeatalarm": "true",  # 台账查询必需字段
    }


_TOKEN_EXPIRED = object()  # 哨兵：标识 401 token 失效


def _get_with_token(base_url: str, path: str, token_info: dict, timeout: int = 20):
    url = f"{base_url.rstrip('/')}{path}"
    headers = _build_headers(base_url, token_info)
    try:
        with _make_no_proxy_session() as s:
            rsp = s.get(url, headers=headers, timeout=timeout, verify=False)
        if rsp.status_code == 401:
            logger.warning(f"GET 401 token 失效 [{url}]")
            return _TOKEN_EXPIRED
        rsp.raise_for_status()
        return rsp.json()
    except Exception as e:
        logger.warning(f"GET 请求失败 [{url}]: {str(e)[:80]}")
        return None


def _post_with_token(base_url: str, path: str, payload: dict,
                     token_info: dict, timeout: int = 30):
    url = f"{base_url.rstrip('/')}{path}"
    headers = _build_headers(base_url, token_info)
    try:
        with _make_no_proxy_session() as s:
            rsp = s.post(url, headers=headers, json=payload, timeout=timeout, verify=False)
        if rsp.status_code == 401:
            logger.warning(f"POST 401 token 失效 [{url}]")
            return _TOKEN_EXPIRED
        rsp.raise_for_status()
        return rsp.json()
    except Exception as e:
        logger.warning(f"POST 请求失败 [{url}]: {str(e)[:80]}")
        return None


def _relogin(platform: dict, plat_name: str) -> Optional[dict]:
    """重新登录，返回新的 token_info，失败返回 None。"""
    login_res = login_platform(platform)
    if login_res.get("success"):
        logger.info(f"[{plat_name}] token 重新获取成功")
        return login_res
    logger.error(f"[{plat_name}] 重新登录失败: {login_res.get('msg')}")
    return None


def query_alarm_type_labels(base_url: str, token_info: dict, alarm_type_id: str) -> List[str]:
    t = round(time.time() * 1000)
    rsp = _get_with_token(base_url, f"/api/alarm/admin/alarmType/label/config?alarmTypeId={alarm_type_id}&_t={t}", token_info)
    if not rsp or rsp is _TOKEN_EXPIRED or rsp.get("code") != 0:
        return []
    return [item.get("algorithmLabel", "") for item in rsp.get("data", []) if item.get("algorithmLabel")]


def query_devices_by_alarm_type(base_url: str, token_info: dict, alarm_type_id: str) -> List[dict]:
    all_devices = []
    current = 1
    page_size = 100
    while len(all_devices) < LEDGER_MAX_DEVICES_PER_TYPE:
        payload = {
            "current": current, "extra": {},
            "model": {"alarmTypeId": str(alarm_type_id), "scene": None, "deviceId": "",
                      "deviceName": None, "areaCode": None, "lastSnapTime": None,
                      "alarmNumStatus": False, "startTime": None, "endTime": None},
            "order": "descending", "searchCount": True, "size": page_size, "sort": "id", "total": 0,
        }
        rsp = _post_with_token(base_url, "/api/alarm/device/device/config/page", payload, token_info)
        if rsp is _TOKEN_EXPIRED:
            return []  # 调用方会处理重登录
        if not rsp or rsp.get("code") != 0:
            break
        records = rsp.get("data", {}).get("records", [])
        if not records:
            break
        all_devices.extend(records)
        total = int(rsp.get("data", {}).get("total", 0))
        if current * page_size >= total or len(all_devices) >= LEDGER_MAX_DEVICES_PER_TYPE:
            break
        if current % 5 == 0:
            logger.info(f"  [设备翻页] 第 {current} 页，已获取 {len(all_devices)} 台…")
        current += 1
    return all_devices[:LEDGER_MAX_DEVICES_PER_TYPE]


def query_ledger_record_page(base_url: str, token_info: dict,
                             device_id: str, start_time: str, end_time: str,
                             current: int, page_size: int = 100):
    """查询单页台账，返回 (records, total) 或 _TOKEN_EXPIRED。"""
    payload = {
        "current": current, "size": page_size,
        "model": {"deviceId": device_id, "startTime": start_time, "endTime": end_time},
    }
    rsp = _post_with_token(base_url, "/api/schedule/capture/stat/v1/log", payload, token_info)
    if rsp is _TOKEN_EXPIRED:
        return _TOKEN_EXPIRED
    if not rsp or rsp.get("code") != 0:
        raise RuntimeError(f"台账查询失败: {rsp.get('msg', '未知错误') if rsp else '请求失败或超时'}")
    data = rsp.get("data", {})
    return data.get("records", []) or [], int(data.get("total", 0))


def iter_ledger_record_pages(base_url: str, token_info: dict,
                             device_id: str, start_time: str, end_time: str,
                             max_pages: int = 0):
    """按页产出台账记录，避免一次把单台设备全部记录保存在内存。"""
    current = 1
    page_size = 100
    while max_pages <= 0 or current <= max_pages:
        page = query_ledger_record_page(
            base_url, token_info, device_id, start_time, end_time, current, page_size
        )
        if page is _TOKEN_EXPIRED:
            yield _TOKEN_EXPIRED
            return
        records, total = page
        if not records:
            return
        yield records
        if current * page_size >= total:
            return
        current += 1


def query_ledger_records(base_url: str, token_info: dict,
                         device_id: str, start_time: str, end_time: str,
                         max_pages: int = 0):
    """兼容旧调用方：返回记录列表，或 _TOKEN_EXPIRED 哨兵。"""
    all_records = []
    for records in iter_ledger_record_pages(
        base_url, token_info, device_id, start_time, end_time, max_pages
    ):
        if records is _TOKEN_EXPIRED:
            return _TOKEN_EXPIRED
        all_records.extend(records)
    return all_records


def fetch_and_save_devices(platforms: list) -> dict:
    """
    从各平台拉取全量设备列表，写入 devices 表。
    platforms: get_smart_search_platforms() 返回的 list。
    返回: {"total": N, "platforms": [{"name": ..., "count": N}, ...]}
    """
    summary = {"total": 0, "platforms": []}
    for platform in platforms:
        plat_name = platform.get("platform_name", "")
        base_url = platform.get("base_url", "")
        plat_id = platform.get("plat_id", "")
        if not base_url:
            continue

        login_res = login_platform(platform)
        if not login_res.get("success"):
            logger.warning(f"[{plat_name}] 登录失败，跳过设备更新")
            continue

        token_info = login_res
        all_devices = []
        current = 1
        page_size = 100

        while len(all_devices) < LEDGER_MAX_DEVICES_PER_TYPE:
            payload = {
                "current": current, "extra": {},
                "model": {"alarmTypeId": "", "scene": None, "deviceId": "",
                          "deviceName": None, "areaCode": None, "lastSnapTime": None,
                          "alarmNumStatus": False, "startTime": None, "endTime": None,
                          "captureType": None, "activeStatus": 1, "orderByTime": True},
                "order": "descending", "searchCount": True,
                "size": page_size, "sort": "createdTime", "total": 0,
            }
            rsp = _post_with_token(base_url, "/api/alarm/device/device/config/page", payload, token_info)
            if rsp is _TOKEN_EXPIRED or not rsp or rsp.get("code") != 0:
                break
            records = rsp.get("data", {}).get("records", [])
            if not records:
                break

            for rec in records:
                all_devices.append({
                    "device_id": rec.get("deviceId", ""),
                    "device_name": rec.get("deviceName", ""),
                    "alarm_type_names": rec.get("alarmTypeNames") or [],
                    "base_url": base_url,
                    "plat_id": plat_id,
                    "platform_name": plat_name,
                })

            total = int(rsp.get("data", {}).get("total", 0))
            if current * page_size >= total:
                break
            current += 1

        # 过滤掉无 device_id 的记录
        all_devices = [d for d in all_devices if d["device_id"]]
        saved = upsert_devices(all_devices)
        summary["total"] += saved
        summary["platforms"].append({"name": plat_name, "count": saved})
        logger.info(f"[{plat_name}] 设备更新完成，写入 {saved} 台")

    return summary


def _download_image(url: str, save_path: str, session: requests.Session = None, associate=True, collection_name=None) -> bool:
    if cached_image(save_path):
        return True
    max_retries = 2
    own_session = session is None
    local_session = session or _make_no_proxy_session()
    for attempt in range(max_retries):
        try:
            time.sleep(LEDGER_DOWNLOAD_DELAY)
            rsp = local_session.get(url, stream=True, timeout=15, verify=False)
            rsp.raise_for_status()
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            if not save_image_content(save_path, response_bytes(rsp), associate=associate, collection_name=collection_name):
                if own_session:local_session.close()
                return False
            return True
        except Exception as e:
            logger.warning(f"图片下载失败 [{url[:60]}]: {str(e)[:50]}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
    if own_session:
        local_session.close()
    return False


# ---------------------------------------------------------------------------
# Record-level filter
# ---------------------------------------------------------------------------

def _build_record_filter(alarm_name: str, alarm_code: str,
                         base_url: str, token_info: dict) -> Optional[dict]:
    """
    Build a record-level filter for an alarm type.
    Returns None only when we can't build a filter AND shouldn't filter.
    When alarm_type_filter is set, this is ONLY called for types IN the filter,
    so we must return a valid filter to prevent cross-type leakage.
    """
    if '大模型' in alarm_name:
        clean = re.sub(r'\s*>\s*id\s*>\s*\d+', '', alarm_name).strip()
        return {"type": "large_model", "name": clean}

    # Small model: try to get labels from API
    labels = query_alarm_type_labels(base_url, token_info, alarm_code)
    if labels:
        return {"type": "small_model", "labels": set(labels)}
    # Labels API returned empty — still must filter: reject large model results,
    # keep only records that have actual small model detections (non-empty labels)
    logger.warning(f"告警类型 [{alarm_name}] 标签API返回空，将拒绝大模型结果")
    return {"type": "small_model_only"}


def _record_matches_filter(parsed: dict, filter_rule: dict) -> bool:
    if not filter_rule:
        return True
    model_type = parsed.get("model_type")
    ftype = filter_rule["type"]

    if ftype == "large_model":
        if model_type != "大模型":
            return False
        fname = filter_rule.get("name", "")
        mname = parsed.get("model_name", "") or ""
        # 告警类型名包含在模型名里，或模型名包含告警类型名（互相包含即通过）
        # 没有名称时放行（无法判断，宁可保留）
        if not fname or not mname:
            return True
        return fname in mname or mname in fname

    # 小模型过滤：拒绝大模型记录
    if model_type == "大模型":
        return False
    if ftype == "small_model":
        return bool(set(parsed.get("labels", [])) & filter_rule["labels"])
    # small_model_only：有标签即通过
    return model_type == "小模型" and len(parsed.get("labels", [])) > 0


def _safe_name(value: str) -> str:
    return (value or "未知").replace("/", "_").replace("\\", "_").replace(":", "_")


def _iter_ledger_zip_files(task_dir: Path):
    for root, dirs, files in os.walk(task_dir):
        dirs[:] = [name for name in dirs if name != "重复图片"]
        for filename in files:
            yield Path(root) / filename


def _normalize_alarm_name(value: str) -> str:
    value = re.sub(r'\s*>\s*id\s*>\s*\d+', '', value or '')
    return value.replace("-大模型", "").replace("大模型", "").replace("检测", "").replace("识别", "").strip()


def _build_alarm_filter(alarm_name: str, alarm_code: str,
                        base_url: str, token_info: dict) -> dict:
    labels = set(query_alarm_type_labels(base_url, token_info, alarm_code))
    if labels:
        logger.info(f"告警类型 [{alarm_name}] 标签: {sorted(labels)}")
    elif "大模型" not in alarm_name:
        logger.warning(f"告警类型 [{alarm_name}] 标签API返回空，将仅按大模型名称/告警类型兜底匹配")

    return {
        "alarm_name": alarm_name,
        "alarm_code": str(alarm_code),
        "safe_alarm": _safe_name(alarm_name),
        "labels": labels,
        "normalized_name": _normalize_alarm_name(alarm_name),
        "is_large_model": "大模型" in alarm_name,
    }


def _record_matches_alarm(parsed: dict, rule: dict) -> bool:
    if (parsed.get("model_type") == "大模型") != bool(rule.get("is_large_model")):
        return False

    alarm_type_ids = {str(value) for value in parsed.get("alarm_type_ids", []) if value}
    if alarm_type_ids:
        return str(rule.get("alarm_code", "")) in alarm_type_ids

    # 1. 大模型记录：优先用 alarm_type_id 精确匹配
    alarm_type_id = parsed.get("alarm_type_id")
    if alarm_type_id:
        rule_alarm_code = str(rule.get("alarm_code", ""))
        if rule_alarm_code and alarm_type_id == rule_alarm_code:
            return True
        # alarm_type_id 存在但不匹配，直接拒绝（避免名称模糊匹配误判）
        return False

    # 2. 小模型记录：标签匹配
    labels = rule.get("labels") or set()
    if labels and set(parsed.get("labels", [])) & labels:
        return True

    # 3. 回退：名称模糊匹配（仅用于无 alarm_type_id 且无标签的记录）
    model_name = parsed.get("model_name", "") or ""
    normalized_alarm = rule.get("normalized_name", "")
    normalized_model = _normalize_alarm_name(model_name)
    if normalized_alarm and normalized_model:
        return normalized_alarm in normalized_model or normalized_model in normalized_alarm

    return False


def _small_model_label_name(parsed: dict, rule: dict) -> str:
    labels = set(parsed.get("labels", []))
    return next(iter(labels & rule["labels"]), rule["safe_alarm"])


def _get_devices_for_alarm_types(alarm_names: List[str], base_url: str) -> List[dict]:
    devices_by_id = {}
    for alarm_name in alarm_names:
        for device in get_devices_by_alarm_type_name(alarm_name, base_url):
            device_id = device.get("device_id")
            if device_id:
                devices_by_id[device_id] = device
    return list(devices_by_id.values())


# ---------------------------------------------------------------------------
# Platform processor
# ---------------------------------------------------------------------------

def process_single_platform(platform: dict, alarm_types: dict,
                            start_time: str, end_time: str, output_root: Path,
                            small_model_mode: str = "crop",
                            task_id: str = "",
                            sched_id: str = "") -> dict:
    plat_name = platform.get("platform_name", "未知平台")
    base_url = platform.get("base_url", "")
    plat_id = platform.get("plat_id", "")

    result = {"plat_id": plat_id, "plat_name": plat_name, "status": "pending",
              "msg": "", "device_count": 0, "record_count": 0,
              "downloaded_count": 0, "cropped_count": 0, "skipped_count": 0,
              "downloaded_alarm_count": 0, "unmatched_alarm_count": 0,
              "missing_ledger_count": 0, "missing_original_count": 0,
              "failed_alarm_count": 0, "errors": []}

    if not base_url:
        result["status"] = "失败"; result["msg"] = "缺少 base_url"; return result

    try:
        candidates = query_alarm_candidates(platform, start_time, end_time, list(alarm_types.values()))
    except Exception as e:
        result["status"] = "失败"; result["msg"] = f"预警数据库查询失败: {e}"
        result["errors"].append(result["msg"])
        return result
    result["alarm_count"] = len(candidates)
    if not candidates:
        result["status"] = "成功"; result["msg"] = "本轮时间范围内无预警"; return result

    windows = {}
    for alarm in candidates:
        device_id = str(alarm["device_id"])
        alarm_time = alarm["alarm_time"]
        if not isinstance(alarm_time, datetime):
            alarm_time = datetime.fromisoformat(alarm_time)
        key = (device_id, alarm_time)
        if key not in windows:
            windows[key] = {
                "device_id": device_id,
                "start_time": (alarm_time - timedelta(seconds=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": (alarm_time + timedelta(seconds=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "alarm_time": alarm_time, "alarms": [], "records_seen": 0,
            }
        windows[key]["alarms"].append(alarm)
    del candidates
    devices = list(windows.values())
    result["device_count"] = len({item["device_id"] for item in devices})

    login_res = login_platform(platform)
    if not login_res.get("success"):
        result["status"] = "失败"
        result["failed_alarm_count"] = result["alarm_count"]
        result["msg"] = f"登录失败: {login_res.get('msg', '未知')}"; return result

    token_info = login_res
    result["status"] = "运行中"
    safe_plat = _safe_name(plat_name)
    plat_dir = output_root / safe_plat
    plat_dir.mkdir(parents=True, exist_ok=True)

    session = _make_no_proxy_session()
    # token_info 包装为列表，便于 process_device 内层闭包修改（Python 闭包不能 rebind 外层变量）
    _token = [token_info]

    def _refresh_token() -> bool:
        """原地刷新 _token[0]，返回是否成功。"""
        new_tok = _relogin(platform, plat_name)
        if new_tok:
            _token[0] = new_tok
            return True
        return False

    try:
        # 类型已在数据库筛选，标签仅用于裁图及目录命名。
        alarm_rules = {}  # alarm_name -> rule
        for alarm_name, alarm_code in alarm_types.items():
            alarm_rules[alarm_name] = _build_alarm_filter(alarm_name, alarm_code, base_url, _token[0])

        if not alarm_rules:
            result["status"] = "失败"; result["msg"] = "没有可用告警类型"; return result

        logger.info(f"[{plat_name}] 预警 {result['alarm_count']} 条，设备 {result['device_count']} 台，台账窗口 {len(devices)} 个")

        rules_by_code = {rule["alarm_code"]: rule for rule in alarm_rules.values()}
        download_locks = {}
        total_devices = len(devices)
        devices_done = 0

        def _query_device_page(device, current):
            return query_ledger_record_page(
                base_url, _token[0], device["device_id"], device["start_time"], device["end_time"], current
            )

        def _do_download(task, lock):
            url, full_path, bboxes, need_full, need_crop = task
            with lock:
                downloaded = cropped = 0
                try:
                    if not _download_image(url, full_path, associate=need_full, collection_name=plat_name):
                        return 0, 0
                    if need_full:
                        downloaded += 1
                    if need_crop and bboxes:
                        stem = os.path.splitext(full_path)[0]
                        if stem.endswith("_full"):
                            stem = stem[:-5]
                        for i, bbox in enumerate(bboxes):
                            if crop_image_from_bbox(full_path, bbox, f"{stem}_crop_{i+1}.jpg", collection_name=plat_name):
                                cropped += 1
                        if small_model_mode == "crop":
                            try: os.remove(full_path)
                            except OSError: pass
                except Exception as e:
                    logger.warning(f"下载任务异常 [{url[:60]}]: {e}")
                return downloaded, cropped

        # 查询和下载流水线并行；限制待处理数量，避免一次积压数万个 Future。
        dl_futures = set()
        max_pending_downloads = max(LEDGER_DOWNLOAD_CONCURRENCY * 4, 1)

        def _consume_download_future(download_future, publish=False):
            try:
                downloaded, cropped = download_future.result()
                result["downloaded_count"] += downloaded
                result["cropped_count"] += cropped
                succeeded = int(downloaded + cropped > 0)
                result["downloaded_alarm_count"] += succeeded
                result["failed_alarm_count"] += 1 - succeeded
                _update_download_progress(
                    task_id, finished=1, success=succeeded,
                    failed=1 - succeeded, publish=publish,
                )
            except Exception as e:
                logger.warning(f"[{plat_name}] 下载future异常: {e}")
                result["failed_alarm_count"] += 1
                _update_download_progress(task_id, finished=1, failed=1, publish=publish)

        def _submit_download(download_args):
            nonlocal dl_futures
            lock = download_locks.setdefault(download_args[1], threading.Lock())
            dl_futures.add(dl_exec.submit(_do_download, download_args, lock))
            _update_download_progress(task_id, submitted=1)
            if len(dl_futures) >= max_pending_downloads:
                done, dl_futures = wait(dl_futures, return_when=FIRST_COMPLETED)
                for download_future in done:
                    _consume_download_future(download_future)

        with ThreadPoolExecutor(max_workers=LEDGER_DOWNLOAD_CONCURRENCY) as dl_exec, \
             ThreadPoolExecutor(max_workers=LEDGER_DEVICE_CONCURRENCY) as dev_exec:

            device_iter = iter(devices)
            futures = {}

            def _submit_page(device, current):
                futures[dev_exec.submit(_query_device_page, device, current)] = (device, current)

            def _start_next_device():
                try:
                    _submit_page(next(device_iter), 1)
                    return True
                except StopIteration:
                    return False

            def _finish_device(device, query_failed=False):
                nonlocal devices_done
                alarm_count = len(device["alarms"])
                rec = device.get("best_record")
                if query_failed:
                    result["failed_alarm_count"] += alarm_count
                elif rec is None:
                    result["unmatched_alarm_count"] += alarm_count
                    key = "missing_ledger_count" if not device["records_seen"] else "missing_original_count"
                    result[key] += alarm_count
                else:
                    img_path = rec.get("backPath") or rec.get("capturePath")
                    if not img_path.startswith("http"):
                        img_path = base_url.rstrip("/") + "/" + img_path.lstrip("/")
                    parsed = parse_analyse_res(rec.get("analyseRes", "")) or {}
                    text = (rec.get("analyseRes") or "").strip()
                    if text.startswith('{'):
                        try:
                            _, json_end = json.JSONDecoder().raw_decode(text)
                            parsed = parse_analyse_res(text[:json_end]) or parsed
                        except json.JSONDecodeError:
                            pass
                    safe_dev = _safe_name(device["device_id"])
                    capture_time = rec["captureTime"].replace(":", "").replace(" ", "_")
                    for alarm in device["alarms"]:
                        rule = rules_by_code[str(alarm["alarm_type_id"])]
                        is_large = rule["is_large_model"]
                        model_type = "大模型" if is_large else "小模型"
                        label = (rule["safe_alarm"] if is_large else _small_model_label_name(parsed, rule))
                        image_dir = plat_dir / rule["safe_alarm"] / model_type / _safe_name(label)
                        image_dir.mkdir(parents=True, exist_ok=True)
                        full_path = str(image_dir / f"{safe_dev}_{capture_time}_full.jpg")
                        _submit_download((img_path, full_path, parsed.get("bboxes", []),
                                          is_large or small_model_mode in ("full", "both"),
                                          not is_large and small_model_mode in ("crop", "both")))
                devices_done += 1
                if task_id and total_devices > 0:
                    with _task_progress_lock:
                        t = ledger_image_tasks.get(task_id)
                        if t:
                            t["stage"] = "querying"
                            t["progress"] = int(devices_done / total_devices * 70)
                            t["message"] = (
                                f"查询预警窗口 {devices_done}/{total_devices}，"
                                f"记录 {result['record_count']}；下载任务 "
                                f"{t.get('download_completed', 0)}/{t.get('download_total', 0)}，"
                                f"剩余 {t.get('download_pending', 0)}"
                            )

            for _ in range(min(LEDGER_DEVICE_CONCURRENCY, total_devices)):
                _start_next_device()

            while futures:
                if task_id and ledger_image_tasks.get(task_id, {}).get("cancelled"):
                    for f in futures:
                        f.cancel()
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    device, current = futures.pop(fut)
                    try:
                        page = fut.result()
                    except Exception as e:
                        logger.warning(f"[{plat_name}] 设备查询失败/超时: {str(e)[:80]}")
                        result["errors"].append(str(e)[:80])
                        _finish_device(device, query_failed=True)
                        _start_next_device()
                        continue

                    if page is _TOKEN_EXPIRED:
                        retried_pages = device.setdefault("retried_pages", set())
                        if current not in retried_pages and _refresh_token():
                            retried_pages.add(current)
                            _submit_page(device, current)
                        else:
                            result["errors"].append("台账登录失效，刷新后仍无法查询")
                            _finish_device(device, query_failed=True)
                            _start_next_device()
                        continue

                    device_records, total_records = page
                    result["record_count"] += len(device_records)

                    device["records_seen"] += len(device_records)
                    for rec in device_records:
                        if str(rec.get("deviceId", "")) != device["device_id"]:
                            continue
                        if not (rec.get("backPath") or rec.get("capturePath")):
                            continue
                        try:
                            capture = datetime.fromisoformat(rec.get("captureTime", ""))
                        except (ValueError, TypeError):
                            continue
                        distance = abs((capture - device["alarm_time"]).total_seconds())
                        rank = (distance, not bool(rec.get("backPath")), capture)
                        if distance <= 15 and ("best_rank" not in device or rank < device["best_rank"]):
                            device["best_rank"] = rank
                            device["best_record"] = rec

                    if (LEDGER_MAX_PAGES_PER_DEVICE <= 0 or current < LEDGER_MAX_PAGES_PER_DEVICE) \
                            and current * 100 < total_records:
                        _submit_page(device, current + 1)
                    else:
                        _finish_device(device)
                        _start_next_device()

                    # 收割已完成的下载 future，避免内存积压
                    done_downloads = {df for df in dl_futures if df.done()}
                    dl_futures.difference_update(done_downloads)
                    for download_future in done_downloads:
                        _consume_download_future(download_future)

            # 等待剩余下载任务完成
            _update_download_progress(task_id, publish=True)
            for df in as_completed(list(dl_futures)):
                if task_id and ledger_image_tasks.get(task_id, {}).get("cancelled"):
                    for f in dl_futures:
                        f.cancel()
                    break
                _consume_download_future(df, publish=True)

        if task_id and ledger_image_tasks.get(task_id, {}).get("cancelled"):
            result["status"] = "已取消"; result["msg"] = "任务已被用户取消"; return result

        if result["unmatched_alarm_count"] or result["failed_alarm_count"]:
            result["errors"].append(
                f"无台账:{result['missing_ledger_count']} 窗口内无可用原图:{result['missing_original_count']} "
                f"失败:{result['failed_alarm_count']}"
            )
        result["status"] = "失败" if result["errors"] else "成功"
        result["msg"] = (f"预警:{result['alarm_count']} 对应成功:{result['downloaded_alarm_count']} "
                         f"未匹配:{result['unmatched_alarm_count']} 失败:{result['failed_alarm_count']} "
                         f"原图下载:{result['downloaded_count']} 裁图:{result['cropped_count']}")
        if result["errors"]:
            result["msg"] += f"；{result['errors'][0]}"

        # 更新水位线（如果启用且有调度器ID）
        if LEDGER_ENABLE_WATERMARK and sched_id and not result["errors"]:
            try:
                # 为每个告警类型更新水位线
                for alarm_name, rule in alarm_rules.items():
                    alarm_code = rule["alarm_code"]
                    # 使用结束时间作为水位线
                    watermark_time = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                    update_ledger_watermark(
                        sched_id=sched_id,
                        plat_id=plat_id,
                        alarm_code=alarm_code,
                        last_seen_time=watermark_time,
                        record_count=result['record_count']
                    )
                logger.info(f"[水位线] 已更新 {plat_name} 的水位线")
            except Exception as e:
                logger.warning(f"[水位线] 更新失败: {e}")

    except Exception as e:
        result["status"] = "失败"; result["msg"] = str(e)[:200]
        result["failed_alarm_count"] += max(
            0, result.get("alarm_count", 0) - result["downloaded_alarm_count"]
            - result["unmatched_alarm_count"] - result["failed_alarm_count"]
        )
        result["errors"].append(str(e)[:200])
        logger.error(f"[{plat_name}] 处理异常: {e}", exc_info=True)
    finally:
        session.close()
    return result


# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

def run_ledger_image_download_task(task_id: str,
                                   platform_ids: Optional[List[str]] = None,
                                   alarm_type_filter: Optional[List[str]] = None,
                                   start_time: Optional[str] = None,
                                   end_time: Optional[str] = None,
                                   small_model_mode: str = "crop",
                                   sched_id: str = "") -> None:
    task = ledger_image_tasks.get(task_id)
    if not task:
        return
    task["status"] = "running"; task["progress"] = 0
    task["message"] = "正在初始化..."; task["platform_results"] = []
    task.update({
        "stage": "initializing", "download_total": 0,
        "download_completed": 0, "download_pending": 0,
        "download_success": 0, "download_failed": 0,
    })

    try:
        all_platforms = get_smart_search_platforms(only_active=True)
        if platform_ids:
            all_platforms = [p for p in all_platforms if p["plat_id"] in platform_ids]
        if not all_platforms:
            task["status"] = "failed"; task["message"] = "没有找到启用的平台"; return

        if not end_time:
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not start_time:
            start_time = (datetime.now() - timedelta(hours=LEDGER_QUERY_WINDOW_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

        platform_mapping = get_combined_platform_mapping()
        safe_tid = task_id.replace("/", "_").replace("\\", "_")
        task_dir = Path(DOWNLOAD_PATH) / f"ledger_{safe_tid}"
        task_dir.mkdir(parents=True, exist_ok=True)

        total_platforms = len(all_platforms)
        task["message"] = f"正在处理 {total_platforms} 个平台..."
        all_results = []; completed = 0

        def _process_one(platform):
            plat_name = platform.get("platform_name", "")
            plat_alarm_types = platform_mapping.get(plat_name, {}).get("alarmTypes", {})
            if alarm_type_filter:
                # 仅保留名称在过滤列表中的告警类型（alarm_type_filter 是名称列表）
                plat_alarm_types = {k: v for k, v in plat_alarm_types.items()
                                    if k in alarm_type_filter}
            if not plat_alarm_types:
                return {"plat_id": platform.get("plat_id"), "plat_name": plat_name,
                        "status": "跳过", "msg": "没有匹配的告警类型", "device_count": 0,
                        "record_count": 0, "downloaded_count": 0, "cropped_count": 0,
                        "skipped_count": 0, "errors": []}
            return process_single_platform(platform, plat_alarm_types, start_time, end_time, task_dir,
                                           small_model_mode=small_model_mode, task_id=task_id, sched_id=sched_id)

        with ThreadPoolExecutor(max_workers=LEDGER_PLATFORM_CONCURRENCY) as plat_exec:
            for future in as_completed({plat_exec.submit(_process_one, p): p for p in all_platforms}):
                all_results.append(future.result())
                completed += 1
                task["progress"] = int(completed / total_platforms * 80)
                task["platform_results"] = all_results
                task["message"] = f"已完成 {completed}/{total_platforms} 个平台"

        if task.get("cancelled"):
            task["status"] = "已取消"; task["message"] = "任务已被用户取消"; return

        total_downloaded = sum(r.get("downloaded_count", 0) for r in all_results)
        total_cropped = sum(r.get("cropped_count", 0) for r in all_results)
        total_alarms = sum(r.get("alarm_count", 0) for r in all_results)
        covered_alarms = sum(r.get("downloaded_alarm_count", 0) for r in all_results)
        unmatched_alarms = sum(r.get("unmatched_alarm_count", 0) for r in all_results)
        failed_alarms = sum(r.get("failed_alarm_count", 0) for r in all_results)
        success_count = sum(1 for r in all_results if r.get("status") == "成功")

        if total_downloaded == 0 and total_cropped == 0:
            failures = [r for r in all_results if r.get("status") == "失败"]
            task["status"] = "failed" if failures else "completed"; task["progress"] = 100
            task["message"] = (f"下载失败: {failures[0]['msg']}" if failures else
                               f"完成（{success_count}/{total_platforms}），无图片可下载")
            task["platform_results"] = all_results
            _add_to_recent(task_id, task)
            return

        # 图片去重（如果启用）
        dedup_stats = {}
        if LEDGER_ENABLE_DEDUP:
            task["stage"] = "deduplicating"
            task["message"] = "正在进行图片去重..."; task["progress"] = 90
            try:
                deduplicator = ImageDeduplicator(threshold=LEDGER_DEDUP_THRESHOLD)

                def _dedup_progress(current, total):
                    task["progress"] = 90 + int(min(current / max(total, 1), 1) * 7)
                    task["message"] = f"图片去重 {min(current, total)}/{total}"

                dedup_stats = deduplicator.deduplicate_directory(
                    str(task_dir),
                    duplicate_folder=str(task_dir / "重复图片"),
                    delete=False,  # 移动到重复文件夹，不直接删除
                    progress_callback=_dedup_progress,
                )
                logger.info(f"[去重] {dedup_stats}")
            except Exception as e:
                logger.warning(f"图片去重失败: {e}")

        task["stage"] = "packing"
        task["message"] = "正在打包 ZIP..."; task["progress"] = 98
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"ledger_images_{ts}_{task_id[:8]}.zip"
        zip_path = Path(DOWNLOAD_PATH) / zip_filename
        try:
            with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in _iter_ledger_zip_files(task_dir):
                    zf.write(str(file_path), str(file_path.relative_to(task_dir)))
            task["zip_path"] = str(zip_path); task["zip_filename"] = zip_filename
        except Exception as e:
            logger.error(f"ZIP 打包失败: {e}")

        task["status"] = "completed"; task["stage"] = "completed"; task["progress"] = 100
        dedup_msg = ""
        if dedup_stats:
            dedup_msg = f", 文件去重: {dedup_stats.get('total', 0)}→{dedup_stats.get('unique', 0)} (移除 {dedup_stats.get('moved', 0)} 张)"
        task["message"] = (f"完成！预警 {total_alarms} 条，对应成功 {covered_alarms}，"
                           f"未匹配 {unmatched_alarms}，失败 {failed_alarms}；"
                           f"下载 {total_downloaded} 张（含重复）, 裁切 {total_cropped} 张{dedup_msg}")
    except Exception as e:
        logger.error(f"台账下载任务 {task_id} 失败: {e}", exc_info=True)
        task["status"] = "failed"; task["message"] = f"任务失败: {str(e)}"
    _add_to_recent(task_id, task)


def _add_to_recent(task_id: str, task: dict):
    _recent_tasks.insert(0, {"task_id": task_id, "status": task.get("status"),
                              "message": task.get("message"), "created_at": task.get("created_at"),
                              "platform_results": task.get("platform_results", [])})
    if len(_recent_tasks) > 20:
        _recent_tasks.pop()


def cancel_task(task_id: str) -> bool:
    task = ledger_image_tasks.get(task_id)
    if not task or task.get("status") not in ("running", "pending"):
        return False
    task["cancelled"] = True; task["status"] = "cancelling"
    task["message"] = "正在取消..."
    return True


def get_all_tasks() -> List[dict]:
    result = []
    for tid, t in ledger_image_tasks.items():
        result.append({
            "task_id": tid, "status": t.get("status", "unknown"),
            "progress": t.get("progress", 0), "message": t.get("message", ""),
            "created_at": t.get("created_at"),
            "zip_filename": t.get("zip_filename"), "zip_path": t.get("zip_path"),
            "platform_results": t.get("platform_results", []),
            "stage": t.get("stage"),
            "download_total": t.get("download_total", 0),
            "download_completed": t.get("download_completed", 0),
            "download_pending": t.get("download_pending", 0),
            "download_success": t.get("download_success", 0),
            "download_failed": t.get("download_failed", 0),
        })
    return result


# ---------------------------------------------------------------------------
# Scheduler loop (runs per-instance)
# ---------------------------------------------------------------------------

def _scheduler_loop(sched: SchedulerInstance):
    while not sched._stop_event.is_set():
        cycle_started = time.monotonic()
        try:
            if sched.task_duration_hours > 0 and sched.started_at:
                try:
                    started_dt = datetime.strptime(sched.started_at, "%Y-%m-%dT%H:%M:%S")
                    if (datetime.now() - started_dt).total_seconds() >= sched.task_duration_hours * 3600:
                        logger.info(f"[{sched.name}] 任务时长 {sched.task_duration_hours}h 已到，自动停止")
                        sched.enabled = False
                        break
                except ValueError:
                    pass

            if not sched.is_running:
                sched.is_running = True
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    task_id = f"{sched.sched_id}_{ts}_{uuid.uuid4().hex[:8]}"
                    ledger_image_tasks[task_id] = {
                        "id": task_id, "status": "pending", "progress": 0,
                        "message": f"[{sched.name}] 自动触发", "created_at": time.time(),
                        "platform_results": [],
                    }
                    sched.current_task_id = task_id
                    logger.info(f"[{sched.name}] 开始执行任务 {task_id}")
                    window_end = datetime.now().replace(microsecond=0)
                    window_start = window_end - timedelta(minutes=sched.interval_minutes)
                    tracked_run(run_ledger_image_download_task, task_id, sched.platform_ids,
                                                   sched.alarm_type_filter,
                                                   window_start.strftime("%Y-%m-%d %H:%M:%S"),
                                                   window_end.strftime("%Y-%m-%d %H:%M:%S"), sched.small_model_mode,
                                                   sched_id=sched.sched_id)
                    sched.last_run = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    sched.last_task_id = task_id
                    task = ledger_image_tasks.get(task_id, {})
                    sched.last_result = {
                        "total_images": sum(r.get("downloaded_count", 0) + r.get("cropped_count", 0)
                                            for r in task.get("platform_results", [])),
                        "platforms_ok": sum(1 for r in task.get("platform_results", []) if r.get("status") == "成功"),
                        "platforms_fail": sum(1 for r in task.get("platform_results", []) if r.get("status") in ("失败",)),
                    }
                    sched.recent_tasks.insert(0, {"task_id": task_id, "status": task.get("status"),
                                                   "message": task.get("message"),
                                                   "created_at": task.get("created_at")})
                    if len(sched.recent_tasks) > 20:
                        sched.recent_tasks.pop()
                    logger.info(f"[{sched.name}] 任务 {task_id} 完成")
                except Exception as e:
                    logger.error(f"[{sched.name}] 任务异常: {e}", exc_info=True)
                    t = ledger_image_tasks.get(task_id, {})
                    if t:
                        t["status"] = "failed"; t["message"] = f"任务异常: {str(e)[:200]}"
                finally:
                    sched.is_running = False
                    sched.current_task_id = None
        except Exception:
            logger.error(f"[{sched.name}] 循环异常", exc_info=True)

        wait_seconds = max(0, sched.interval_minutes * 60 - (time.monotonic() - cycle_started))
        sched.next_run = (datetime.now() + timedelta(seconds=wait_seconds)).strftime("%Y-%m-%dT%H:%M:%S")
        sched._stop_event.wait(timeout=wait_seconds)


# ---------------------------------------------------------------------------
# Scheduler management API
# ---------------------------------------------------------------------------

def start_scheduler(sched_id: str) -> bool:
    sched = _schedulers.get(sched_id)
    if not sched:
        return False
    with sched._lock:
        if sched._thread and sched._thread.is_alive():
            return not sched._stop_event.is_set()
        sched._stop_event.clear()
        sched.enabled = True
        sched.started_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        import contextvars
        context = contextvars.copy_context()
        sched._thread = threading.Thread(target=lambda: context.run(_scheduler_loop, sched), args=(), daemon=True)
        sched._thread.start()
        logger.info(f"[{sched.name}] 调度器已启动")
        return True


def stop_scheduler(sched_id: str) -> bool:
    sched = _schedulers.get(sched_id)
    if not sched:
        return False
    with sched._lock:
        sched.enabled = False
        sched._stop_event.set()
        if sched.current_task_id:
            from integrations.smarteye.runtime import get_runtime
            get_runtime().cancel(sched.current_task_id)
        logger.info(f"[{sched.name}] 调度器已停止")
        return True


def remove_scheduler(sched_id: str) -> bool:
    sched = _schedulers.get(sched_id)
    if not sched:
        return False
    stop_scheduler(sched_id)
    with sched._lock:
        sched._stop_event.set()
        sched.enabled = False
    with _schedulers_lock:
        _schedulers.pop(sched_id, None)
    logger.info(f"调度器 [{sched_id}] 已删除")
    return True


def configure_scheduler(sched_id: str = None, name: str = None,
                        enabled: bool = False, interval_minutes: int = None,
                        platform_ids: List[str] = None,
                        alarm_type_filter: List[str] = None,
                        small_model_mode: str = None,
                        task_duration_hours: int = None) -> dict:
    sched = _get_or_create_scheduler(sched_id)
    with sched._lock:
        if name is not None:
            sched.name = name
        if interval_minutes is not None:
            sched.interval_minutes = interval_minutes
        if task_duration_hours is not None:
            sched.task_duration_hours = task_duration_hours
        if platform_ids is not None:
            sched.platform_ids = platform_ids
        if alarm_type_filter is not None:
            sched.alarm_type_filter = alarm_type_filter
        if small_model_mode is not None:
            sched.small_model_mode = small_model_mode

    if enabled:
        if sched.enabled:
            # Restart: stop then start
            stop_scheduler(sched.sched_id)
        start_scheduler(sched.sched_id)
    else:
        stop_scheduler(sched.sched_id)

    return sched.to_dict()


def get_all_schedulers() -> List[dict]:
    with _schedulers_lock:
        return [s.to_dict() for s in _schedulers.values()]
