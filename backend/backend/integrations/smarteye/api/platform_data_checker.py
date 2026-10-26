# -*- coding: utf-8 -*-
"""
@File    : platform_data_checker.py
@Time    : 2025/11/29 21:06
@Author  : 陈冲
@Description : 有效平台获取接口
@Version : 1.1
"""

import requests
from typing import Dict, List, Any, Optional
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.config import URLS
from integrations.smarteye.api.utils import get_common_headers, safe_post_request, logger


def get_platforms_with_alarm_data(query_params, auth_token: str) -> List[Dict[str, Any]]:
    """
    查询平台中存在告警数据的信息（支持单个平台或所有平台）

    Args:
        query_params: 查询参数，包含platName、startTime、endTime等
        auth_token: 认证token

    Returns:
        有告警数据的平台列表，格式：[{"name": "平台名称", "id": "平台ID", "alarm_types": [...]}, ...]
    """
    # 获取平台映射关系
    platform_mapping = get_combined_platform_mapping()

    # 获取通用请求头
    headers = get_common_headers(auth_token)

    # 处理参数，platName未传入则默认为空字符串
    plat_name = query_params.get('platName', '')
    start_time = query_params.get('startTime', '')
    end_time = query_params.get('endTime', '')

    # 存储有数据的平台
    platforms_with_data = []

    # 确定要遍历的平台列表
    platforms_to_check = []

    if plat_name and plat_name.strip():
        # 如果传入了platName，只检查指定平台
        if plat_name in platform_mapping:
            platforms_to_check.append((plat_name, platform_mapping[plat_name]))
        else:
            logger.error(f"错误：未找到平台 '{plat_name}'")
            return platforms_with_data
    else:
        # 如果未传入platName，检查所有平台
        platforms_to_check = platform_mapping.items()

    # 遍历选定的平台
    for plat_name_check, plat_info in platforms_to_check:
        plat_id = plat_info.get('platId', '')

        if not plat_id:
            logger.warning(f"平台 {plat_name_check} 缺少platId，跳过")
            continue

        try:
            # 构建请求payload，只添加非空参数
            alarm_id_payload = {}

            # plat_id不为空时才添加
            if plat_id.strip():
                alarm_id_payload["platId"] = plat_id

            # start_time不为空时才添加
            if start_time and start_time.strip():
                alarm_id_payload["startTime"] = start_time.strip()

            # end_time不为空时才添加
            if end_time and end_time.strip():
                alarm_id_payload["endTime"] = end_time.strip()

            # 调用API检查该平台是否有告警数据
            # 使用 safe_post_request 替代直接的 requests.post
            response_data = safe_post_request(URLS["alarm_stat"], alarm_id_payload, headers)

            if not response_data:
                continue

            alarm_data = response_data.get('data', [])

            # 只保留data不为空的平台
            if alarm_data and len(alarm_data) > 0:
                # 提取告警类型信息
                alarm_types = []
                for item in alarm_data:
                    alarm_types.append({
                        "alarmTypeId": item.get('alarmTypeId'),
                        "alarmTypeName": item.get('alarmTypeName'),
                        "alarmCount": item.get('count', 0)
                    })

                # 添加到结果列表
                platforms_with_data.append({
                    "plat_name": plat_name_check,
                    "plat_id": plat_id,
                    "alarm_types_count": len(alarm_data),
                    "alarm_types": alarm_types,
                    "total_count": sum(item.get('count', 0) for item in alarm_data)
                })

                logger.info(f"平台 {plat_name_check} (ID: {plat_id}) - 有数据，包含 {len(alarm_data)} 种告警类型")
            else:
                logger.info(f"平台 {plat_name_check} (ID: {plat_id}) - 暂无数据")

        except Exception as e:
            logger.error(f"检查平台 {plat_name_check} (ID: {plat_id}) 时出错: {str(e)}")
            continue

    if plat_name and plat_name.strip():
        logger.info(f"\n查询平台 '{plat_name}' 完成，找到 {len(platforms_with_data)} 条数据")
    else:
        logger.info(f"\n总计找到 {len(platforms_with_data)} 个有告警数据的平台")

    return platforms_with_data


# 使用示例
if __name__ == "__main__":
    token = "YOUR_TEST_TOKEN_HERE"
    query_params = {
        "platName": "3.0-嘉兴小区",
        "startTime": "",
        "endTime": ""
    }

    # 获取有数据的平台列表
    result = get_platforms_with_alarm_data(query_params, token)

    # 打印结果
    print("\n=== 有告警数据的平台列表 ===")
    print("请提供有效的Token运行测试")
