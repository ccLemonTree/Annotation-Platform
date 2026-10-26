# -*- coding: utf-8 -*-
"""
@File    : alarm_data_crawler.py
@Time    : 2025/11/26 15:45
@Author  : 陈冲
@Description : 报警数据【平台，时间范围，设备ID，事件类型】
@Version : 1.5
"""

import json
from typing import Dict, List, Any
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.config import URLS
from integrations.smarteye.api.utils import get_common_headers, safe_post_request, logger, is_response_success


def get_alarm_image_data(
        query_params: Dict[str, Any],
        auth_token: str,
        page_index: int = 1,
        page_size: int = 20,
        get_all_records: bool = False
) -> Dict[str, Any]:
    """
    获取报警图片数据 (支持分页查询或全量抓取)
    """
    platform_mapping = get_combined_platform_mapping()

    # 1. 确定平台 ID 和名称 (复用逻辑)
    plat_id = ""
    plat_name = ""
    if 'platId' in query_params and query_params['platId']:
        plat_id = query_params['platId']
        for name, info in platform_mapping.items():
            if info.get('platId') == plat_id: plat_name = name; break
    elif 'platName' in query_params and query_params['platName']:
        plat_name = query_params['platName']
        # 修正：确保能正确获取到 ID
        if plat_name in platform_mapping:
            plat_id = platform_mapping[plat_name].get('platId', '')

    if not plat_id:
        logger.error(f"未找到平台信息: {query_params}")
        return {"error": "未找到平台信息"}

    # 2. 获取告警类型 ID 映射
    alarm_type_id = query_params.get('alarm_type_id', '')
    alarm_type_name = query_params.get('alarm_type_name', '')
    alarm_types_map = platform_mapping.get(plat_name, {}).get('alarmTypes', {})
    alarm_id_to_name = {v: k for k, v in alarm_types_map.items()} # ID -> Name

    # 类型名称转 ID
    if alarm_type_name and not alarm_type_id:
        alarm_type_id = alarm_types_map.get(alarm_type_name, '')
        if not alarm_type_id:
            logger.warning(f"类型 {alarm_type_name} 不存在，将查询全部类型")

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
    
    # 1. 首先请求第一页以获取总页数
    try:
        def fetch_page_data(page):
            payload = {
                "current": str(page),
                "size": str(fetch_size),
                "model": {
                    "platId": plat_id,
                    "startTime": query_params.get('startTime', ''),
                    "endTime": query_params.get('endTime', ''),
                    "deviceId": query_params.get('deviceId', ''),
                    "alarmTypeId": alarm_type_id,
                    "send":True
                },
                "order": "descending",
                "sort": "alarmTime"
            }
            logger.info(f"正在查询报警数据: {plat_name} 第 {page} 页...")
            return safe_post_request(URLS["alarm_list"], payload, headers)

        # 获取首页
        response_data = fetch_page_data(first_page)
        # logger.info(f"报警数据: {response_data} ")
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
                t_id = str(record.get('alarmTypeId'))
                t_name = record.get('alarmTypeName') or alarm_id_to_name.get(t_id, f"未知类型({t_id})")
                pic = record.get('alarmPic')
                
                full_record = {
                    "id": str(record.get("id")),
                    "platform": plat_name,
                    "dataType": "alarm",
                    "targetType": t_name,
                    "targetColor": "red",
                    "device": record.get("deviceName", "-"),
                    "deviceId": record.get("deviceId", ""),
                    "timestamp": record.get("alarmTime", "-"),
                    "picUrl": pic
                }
                local_records.append(full_record)

                if pic and pic.strip():
                    if t_name not in local_url_map: local_url_map[t_name] = []
                    local_url_map[t_name].append(pic.strip())

            with lock:
                all_records.extend(local_records)
                for t, urls in local_url_map.items():
                    if t not in type_url_map: type_url_map[t] = []
                    type_url_map[t].extend(urls)

        process_records(initial_records)

        # 2. 如果是全量抓取且有多页，并行请求剩余页码
        if get_all_records and total_pages > first_page:
            remaining_pages = list(range(first_page + 1, total_pages + 1))
            # 限制爬虫抓取并发，避免对 API 造成过大压力
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_page = {executor.submit(fetch_page_data, p): p for p in remaining_pages}
                for future in concurrent.futures.as_completed(future_to_page):
                    p_res = future.result()
                    if p_res and is_response_success(p_res):
                        p_data = p_res.get('data', {})
                        process_records(p_data.get('records', []))

    except Exception as e:
        logger.error(f"报警数据查询异常: {e}", exc_info=True)

    return {
        "platName": plat_name,
        "total": total_count,
        "pages": total_pages,
        "current": first_page,
        "records": all_records,
        "type_url_map": type_url_map
    }


if __name__ == "__main__":
    # 认证token
    token = "YOUR_TEST_TOKEN_HERE"

    # 示例1：查询所有时间的全部告警类型
    print("=== 示例1：查询所有时间的全部告警类型 ===")
    query_parameters = {
        "platName": "3.0-嘉兴小区",
        "startTime": '',
        "endTime": '',
        "alarmTypeName": "垃圾堆放(社区)",
        "current": "",
        "size": 10
    }

    # alarm_data = get_alarm_image_data(query_parameters, token)
    print("请提供有效的Token运行测试")
