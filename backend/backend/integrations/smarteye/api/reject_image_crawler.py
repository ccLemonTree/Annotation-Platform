# -*- coding: utf-8 -*-
"""
@File    : reject_image_crawler.py
@Time    : 2025/11/26 15:46
@Author  : 陈冲
@Description : 废片数据采集【平台，时间范围，设备ID，事件类型】
@Version : 1.4
"""

import os
from typing import Dict, List, Any
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.config import URLS
from integrations.smarteye.api.utils import get_common_headers, safe_post_request, logger, is_response_success


def get_reject_image_data(
        query_params: Dict[str, Any],
        auth_token: str,
        page_index: int = 1,
        page_size: int = 20,
        get_all_records: bool = False
) -> Dict[str, Any]:
    """
    分页获取所有废片图片数据
    """
    platform_mapping = get_combined_platform_mapping()

    # 1. 确定平台信息
    plat_name = query_params.get('platName', '')
    plat_info = platform_mapping.get(plat_name, {})
    plat_id = plat_info.get('platId', '')

    if not plat_id:
        logger.error(f"错误：平台 '{plat_name}' 不存在或未配置")
        return {"error": "平台不存在"}

    # 2. 处理告警类型转换
    alarm_type_name = query_params.get('alarmTypeName', '')
    # 兼容 frontend 传参 param 'alarm_type_name'
    if not alarm_type_name:
         alarm_type_name = query_params.get('alarm_type_name', '')

    alarm_type_id = query_params.get('alarm_type_id', '')
    if alarm_type_name and not alarm_type_id:
        alarm_type_id = plat_info.get('alarmTypes', {}).get(alarm_type_name, '')

    import concurrent.futures
    import threading
    from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor

    headers = get_common_headers(auth_token)
    all_records = []
    type_url_map = {}
    lock = threading.Lock()
    
    # 优化：分页逻辑重构
    first_page = page_index
    fetch_size = 100 if get_all_records else page_size
    
    # 1. 尝试获取首页
    try:
        def fetch_page_data(page):
            payload = {
                "current": str(page),
                "size": str(fetch_size),
                "model": {
                    "platId": plat_id,
                    "startTime": query_params.get('startTime', ''),
                    "endTime": query_params.get('endTime', ''),
                    "alarmTypeId": alarm_type_id,
                    "deviceId": query_params.get('deviceId', ''),
                    "deviceName": query_params.get('deviceName', '')
                },
                "order": "descending"
            }
            logger.info(f"正在抓取废片数据: {plat_name} 第 {page} 页...")
            return safe_post_request(URLS["reject"], payload, headers)

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

        def process_records(records_list):
            local_records = []
            local_url_map = {}
            for record in records_list:
                t_name = record.get('alarmTypeName', '未知类型')
                pic = record.get('picUrl') or record.get('gifUrl')
                
                full_record = {
                    "id": str(record.get("id")),
                    "platform": plat_name,
                    "dataType": "reject",
                    "targetType": t_name,
                    "targetColor": "gray",
                    "device": record.get("deviceName", "-"),
                    "timestamp": record.get("createTime", record.get("alarmTime", "-")),
                    "picUrl": pic
                }
                local_records.append(full_record)

                if pic and pic.strip() and pic.lower().endswith(('.jpg', '.jpeg', '.png')):
                    if t_name not in local_url_map:
                        local_url_map[t_name] = []
                    local_url_map[t_name].append(pic.strip())

            with lock:
                all_records.extend(local_records)
                for t, urls in local_url_map.items():
                    if t not in type_url_map: type_url_map[t] = []
                    type_url_map[t].extend(urls)

        process_records(initial_records)

        # 2. 并行请求剩余页
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
        logger.error(f"废片分页抓取异常: {e}")

    return {
        "platName": plat_name,
        "total": total_count,
        "pages": total_pages,
        "current": first_page,
        "records": all_records,
        "type_url_map": type_url_map
    }


# 执行示例
if __name__ == "__main__":
    # 认证token (建议使用环境变量或从登录接口获取，此处仅为示例)
    token = os.environ["SMARTEYE_ACCESS_TOKEN"]

    print("=== 示例：查询全部告警类型 ===")
    query_parameters = {
        "startTime": "2025-11-29 00:00:00",
        "endTime": "2025-11-29 23:59:59",
        "platName": "濮院消防",
        "alarmTypeName": "",
    }

    # 注意：如果没有有效的token，这里会失败
    reject_data = get_reject_image_data(query_parameters, token)
    print(reject_data)
    print("请提供有效的Token运行测试")
