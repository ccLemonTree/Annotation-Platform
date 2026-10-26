"""
API测试脚本
"""
import os
import requests
import json

BASE_URL = "http://localhost:8000"

def test_login():
    """测试登录"""
    print("\n=== 测试登录 ===")
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        data={
            "username": "admin",
            "password": os.environ["ANNOTATION_PASSWORD"]
        }
    )
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")

    if response.status_code == 200 and "access_token" in data:
        print("✓ 登录成功")
        return data["access_token"]
    else:
        print("✗ 登录失败")
        return None


def test_get_current_user(token):
    """测试获取当前用户信息"""
    print("\n=== 测试获取当前用户信息 ===")
    response = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")

    if response.status_code == 200:
        print("✓ 获取用户信息成功")
    else:
        print("✗ 获取用户信息失败")


def test_health_check():
    """测试健康检查"""
    print("\n=== 测试健康检查 ===")
    response = requests.get(f"{BASE_URL}/health")
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")

    if response.status_code == 200:
        print("✓ 健康检查成功")
    else:
        print("✗ 健康检查失败")


if __name__ == "__main__":
    print("开始测试 API...")

    # 测试健康检查
    test_health_check()

    # 测试登录
    token = test_login()

    # 如果登录成功，测试获取用户信息
    if token:
        test_get_current_user(token)

    print("\n测试完成!")
