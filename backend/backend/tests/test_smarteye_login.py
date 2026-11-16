"""Captcha model requests stay compatible with the deployed Qwen service."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from integrations.smarteye.api import login_utils


def test_captcha_uses_qwen_without_thinking(monkeypatch):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": " 10\n"}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(login_utils.requests, "post", post)

    assert login_utils.recognize_captcha(b"captcha-image") == "10"

    assert post.call_args.args == ("http://10.11.12.123:31030/v1/chat/completions",)
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "Qwen3.8-27B"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    image = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]) == b"captcha-image"


def _response(status, payload):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


@pytest.fixture
def login_mocks(monkeypatch):
    mocks = SimpleNamespace(
        captcha=Mock(return_value=Mock(content=b"captcha-image")),
        login=Mock(return_value=_response(200, {"code": 0, "data": {
            "token": "test-token", "tenantId": "test-tenant"}})),
        recognize=Mock(return_value="10"),
    )
    monkeypatch.setattr(login_utils.requests, "get", mocks.captcha)
    monkeypatch.setattr(login_utils.requests, "post", mocks.login)
    monkeypatch.setattr(login_utils, "recognize_captcha", mocks.recognize)
    monkeypatch.setattr(login_utils, "get_env", lambda name: "test-" + name)
    monkeypatch.setattr(login_utils, "time", SimpleNamespace(time=Mock(side_effect=[1, 2, 3])))
    return mocks


def test_login_retries_empty_recognition_with_new_captcha(login_mocks):
    login_mocks.recognize.side_effect = ["", "10"]

    assert login_utils.get_token_by_login()["token"] == "test-token"
    assert login_mocks.captcha.call_count == 2
    assert login_mocks.captcha.call_args_list[0] != login_mocks.captcha.call_args_list[1]
    assert login_mocks.login.call_count == 1


@pytest.mark.parametrize("status", [200, 400])
def test_login_retries_rejected_captcha(login_mocks, status):
    login_mocks.login.side_effect = [
        _response(status, {"code": -9, "msg": "验证码不正确"}),
        login_mocks.login.return_value,
    ]

    assert login_utils.get_token_by_login()["token"] == "test-token"
    assert login_mocks.captcha.call_count == 2
    assert login_mocks.captcha.call_args_list[0] != login_mocks.captcha.call_args_list[1]
    assert login_mocks.login.call_count == 2


@pytest.mark.parametrize("failure", ["empty", "rejected"])
def test_login_stops_after_three_captcha_failures(login_mocks, failure):
    if failure == "empty":
        login_mocks.recognize.return_value = ""
    else:
        login_mocks.login.return_value = _response(400, {"code": -9, "msg": "验证码不正确"})

    result = login_utils.get_token_by_login()

    assert result["success"] is False
    assert "3" in result["msg"]
    assert login_mocks.captcha.call_count == 3


@pytest.mark.parametrize("status", [200, 400])
def test_login_does_not_retry_other_errors(login_mocks, status):
    login_mocks.login.return_value = _response(status, {"code": -1, "msg": "账号或密码错误"})

    assert login_utils.get_token_by_login()["success"] is False
    assert login_mocks.captcha.call_count == 1
    assert login_mocks.login.call_count == 1
