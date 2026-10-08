"""HTTP deployment boundary regressions; no external provider calls."""
from fastapi.testclient import TestClient
from interview import server


def test_cors_uses_the_configured_origins():
    with TestClient(server.app) as client:
        origin = server._SETTINGS.allowed_origins[0]
        allowed = client.options('/api/guest', headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'POST',
        })
        assert allowed.status_code == 200
        assert allowed.headers['access-control-allow-origin'] == origin
        refused = client.options('/api/guest', headers={
            'Origin': 'https://unlisted.example',
            'Access-Control-Request-Method': 'POST',
        })
        assert refused.status_code == 400
        assert 'access-control-allow-origin' not in refused.headers
