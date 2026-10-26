# -*- coding: utf-8 -*-
"""
@File    : capture_image_crawler.py
@Time    : 2025/11/26 15:44
@Author  : 陈冲
@Description : 抓图数据采集【平台，时间范围，设备ID】
@Version : 1.5
"""

import threading
from typing import Dict, List, Any
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.config import URLS
from integrations.smarteye.api.utils import get_common_headers, safe_post_request, logger, is_response_success


def get_capture_image_data(
        query_params: Dict[str, Any],
        auth_token: str,
        page_index: int = 1,
        page_size: int = 20,
        get_all_records: bool = False
) -> Dict[str, Any]:
    """
    分页获取所有抓图数据
    """
    platform_mapping = get_combined_platform_mapping()
    plat_name = query_params.get('platName', '')
    plat_id = platform_mapping.get(plat_name, {}).get('platId', '')

    if not plat_id: return {"error": "平台不存在"}

    import concurrent.futures
    from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor

    headers = get_common_headers(auth_token)
    # 使用线程安全的方式收集数据
    all_records = []
    image_urls = []
    lock = threading.Lock()

    first_page = page_index
    fetch_size = 100 if get_all_records else page_size

    total_count = 0
    total_pages = 0

    target_type_display = query_params.get('alarm_type_name') or "抓图记录"
    folder_name = query_params.get('alarm_type_name') or "抓图数据"

    try:
        def fetch_page_data(page):
            # 将前端空字符串设备ID转为 None (对应 JSON 的 null)
            device_id = query_params.get('deviceId')
            device_id = device_id if device_id else None

            model_dict = {
                "platId": str(plat_id),
                "startTime": query_params.get('startTime', ''),
                "endTime": query_params.get('endTime', ''),
                "success": True,
                "deviceId": device_id,
                "deviceIp": None,
                "deviceName": None,
                "analyseIds": []
            }

            # 如果存在检测目标/类型参数（由 targetType 映射而来），则一并加入参数请求体中
            alarm_type_name = query_params.get('alarm_type_name')
            if alarm_type_name:
                model_dict["alarmTypeName"] = alarm_type_name

            payload = {
                "current": page,
                "size": fetch_size,
                "extra": {},
                "model": model_dict,
                "order": "descending",
                "searchCount": True,
                "sort": "id"
            }
            logger.info(f"正在抓取抓图数据: {plat_name} 第 {page} 页...")
            return safe_post_request(URLS["capture"], payload, headers)

        def process_records(records_list):
            """将接口记录转换为统一格式，使用锁保证线程安全"""
            local_records = []
            local_urls = []
            for record in records_list:
                pic = record.get('backPath')
                actual_target_type = record.get("alarmTypeName") or target_type_display

                full_record = {
                    "id": str(record.get("id")),
                    "platform": plat_name,
                    "dataType": "capture",
                    "targetType": actual_target_type,
                    "targetColor": "blue",
                    "device": record.get("deviceName", "-"),
                    "timestamp": record.get("createTime", "-"),
                    "picUrl": pic
                }
                local_records.append(full_record)

                if pic and pic.strip():
                    local_urls.append(pic.strip())

            with lock:
                all_records.extend(local_records)
                image_urls.extend(local_urls)

        # 1. 抓取首页
        response_data = fetch_page_data(first_page)
        if not response_data or not is_response_success(response_data):
            return {
                "platName": plat_name, "total": 0, "pages": 0, "current": first_page,
                "records": [], "type_url_map": {}
            }

        data = response_data.get('data', {})
        total_count = int(data.get('total', 0))
        total_pages = int(data.get('pages', 0))
        initial_records = data.get('records', [])

        process_records(initial_records)

        # 2. 如果请求所有数据且页数大于1，则并行抓取
        if get_all_records and total_pages > first_page:
            remaining_pages = list(range(first_page + 1, total_pages + 1))
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_page = {executor.submit(fetch_page_data, p): p for p in remaining_pages}
                for future in concurrent.futures.as_completed(future_to_page):
                    p_res = future.result()
                    if p_res and is_response_success(p_res):
                        p_data = p_res.get('data', {})
                        process_records(p_data.get('records', []))

    except Exception as e:
        logger.error(f"抓图分页异常: {e}")

    return {
        "platName": plat_name,
        "total": total_count,
        "pages": total_pages,
        "current": first_page,
        "records": all_records,
        "type_url_map": {folder_name: image_urls} if image_urls else {}
    }


if __name__ == "__main__":
    # 认证token
    token = "YOUR_TEST_TOKEN_HERE"

    # 示例：查询抓图数据
    print("=== 示例：查询抓图数据 ===")
    query_parameters = {
        "startTime": "2025-11-23 00:00:00",
        "endTime": "2025-11-29 23:59:59",
        "platName": "新算法服务器平台",
        "current": "",
        "size": 10
    }

    # capture_data = get_capture_image_data(query_parameters, token)
    print("请提供有效的Token运行测试")
