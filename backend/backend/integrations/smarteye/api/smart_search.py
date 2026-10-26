from integrations.smarteye.runtime import cached_image, response_bytes
from integrations.smarteye.runtime import DurableTasks, save_image_content
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
"""
@File    : smart_search.py
@Time    : 2026/02/27
@Description : 智搜数据下载 - 多平台登录 + 搜索 + 打包核心逻辑
"""
import os
import time
import uuid
import zipfile
import requests
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import as_completed
from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor
from typing import Dict, Any, List

from integrations.smarteye.api.utils import logger
from integrations.smarteye.api.login_utils import recognize_captcha
from integrations.smarteye.api.config import CAPTCHA_MODEL_API, CAPTCHA_MODEL_NAME

# 本模块请求内网地址，强制关闭系统代理，防止代理拦截

# 图片保存根路径
SMART_SEARCH_DOWNLOAD_PATH = str(download_root())

# 智搜任务缓存（进程内）
smart_search_tasks = DurableTasks("smart-search")


def login_platform(platform: dict) -> dict:
    """
    对单个平台执行验证码登录，返回 token 信息。
    platform 包含: base_url, username, password, login_url, platform_name
    """
    base_url = platform["base_url"]
    username = platform.get("username", "")
    password = platform.get("password", "")
    login_url_path = platform.get("login_url", "/api/oauth/anyTenant/login")
    plat_name = platform.get("platform_name", base_url)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # 1. 获取验证码
            t = round(time.time() * 1000)
            key = f"29EABBD97500C16F9423DEEB{t}"
            captcha_url = f"{base_url}/api/oauth/anyTenant/captcha?key={key}&_t={t}"

            captcha_rsp = requests.get(
                captcha_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
                timeout=10,
                proxies={"http": "", "https": ""},
            )
            captcha_rsp.raise_for_status()

            # 2. 识别验证码（复用 login_utils.recognize_captcha）
            code = recognize_captcha(captcha_rsp.content)
            if not code:
                logger.warning(f"[{plat_name}] 验证码识别失败 (尝试 {attempt + 1}/{max_retries})")
                continue

            # 3. 执行登录
            full_login_url = f"{base_url}{login_url_path}"
            login_data = {
                "username": username,
                "password": password,
                "code": code,
                "grantType": "CAPTCHA",
                "key": key,
            }
            login_headers = {
                "Authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            login_rsp = requests.post(
                full_login_url,
                headers=login_headers,
                json=login_data,
                timeout=10,
                proxies={"http": "", "https": ""},
            )
            login_rsp.raise_for_status()
            result = login_rsp.json()

            if result.get("code") == 0 and result.get("data"):
                data = result["data"]
                expire_time = (datetime.now() + timedelta(seconds=7200)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                return {
                    "success": True,
                    "token": data.get("token", ""),
                    "tenantId": data.get("tenantId", ""),
                    "expire_time": expire_time,
                }
            else:
                msg = result.get("msg", "未知错误")
                logger.warning(f"[{plat_name}] 登录返回错误: {msg} (尝试 {attempt + 1}/{max_retries})")

        except requests.exceptions.ConnectionError:
            logger.warning(f"[{plat_name}] 连接被拒绝 (尝试 {attempt + 1}/{max_retries})")
        except Exception as e:
            logger.warning(f"[{plat_name}] 登录异常: {str(e)[:50]} (尝试 {attempt + 1}/{max_retries})")

    return {"success": False, "msg": f"登录失败（已重试{max_retries}次）"}


def search_platform(platform: dict, token_info: dict, search_params: dict) -> dict:
    """
    对单个平台执行搜索，返回搜索结果。
    search_params: text, limit, startTime, endTime
    """
    base_url = platform["base_url"]
    plat_name = platform.get("platform_name", base_url)
    cut = 'picUrl' in search_params
    search_url = f"{base_url}/api/board/aiNvr/search" + ('/cut' if cut else '')

    search_data = {
        "text": search_params.get("text", ""),
        "limit": search_params.get("limit", 15),
        "startTime": search_params.get("startTime", ""),
        "endTime": search_params.get("endTime", ""),
    }
    if cut:
        search_data = {key: search_params[key] for key in ('picUrl', 'x', 'y', 'width', 'height', 'startTime', 'endTime')}

    search_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Applicationid": "1",
        "Authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
        "Content-Type": "application/json;charset=UTF-8",
        "Gray_version": "zuihou",
        "Repeatalarm": "true",
        "Tenantid": token_info.get("tenantId", ""),
        "Token": token_info.get("token", ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        rsp = requests.post(search_url, headers=search_headers, json=search_data, timeout=30,
                            proxies={"http": "", "https": ""})
        rsp.raise_for_status()
        rsp_json = rsp.json()

        if rsp_json.get("code") != 0 or not isinstance(rsp_json.get("data"), list):
            return {
                "success": False,
                "msg": f"搜索无结果: {rsp_json.get('msg', '未知错误')}",
                "images": [],
            }

        images = []
        for item in rsp_json["data"]:
            if not isinstance(item, dict):
                continue
            img_url = item.get("imageUrl", "")
            if img_url:
                images.append(img_url)

        return {"success": True, "images": images, "total": len(images), "records": rsp_json['data']}

    except requests.exceptions.ConnectionError:
        return {"success": False, "msg": "连接被拒绝", "images": []}
    except Exception as e:
        return {"success": False, "msg": f"搜索异常: {str(e)[:50]}", "images": []}


def download_images(urls: list, save_dir: Path, plat_name: str) -> int:
    """下载图片列表到指定目录，返回成功数量"""
    save_dir.mkdir(exist_ok=True, parents=True)
    success_count = 0

    for idx, url in enumerate(urls, 1):
        try:
            safe_name = plat_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            filename = f"{safe_name}_{idx}.jpg"
            if cached_image(save_dir / filename):
                success_count += 1
                continue
            rsp = requests.get(url, timeout=10, stream=True, proxies={"http": "", "https": ""})
            rsp.raise_for_status()
            # 文件名: 时间戳_平台名_序号.jpg
            safe_name = plat_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            filename = f"{safe_name}_{idx}.jpg"
            if not save_image_content(save_dir / filename, response_bytes(rsp), collection_name=plat_name):
                continue
            success_count += 1
        except Exception as e:
            logger.warning(f"下载图片失败 [{plat_name}]: {str(e)[:30]}")

    return success_count


def run_smart_search_task(task_id: str, platforms: list, search_params: dict):
    """
    后台搜索任务主逻辑：
    1. 逐平台登录 + 搜索（并行）
    2. 下载图片按平台分目录
    3. 打包 ZIP
    """
    task = smart_search_tasks[task_id]
    task["status"] = "running"
    task["message"] = "正在初始化..."
    task["platform_results"] = []

    total_platforms = len(platforms)
    search_text = search_params.get("text", "搜索结果")
    safe_text = search_text.replace("/", "_").replace("\\", "_").replace(":", "_")

    # 任务文件夹
    task_dir = Path(SMART_SEARCH_DOWNLOAD_PATH) / f"smart_search_{task_id}"
    task_dir.mkdir(exist_ok=True, parents=True)

    def process_single_platform(platform):
        """处理单个平台：登录 → 搜索 → 下载"""
        plat_name = platform.get("platform_name", "未知平台")
        plat_id = platform.get("plat_id", "")
        result = {
            "plat_id": plat_id,
            "plat_name": plat_name,
            "base_url": platform.get("base_url", ""),
            "status": "pending",
            "msg": "",
            "image_count": 0,
            "download_count": 0,
        }

        # 1. 登录
        result["status"] = "登录中"
        login_result = login_platform(platform)
        if not login_result.get("success"):
            result["status"] = "失败"
            result["msg"] = login_result.get("msg", "登录失败")
            return result

        # 2. 搜索
        result["status"] = "搜索中"
        search_result = search_platform(platform, login_result, search_params)
        if not search_result.get("success"):
            result["status"] = "失败"
            result["msg"] = search_result.get("msg", "搜索失败")
            return result

        images = search_result.get("images", [])
        result["image_count"] = len(images)

        if not images:
            result["status"] = "成功"
            result["msg"] = "搜索完成，无图片结果"
            return result

        # 3. 下载图片（按平台分子目录）
        result["status"] = "下载中"
        plat_dir = task_dir / plat_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        downloaded = download_images(images, plat_dir, plat_name)
        result["download_count"] = downloaded
        result["status"] = "成功"
        result["msg"] = f"找到 {len(images)} 张图片，下载 {downloaded} 张"
        return result

    # 并行处理所有平台（限制并发为 3，避免过多连接）
    task["message"] = f"正在处理 {total_platforms} 个平台..."
    all_results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {
            executor.submit(process_single_platform, p): p for p in platforms
        }
        for future in as_completed(future_map):
            result = future.result()
            all_results.append(result)
            completed += 1
            task["progress"] = int(completed / total_platforms * 80)
            task["message"] = f"已完成 {completed}/{total_platforms} 个平台"
            task["platform_results"] = all_results

    # 统计结果
    success_count = sum(1 for r in all_results if r["status"] == "成功")
    fail_count = total_platforms - success_count
    total_images = sum(r.get("download_count", 0) for r in all_results)

    if total_images == 0:
        task["status"] = "completed" if success_count else "failed"
        task["progress"] = 100
        task["message"] = f"搜索完成（{success_count}成功/{fail_count}失败），无图片可下载"
        task["platform_results"] = all_results
        return

    # 打包 ZIP
    task["message"] = "正在打包 ZIP..."
    task["progress"] = 85
    zip_filename = f"smart_search_{safe_text}_{task_id[:8]}.zip"
    zip_path = Path(SMART_SEARCH_DOWNLOAD_PATH) / zip_filename

    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(task_dir)):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, str(task_dir))
                    zf.write(file_path, arcname)

        task["zip_path"] = str(zip_path)
        task["zip_filename"] = zip_filename
        logger.info(f"ZIP 打包完成: {zip_path}")
    except Exception as e:
        logger.error(f"ZIP 打包失败: {e}")

    task["status"] = "completed"
    task["progress"] = 100
    task["message"] = (
        f"搜索完成！"
        f"{success_count}/{total_platforms} 个平台成功，"
        f"共下载 {total_images} 张图片"
    )
    task["platform_results"] = all_results
