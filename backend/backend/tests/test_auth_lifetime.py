from datetime import timedelta

import pytest
from jose import jwt

from config.settings import Settings, settings
from services.auth import create_access_token


def test_default_login_lifetime_is_24_hours(monkeypatch):
    monkeypatch.delenv('JWT_EXPIRATION', raising=False)
    configured = Settings(_env_file=None, JWT_SECRET_KEY='isolated-test-secret-key-at-least-32-characters')
    assert configured.JWT_EXPIRATION == 86400


@pytest.mark.parametrize('seconds', [86400, 7200])
def test_login_response_and_token_share_configured_lifetime(client, test_user, monkeypatch, seconds):
    monkeypatch.setattr(settings, 'JWT_EXPIRATION', seconds)
    response = client.post('/api/auth/login', data={'username': test_user.username, 'password': 'testpassword'})
    assert response.status_code == 200
    data = response.json()
    claims = jwt.get_unverified_claims(data['access_token'])
    assert data['expires_in'] == claims['exp'] - claims['iat'] == seconds


def test_explicit_token_lifetime_remains_supported():
    claims = jwt.get_unverified_claims(create_access_token({'user_id': 1}, expires_delta=timedelta(seconds=120)))
    assert claims['exp'] - claims['iat'] == 120
