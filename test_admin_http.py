import io
from admin_http import AdminWSGI


class API:
    def __init__(self):
        self.approvals = 0

    def dashboard(self, init, **kwargs):
        assert init == "signed"
        return {"ok": True}

    def safety_overview(self, init):
        return {"status": {"open_reports": 0}}

    def file_report(self, init, entity_id, entity_type, reason, subject=""):
        self.last = ("file", entity_id, entity_type, reason)
        return {"report_id": "rpt_1"}

    def review_report(self, init, report_id, status, notes=""):
        self.last = ("review", report_id, status)
        return {"report_id": report_id, "status": status}

    def decide_appeal(self, init, appeal_id, decision, notes=""):
        self.last = ("appeal", appeal_id, decision)
        return {"appeal_id": appeal_id, "status": decision}

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




def test_compliance_routes_are_wired():
    import json
    api = API()
    app = AdminWSGI(api)
    def call(method, path, body=None, key="k"):
        result = {}
        raw = json.dumps(body or {}).encode()
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "HTTP_X_TELEGRAM_INIT_DATA": "signed",
                   "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)),
                   "wsgi.input": io.BytesIO(raw), "HTTP_X_IDEMPOTENCY_KEY": key + path}
        response = app(environ, lambda status, values: result.update(status=status))
        return result["status"], json.loads(response[0])
    status, body = call("GET", "/api/admin/safety")
    assert status == "200 OK" and body["status"]["open_reports"] == 0
    status, _ = call("POST", "/api/admin/reports", {"entity_id": "@x", "entity_type": "channel", "reason": "spam"})
    assert status == "200 OK" and api.last == ("file", "@x", "channel", "spam")
    status, _ = call("POST", "/api/admin/reports/rpt_1/review", {"status": "DISMISSED"})
    assert status == "200 OK" and api.last == ("review", "rpt_1", "DISMISSED")
    status, _ = call("POST", "/api/admin/appeals/apl_1/decide", {"decision": "UPHELD"})
    assert status == "200 OK" and api.last == ("appeal", "apl_1", "UPHELD")


if __name__ == "__main__":
    test_dashboard_transport()
    test_transport_security_controls()
    test_mutation_requires_key_and_replays()
    print("PASS test_dashboard_transport")
    print("PASS test_transport_security_controls")
    print("PASS test_mutation_requires_key_and_replays")
    test_compliance_routes_are_wired()
    print("PASS test_compliance_routes_are_wired")
