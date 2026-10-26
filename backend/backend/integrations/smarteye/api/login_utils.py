from integrations.smarteye.configuration import get_env
# -*- coding: utf-8 -*-
import os
import time
import base64
import requests
from datetime import datetime, timedelta
from typing import Dict, Any
from integrations.smarteye.api.config import CAPTCHA_MODEL_NAME, CAPTCHA_MODEL_API, API_BASE_URL
import re


def recognize_captcha(captcha_binary: bytes) -> str:
    """调用大模型识别验证码二进制流"""
    CAPTCHA_PROMPT = "请精准识别这张验证码图片中的数字，并帮我计算结果。要求：只返回计算的结果，不要添加任何解释。"
    try:
        base64_captcha = base64.b64encode(captcha_binary).decode('utf-8')
        payload = {
            "model": CAPTCHA_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CAPTCHA_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_captcha}"}}
                    ]
                }
            ],
            "max_tokens": 20,
            "temperature": 0.0
        }
        response = requests.post(CAPTCHA_MODEL_API, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"验证码识别异常: {e}")
        return ""

# def recognize_captcha(captcha_binary: bytes) -> str:
#     """调用大模型识别验证码二进制流"""
#     # 提示词可以保持不变，或者加强约束
#     CAPTCHA_PROMPT = "请精准识别这张验证码图片中的数字，并帮我计算结果。要求：只返回计算的结果，不要添加任何解释。"

#     try:
#         base64_captcha = base64.b64encode(captcha_binary).decode('utf-8')
#         payload = {
#             "model": CAPTCHA_MODEL_NAME,
#             "messages": [
#                 {
#                     "role": "user",
#                     "content": [
#                         {"type": "text", "text": CAPTCHA_PROMPT},
#                         {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_captcha}"}}
#                     ]
#                 }
#             ],
#             # --- 修改点 1：大幅增加 max_tokens ---
#             # 推理模型需要消耗大量 token 进行思考，20 远远不够
#             "max_tokens": 2000,
#             "temperature": 0.0  # 保持 0 以获得确定性结果
#         }

#         response = requests.post(CAPTCHA_MODEL_API, json=payload, timeout=60)
#         response.raise_for_status()

#         raw_content = response.json()["choices"][0]["message"]["content"]

#         match = re.search(r'<\|begin_of_box\|>(.*?)<\|end_of_box\|>', raw_content)
#         if match:
#             clean_content = match.group(1)
#         else:
#             # 如果没有标签，可能模型直接返回了数字，或者使用了其他格式
#             clean_content = raw_content

#         final_result = clean_content.strip()
#         return final_result
#     except Exception as e:
#         print(f"验证码识别异常: {e}")
#         return ""


def get_token_by_login() -> Dict[str, Any]:
    """
    核心方法：自动完成 验证码获取 -> 识别 -> 登录 -> 获取Token

    Args:

    Returns:
        包含 token, tenantId, expire_time 的字典，失败时返回含有 error 的信息
    """
    try:
        # 1. 准备验证码参数
        t = round(time.time() * 1000)
        key = f"29EABBD97500C16F9423DEEB{t}"
        captcha_url = f"{API_BASE_URL}/api/anyTenant/captcha?key={key}&_t={t}"

        # 2. 获取验证码图片
        captcha_rsp = requests.get(captcha_url, timeout=5)
        captcha_rsp.raise_for_status()

        # 3. 调用大模型识别
        code = recognize_captcha(captcha_rsp.content)
        if not code:
            return {"success": False, "msg": "验证码识别失败"}

        # 4. 执行登录
        login_url = f"{API_BASE_URL}/api/anyTenant/login"
        login_data = {
            "username": get_env("LOGIN_USER"),
            "password": get_env("PASSWORD"),
            "code": code,
            "grantType": "CAPTCHA",
            "key": key
        }
        login_headers = {
            "Authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36..."
        }

        login_rsp = requests.post(login_url, headers=login_headers, json=login_data, timeout=5)
        login_rsp.raise_for_status()
        res = login_rsp.json()

        # 5. 解析结果
        if res.get("code") == 0 and res.get("data"):
            data = res["data"]
            # 记录过期时间（通常为2小时）
            expire_time = (datetime.now() + timedelta(seconds=7100)).strftime("%Y-%m-%d %H:%M:%S")
            return {
                "success": True,
                "token": data.get("token"),
                "tenantId": data.get("tenantId"),
                "expire_time": expire_time
            }
        else:
            return {"success": False, "msg": res.get("msg", "登录接口返回错误")}

    except Exception as e:
        return {"success": False, "msg": f"登录流程异常: {str(e)}"}


def is_token_invalid_response(response: requests.Response) -> bool:
    """
    根据你提供的返回特征判断 Token 是否失效
    特征：HTTP 401 且 JSON code 为 40005
    """
    try:
        res_json = response.json()
        if res_json.get("code") == 40005:
            return True
        if "解析用户身份错误" in res_json.get("msg", ""):
            return True
    except Exception:
        pass

    return False


# --- 使用示例 ---
if __name__ == "__main__":
    result = get_token_by_login()
    if result["success"]:
        print(f"获取Token成功: {result['token']}")
    else:
        print(f"登录失败: {result['msg']}")
