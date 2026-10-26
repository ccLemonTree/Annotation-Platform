from integrations.smarteye.runtime import actor, execution, get_runtime
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
"""
台账监听下载器
按轮询间隔查预警数据库，再按设备和预警时间前后各 15 秒查台账。
"""
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

from integrations.smarteye.api.utils import logger
from integrations.smarteye.api.db_utils import (
    get_smart_search_platforms,
    get_watch_jobs, upsert_watch_job, delete_watch_job,
    ensure_ledger_watch_tables,
)
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.ledger_image_downloader import process_single_platform

DOWNLOAD_PATH = str(download_root())

# ---------------------------------------------------------------------------
# 运行时状态（内存）
# ---------------------------------------------------------------------------

watch_jobs_state: Dict[str, Dict[str, Any]] = {}
_running_jobs: Dict[str, threading.Thread] = {}
_stop_events: Dict[str, threading.Event] = {}
_state_lock = threading.Lock()


def _init_job_state(job_id: str, job: dict):
    persisted = get_runtime().get(job_id) or {}
    with _state_lock:
        watch_jobs_state[job_id] = {
            "job_id": job_id,
            "name": job.get("name", job_id),
            "enabled": job.get("enabled", True),
            "status": "idle",
            "last_poll": None,
            "last_trigger_count": 0,
            "total_downloaded": persisted.get('total_downloaded', 0),
            "recent_logs": persisted.get('recent_logs', []),
            "platform_ids": job.get("platform_ids", []),
            "alarm_type_filter": job.get("alarm_type_filter", []),
            "small_model_mode": job.get("small_model_mode", "crop"),
            "poll_interval_seconds": job.get("poll_interval_seconds", 60),
            "time_offset_seconds": 15,
        }


def _log(job_id: str, msg: str):
    logger.info(f"[watch:{job_id[:8]}] {msg}")
    with _state_lock:
        state = watch_jobs_state.get(job_id)
        if state is not None:
            state["recent_logs"].insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "msg": msg,
            })
            if len(state["recent_logs"]) > 50:
                state["recent_logs"].pop()
    _persist_state(job_id)

def _persist_state(job_id):
    current = execution.get()
    if current and current[0] == job_id:
        with _state_lock:
            state = dict(watch_jobs_state.get(job_id, {}))
        get_runtime().patch(job_id, {**state, 'stage': state.get('status'), 'status': 'running'}, current[1])


# ---------------------------------------------------------------------------
# 单个平台轮询
# ---------------------------------------------------------------------------


def _poll_platform(job_id: str, platform: dict, job: dict,
                   plat_alarm_types: Dict[str, str], output_root: Path) -> int:
    end = datetime.now().replace(microsecond=0)
    start = end - timedelta(seconds=job.get("poll_interval_seconds", 60))
    result = process_single_platform(
        platform, plat_alarm_types,
        start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"),
        output_root, small_model_mode=job.get("small_model_mode", "crop"),
    )
    _log(job_id, f"[{platform.get('platform_name')}] {result['msg']}")
    if result["status"] == "失败":
        raise RuntimeError(result["msg"])
    return result["downloaded_count"] + result["cropped_count"]


# ---------------------------------------------------------------------------
# 监听主循环
# ---------------------------------------------------------------------------

def _watch_loop(job_id: str):
    stop_event = _stop_events.get(job_id)
    if not stop_event:
        return

    while not stop_event.is_set():
        current = execution.get()
        if current:
            get_runtime().patch(job_id, {'status': 'running'}, current[1])
        cycle_started = time.monotonic()
        state = watch_jobs_state.get(job_id)
        if not state or not state.get("enabled"):
            break

        poll_interval = state.get("poll_interval_seconds", 60)
        with _state_lock:
            state["status"] = "polling"
            state["last_poll"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            jobs = get_watch_jobs(only_enabled=True)
            job = next((j for j in jobs if j["job_id"] == job_id), None)
            if not job:
                break

            all_platforms = get_smart_search_platforms(only_active=True)
            platform_ids = job.get("platform_ids", [])
            platforms = ([p for p in all_platforms if p["plat_id"] in platform_ids]
                         if platform_ids else all_platforms)

            if not platforms:
                _log(job_id, "没有可用平台，跳过")
                with _state_lock:
                    state["status"] = "idle"
                stop_event.wait(max(0, poll_interval - (time.monotonic() - cycle_started)))
                continue

            alarm_type_filter = job.get("alarm_type_filter", [])
            platform_mapping = get_combined_platform_mapping()

            ts = datetime.now().strftime("%Y%m%d")
            output_root = Path(DOWNLOAD_PATH) / "ledger_watch" / job_id[:8] / ts

            trigger_count = 0
            for plat in platforms:
                if stop_event.is_set():
                    break
                pname = plat.get("platform_name", "")
                all_types = platform_mapping.get(pname, {}).get("alarmTypes", {})
                plat_alarm_types = (
                    {k: v for k, v in all_types.items() if k in alarm_type_filter}
                    if alarm_type_filter else all_types
                )
                if not plat_alarm_types:
                    _log(job_id, f"[{pname}] 没有匹配的告警类型，跳过")
                    continue
                try:
                    trigger_count += _poll_platform(job_id, plat, job, plat_alarm_types, output_root)
                except Exception as e:
                    logger.error(f"[watch:{job_id[:8]}] [{pname}] 轮询异常: {e}", exc_info=True)

            with _state_lock:
                state["status"] = "idle"
                state["last_trigger_count"] = trigger_count
                if trigger_count:
                    state["total_downloaded"] += trigger_count

        except Exception as e:
            logger.error(f"[watch:{job_id[:8]}] 主循环异常: {e}", exc_info=True)
            with _state_lock:
                if state:
                    state["status"] = "idle"

        _persist_state(job_id)
        stop_event.wait(max(0, poll_interval - (time.monotonic() - cycle_started)))

    with _state_lock:
        s = watch_jobs_state.get(job_id)
        if s:
            s["status"] = "stopped"
    _log(job_id, "监听已停止")
    if execution.get():
        get_runtime().patch(job_id, {'status': 'completed'}, execution.get()[1])


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def start_watch_job(job_id: str):
    if job_id in _running_jobs and _running_jobs[job_id].is_alive():
        return
    jobs = get_watch_jobs()
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not job:
        return
    _init_job_state(job_id, job)
    stop_event = threading.Event()
    _stop_events[job_id] = stop_event
    import contextvars
    owner = actor.get()
    get_runtime().create(job_id, 'watch', {'status': 'pending', 'request': job}, owner)
    if get_runtime().get(job_id)['status'] in ('cancelled', 'interrupted', 'failed'):
        get_runtime().retry(job_id)
    context = contextvars.copy_context()
    def run():
        token = execution.set((job_id, get_runtime().get(job_id)['attempt']))
        try:
            return _watch_loop(job_id)
        finally:
            execution.reset(token)
    t = threading.Thread(target=lambda: context.run(run), args=(), daemon=True,
                         name=f"watch-{job_id[:8]}")
    _running_jobs[job_id] = t
    t.start()
    _log(job_id, f"监听任务 [{job.get('name')}] 已启动，轮询间隔 {job.get('poll_interval_seconds')}s")


def stop_watch_job(job_id: str):
    if get_runtime().get(job_id):
        get_runtime().cancel(job_id)
    ev = _stop_events.get(job_id)
    if ev:
        ev.set()
    with _state_lock:
        state = watch_jobs_state.get(job_id)
        if state:
            state["status"] = "stopping"
            state["enabled"] = False


def get_all_watch_states() -> List[dict]:
    jobs = get_watch_jobs()
    result = []
    for job in jobs:
        jid = job["job_id"]
        runtime = watch_jobs_state.get(jid, {})
        persisted = get_runtime().get(jid) or {}
        merged = {**job, **persisted, **runtime}
        merged["is_running"] = jid in _running_jobs and _running_jobs[jid].is_alive()
        result.append(merged)
    return result


def restore_watch_jobs():
    ensure_ledger_watch_tables()
    jobs = get_watch_jobs(only_enabled=True)
    for job in jobs:
        start_watch_job(job["job_id"])
    logger.info(f"已恢复 {len(jobs)} 个监听任务")
