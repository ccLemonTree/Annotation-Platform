# -*- -*- coding: utf-8 -*-
"""
@File    : main.py
@Time    : 2025/10/30 19:46
@Author  : 陈冲
@Description : 多IP图像搜索服务（所有图片保存在同一文件夹，文件名含IP名称）
@Version : 1.3
"""
import time
import base64
import requests
import mimetypes
from datetime import datetime, timedelta
from fastapi import FastAPI
from typing import Dict, List, Any, Optional
from pydantic import BaseModel
import asyncio
from pathlib import Path


# -------------------------- 全局配置 --------------------------
# IP与名称映射（新增）
IP_NAME_MAPPING = {
    "36.140.30.77": "嘉兴移动",
    # "39.171.255.109": "苍穹大模型",
    # "39.175.51.2": "义务服装厂",
    # "10.11.12.206": "可口可乐"
}

class Lifespan:
    def __init__(self, app: FastAPI):
        self.app = app
        self.token_refresh_task: asyncio.Task | None = None

    async def __aenter__(self):
        print("服务启动中，首次刷新Token...")
        initial_refresh = refresh_all_tokens()
        print(f"首次Token刷新结果：{initial_refresh}")
        self.token_refresh_task = asyncio.create_task(scheduled_token_refresh())
        print("服务启动完成，定时任务已开启（每天0点刷新Token）")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.token_refresh_task and not self.token_refresh_task.done():
            self.token_refresh_task.cancel()
            await self.token_refresh_task
        print("服务已关闭，定时任务已停止")


app = FastAPI(title="多IP图像搜索服务", description="支持多参数搜索+自动登录")
app.lifespan = Lifespan(app)

# 账号密码配置
USERNAME = "superAdmin"
PASSWORD_DICT = {
    "36.140.30.77": "HzxfAdmin0321~"
}

# 验证码识别模型配置
CAPTCHA_MODEL_API = "http://39.185.65.85:8690/bigv1/chat/completions"
CAPTCHA_MODEL_NAME = "Qwen3-VL-8B-Instruct"

# Token缓存
TOKEN_CACHE: Dict[str, Dict[str, str]] = {}

# 图片保存根路径
ROOT_SAVE_PATH = Path("./image_search_results")
ROOT_SAVE_PATH.mkdir(exist_ok=True, parents=True)


# -------------------------- 模型定义 --------------------------
class SearchRequest(BaseModel):
    text: str  # 搜索内容，必填字段
    limit: int = 15  # 默认返回15条结果
    startTime: Optional[str] = ""  # 开始时间，默认空字符串
    endTime: Optional[str] = ""  # 结束时间，默认空字符串


class IPSearchResult(BaseModel):
    ip: str
    ip_name: str  # 新增：IP对应的名称（如安邦、苍穹大模型）
    status: str
    msg: str
    save_path: Optional[str] = None  # 统一文件夹路径


class SearchResponse(BaseModel):
    code: int = 0
    msg: str
    search_text: str
    total_ip: int
    success_ip: int
    detail: List[IPSearchResult]
    root_save_path: str


class TokenRefreshResponse(BaseModel):
    code: int = 0
    msg: str
    refresh_time: str
    result: Dict[str, str]


class TokenStatusResponse(BaseModel):
    code: int = 0
    check_time: str
    token_cache: Dict[str, Dict[str, str]]
    expired_ip: List[str]


# -------------------------- 核心工具函数 --------------------------
def recognize_captcha_by_qwen(captcha_binary: bytes) -> str:
    """验证码识别"""
    CAPTCHA_PROMPT = """
请精准识别这张验证码图片中的数字，并帮我计算结果。
要求：
1. 只返回计算的结果，不要添加任何解释、空格或多余文字
2. 若无法清晰识别，直接返回空字符串
"""
    try:
        base64_captcha = base64.b64encode(captcha_binary).decode('utf-8')
        mime_type = mimetypes.guess_type("temp_captcha")[0] or "image/png"
        if not mime_type.startswith("image/"):
            mime_type = "image/png"
        payload = {
            "model": CAPTCHA_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CAPTCHA_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_captcha}"}}
                    ]
                }
            ],
            "max_tokens": 20,
            "temperature": 0.0
        }
        response = requests.post(
            CAPTCHA_MODEL_API,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"].strip()
        if result.lower() in ["unrecognizable", "无法识别", "空"]:
            return ""
        return result
    except Exception:
        return ""


def login_single_ip(ip: str) -> Dict[str, str]:
    """单个IP登录"""
    try:
        password = PASSWORD_DICT[ip]
        t = round(time.time() * 1000)
        key = f"29EABBD97500C16F9423DEEB{t}"
        captcha_url = f"http://{ip}:8690/api/oauth/anyTenant/captcha?key={key}&_t={t}"

        captcha_rsp = requests.get(
            captcha_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"},
            timeout=5
        )
        captcha_rsp.raise_for_status()

        code = recognize_captcha_by_qwen(captcha_rsp.content)
        if not code:
            return {"code": "-1", "msg": "验证码识别失败", "tenantId": "", "token": ""}

        login_url = f"http://{ip}:8690/api/oauth/anyTenant/login"
        login_data = {
            "username": USERNAME,
            "password": password,
            "code": code,
            "grantType": "CAPTCHA",
            "key": key
        }
        login_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            "Authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
            "Content-Type": "application/json",
            "Host": f"{ip}:8690"
        }
        login_rsp = requests.post(
            login_url,
            headers=login_headers,
            json=login_data,
            timeout=5
        )
        login_rsp.raise_for_status()
        login_result = login_rsp.json()

        if login_result.get("code") == 0 and login_result.get("data"):
            data = login_result["data"]
            expire_time = (datetime.now() + timedelta(seconds=7200)).strftime("%Y-%m-%d %H:%M:%S")
            return {
                "code": "0",
                "msg": "登录成功",
                "tenantId": data.get("tenantId", ""),
                "token": data.get("token", ""),
                "expire_time": expire_time
            }
        else:
            return {"code": "-2", "msg": f"登录失败：{login_result.get('msg', '未知错误')}", "tenantId": "", "token": ""}
    except requests.exceptions.ConnectionError:
        return {"code": "-3", "msg": "连接被拒绝", "tenantId": "", "token": ""}
    except Exception as e:
        return {"code": "-4", "msg": f"未知错误：{str(e)[:30]}", "tenantId": "", "token": ""}


def refresh_all_tokens() -> Dict[str, str]:
    """刷新所有IP的Token"""
    refresh_result = {}
    for ip in PASSWORD_DICT.keys():
        login_result = login_single_ip(ip)
        if login_result["code"] == "0":
            TOKEN_CACHE[ip] = {
                "tenantId": login_result["tenantId"],
                "token": login_result["token"],
                "expire_time": login_result["expire_time"]
            }
            refresh_result[ip] = "成功"
        else:
            refresh_result[ip] = f"失败：{login_result['msg']}"
    return refresh_result


async def scheduled_token_refresh():
    """每天0点定时刷新所有Token"""
    while True:
        now = datetime.now()
        next_refresh = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_seconds = (next_refresh - now).total_seconds()

        await asyncio.sleep(sleep_seconds)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始定时刷新所有Token")
        refresh_result = refresh_all_tokens()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Token刷新结果：{refresh_result}")


def ensure_valid_token(ip: str) -> bool:
    """确保Token有效，无效则重新登录"""
    cache = TOKEN_CACHE.get(ip)
    if cache:
        try:
            expire_time = datetime.strptime(cache["expire_time"], "%Y-%m-%d %H:%M:%S")
            if expire_time > datetime.now():
                return True
        except Exception:
            pass

    print(f"IP {ip} Token无效，自动重新登录...")
    login_result = login_single_ip(ip)
    if login_result["code"] == "0":
        TOKEN_CACHE[ip] = {
            "tenantId": login_result["tenantId"],
            "token": login_result["token"],
            "expire_time": login_result["expire_time"]
        }
        print(f"IP {ip} 重新登录成功")
        return True
    else:
        print(f"IP {ip} 重新登录失败：{login_result['msg']}")
        return False


# -------------------------- 搜索函数（修改保存路径） --------------------------
def search_single_ip(
    ip: str,
    text: str,
    save_dir: Path,  # 统一保存文件夹（不再按IP分目录）
    limit: int,
    start_time: str,
    end_time: str
) -> Dict[str, Any]:
    """单个IP搜索+图片下载（所有图片保存在同一文件夹，文件名含IP名称）"""
    # 获取IP对应的名称（如“安邦”）
    ip_name = IP_NAME_MAPPING.get(ip, ip)  # 若未配置映射，默认使用IP

    if not ensure_valid_token(ip):
        return {
            "ip": ip,
            "ip_name": ip_name,
            "status": "失败",
            "msg": "Token获取失败，无法搜索",
            "save_path": str(save_dir)
        }

    cache = TOKEN_CACHE[ip]
    base_url = f"http://{ip}:8690/api/board/aiNvr/search"
    search_data = {
        "text": text,
        "limit": limit,
        "startTime": start_time,
        "endTime": end_time
    }
    search_headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Applicationid': '1',
        'Authorization': 'bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json;charset=UTF-8',
        'Gray_version': 'zuihou',
        'Host': f'{ip}:8690',
        'Origin': f'http://{ip}:8690',
        'Referer': f'http://{ip}:8690/',
        'Repeatalarm': 'true',
        'Tenantid': cache["tenantId"],
        'Token': cache["token"],
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
    }

    try:
        rsp = requests.post(base_url, headers=search_headers, json=search_data, timeout=10)
        rsp.raise_for_status()
        rsp_json = rsp.json()
        if rsp_json.get("code") != 0 or not rsp_json.get("data"):
            return {
                "ip": ip,
                "ip_name": ip_name,
                "status": "失败",
                "msg": f"搜索无结果：{rsp_json.get('msg', '未知错误')}",
                "save_path": str(save_dir)
            }

        image_urls = [item["imageUrl"] for item in rsp_json["data"]]
        if not image_urls:
            return {
                "ip": ip,
                "ip_name": ip_name,
                "status": "失败",
                "msg": "未获取到图片URL",
                "save_path": str(save_dir)
            }

        # 确保统一保存文件夹存在（不再创建IP子目录）
        save_dir.mkdir(exist_ok=True, parents=True)

        success_count = 0
        for idx, img_url in enumerate(image_urls, 1):
            try:
                img_rsp = requests.get(img_url, timeout=5)
                img_rsp.raise_for_status()
                # 文件名格式：时间戳_IP名称_序号.jpg（如20251031120000_安邦_1.jpg）
                img_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{ip_name}_{idx}.jpg"
                # 替换文件名中的特殊字符（避免创建文件失败）
                img_filename = img_filename.replace("/", "_").replace("\\", "_").replace(":", "_")
                with open(save_dir / img_filename, "wb") as f:
                    f.write(img_rsp.content)
                success_count += 1
            except Exception as e:
                print(f"下载图片失败：{str(e)}")
                continue

        return {
            "ip": ip,
            "ip_name": ip_name,
            "status": "成功",
            "msg": f"共获取{len(image_urls)}张图片，成功下载{success_count}张",
            "save_path": str(save_dir)
        }
    except requests.exceptions.ConnectionError:
        return {
            "ip": ip,
            "ip_name": ip_name,
            "status": "失败",
            "msg": "连接被拒绝",
            "save_path": str(save_dir)
        }
    except Exception as e:
        return {
            "ip": ip,
            "ip_name": ip_name,
            "status": "失败",
            "msg": f"未知错误：{str(e)[:30]}",
            "save_path": str(save_dir)
        }


# -------------------------- FastAPI接口 --------------------------
@app.post("/search-and-download", summary="多IP搜索并下载图片", response_model=SearchResponse)
def search_and_download(request: SearchRequest) -> SearchResponse:
    """
    传入搜索参数（JSON格式），所有图片保存在同一文件夹：
    - 请求体示例：
      {
        "text": "绿色奔驰车",
        "limit": 30,
        "startTime": "2025-10-01 00:00:00",
        "endTime": "2025-10-31 23:59:59"
      }
    - 图片保存路径：./image_search_results/[搜索内容]/
    - 文件名格式：时间戳_IP名称_序号.jpg（如20251031120000_安邦_1.jpg）
    """
    text = request.text
    limit = request.limit
    start_time = request.startTime
    end_time = request.endTime

    # 过滤非法路径字符，创建统一保存文件夹（不包含IP子目录）
    safe_text = text.replace("/", "_").replace("\\", "_").replace(":", "_")
    search_save_dir = ROOT_SAVE_PATH / safe_text  # 所有IP的图片都保存在这里
    search_save_dir.mkdir(exist_ok=True, parents=True)

    all_results = []
    for ip in PASSWORD_DICT.keys():
        ip_result = search_single_ip(
            ip=ip,
            text=text,
            save_dir=search_save_dir,  # 传入统一文件夹
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )
        all_results.append(IPSearchResult(**ip_result))

    success_count = sum(1 for res in all_results if res.status == "成功")
    total_ip = len(PASSWORD_DICT)

    return SearchResponse(
        msg=f"搜索完成，共{total_ip}个IP，{success_count}个IP搜索成功",
        search_text=text,
        total_ip=total_ip,
        success_ip=success_count,
        detail=all_results,
        root_save_path=str(search_save_dir)
    )


@app.get("/refresh-token-now", summary="手动刷新所有Token", response_model=TokenRefreshResponse)
def refresh_token_now() -> TokenRefreshResponse:
    refresh_result = refresh_all_tokens()
    return TokenRefreshResponse(
        msg="手动Token刷新完成",
        refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        result=refresh_result
    )


@app.get("/token-status", summary="查看当前Token缓存状态", response_model=TokenStatusResponse)
def get_token_status() -> TokenStatusResponse:
    expired_ip = [
        ip for ip, cache in TOKEN_CACHE.items()
        if datetime.strptime(cache["expire_time"], "%Y-%m-%d %H:%M:%S") <= datetime.now()
    ]
    return TokenStatusResponse(
        check_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        token_cache=TOKEN_CACHE,
        expired_ip=expired_ip
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)