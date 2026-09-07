import io
from admin_http import AdminWSGI


class API:
    def __init__(self):
        self.approvals = 0

    def dashboard(self, init, **kwargs):
        assert init == "signed"
        return {"ok": True}

    def approve_referral_ranking(self, init, period, reason):
        self.approvals += 1
        return {"period": period, "status": "approved"}


def test_dashboard_transport():
    app = AdminWSGI(API())
    result = {}
    def start(status, headers):
        result["status"] = status
    response = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/api/admin/dashboard",
                    "HTTP_X_TELEGRAM_INIT_DATA": "signed", "wsgi.input": io.BytesIO()}, start)
    assert result["status"] == "200 OK"
    assert b'"ok":true' in response[0]


def test_transport_security_controls():
    api = API()
    app = AdminWSGI(api, allowed_origins={"https://admin.example"}, requests_per_minute=1, max_body_bytes=1024)
    result = {}
    def start(status, headers):
        result["status"] = status
    base = {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/admin/dashboard",
            "HTTP_X_TELEGRAM_INIT_DATA": "signed", "REMOTE_ADDR": "127.0.0.1",
            "wsgi.input": io.BytesIO()}
    app({**base, "HTTP_ORIGIN": "https://blocked.example"}, start)
    assert result["status"] == "403 Forbidden"
    app({**base, "HTTP_ORIGIN": "https://admin.example"}, start)
    assert result["status"] == "200 OK"
    app({**base, "HTTP_ORIGIN": "https://admin.example"}, start)
    assert result["status"] == "429 Too Many Requests"


def test_mutation_requires_key_and_replays():
    api = API()
    app = AdminWSGI(api)
    def request(headers):
        result = {}
        body = b'{"reason":"test"}'
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/admin/referrals/2026-09/approve",
                   "HTTP_X_TELEGRAM_INIT_DATA": "signed", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), **headers}
        response = app(environ, lambda status, values: result.update(status=status, headers=values))
        return result, response
    missing, _ = request({})
    assert missing["status"] == "400 Bad Request"
    first, _ = request({"HTTP_X_IDEMPOTENCY_KEY": "same-key"})
    second, _ = request({"HTTP_X_IDEMPOTENCY_KEY": "same-key"})
    assert first["status"] == second["status"] == "200 OK"
    assert api.approvals == 1


if __name__ == "__main__":
    test_dashboard_transport()
    test_transport_security_controls()
    test_mutation_requires_key_and_replays()
    print("PASS test_dashboard_transport")
    print("PASS test_transport_security_controls")
    print("PASS test_mutation_requires_key_and_replays")
