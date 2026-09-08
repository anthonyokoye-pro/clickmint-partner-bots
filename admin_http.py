"""Minimal framework-neutral HTTP transport for the authenticated Admin API."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import mimetypes
from pathlib import Path
from collections import defaultdict, deque
from urllib.parse import parse_qs, unquote

from admin_api import AdminAuthorizationError


class AdminWSGI:
    def __init__(self, api, *, allowed_origins: set[str] | None = None,
                 max_body_bytes: int = 32768, requests_per_minute: int = 60,
                 idempotency_store=None):
        self.api = api
        self.allowed_origins = allowed_origins
        self.max_body_bytes = max(1024, int(max_body_bytes))
        self.requests_per_minute = max(1, int(requests_per_minute))
        self._rate_window = defaultdict(deque)
        self._idempotency = {}
        self.idempotency_store = idempotency_store
        self.idempotency_ttl = 3600
        self.idempotency_max_entries = 10000
        self._metrics = {"requests": 0, "responses": {}, "paths": {}}

    def metrics(self) -> dict:
        return {"requests": self._metrics["requests"],
                "responses": dict(self._metrics["responses"]),
                "paths": dict(self._metrics["paths"])}

    def _prune_idempotency(self):
        now = time.time()
        expired = [key for key, value in self._idempotency.items()
                   if now - value[0] >= self.idempotency_ttl]
        for key in expired:
            self._idempotency.pop(key, None)
        if len(self._idempotency) > self.idempotency_max_entries:
            excess = len(self._idempotency) - self.idempotency_max_entries
            for key, _ in sorted(self._idempotency.items(), key=lambda item: item[1][0])[:excess]:
                self._idempotency.pop(key, None)

    def _cached_mutation(self, start_response, correlation_id: str, key: str):
        self._prune_idempotency()
        fingerprint = hashlib.sha256((self._current_idem_scope + key).encode()).hexdigest()
        cached = self.idempotency_store.get(fingerprint, ttl=self.idempotency_ttl) if self.idempotency_store else self._idempotency.get(fingerprint)
        if cached is None:
            return None
        return self._response(start_response, cached[0] if self.idempotency_store else cached[1], cached[1] if self.idempotency_store else cached[2], correlation_id)

    def _mutation_result(self, start_response, status: str, payload: dict,
                         correlation_id: str, key: str):
        self._prune_idempotency()
        fingerprint = hashlib.sha256((self._current_idem_scope + key).encode()).hexdigest()
        if self.idempotency_store:
            self.idempotency_store.put(fingerprint, status, payload)
        else:
            self._idempotency[fingerprint] = (time.time(), status, payload)
        if status.startswith("2") and hasattr(self.api, "ledger") and self.api.ledger is not None:
            try:
                identity = self.api.authenticate(self._current_init_data)
                if hasattr(self.api.ledger, "tx"):
                    self.api.ledger.tx.add_audit_event(
                        actor_type="admin", actor_id=str(identity.user_id),
                        action="ADMIN_API_MUTATION", object_type="admin_request",
                        object_id=correlation_id,
                        reason=f"path={self._current_path}; idempotency={key}",
                    )
            except Exception:
                pass
        return self._response(start_response, status, payload, correlation_id)

    def _response(self, start_response, status: str, payload: dict, correlation_id: str):
        code = status.split(" ", 1)[0]
        self._metrics["responses"][code] = self._metrics["responses"].get(code, 0) + 1
        path = getattr(self, "_current_path", "unknown")
        self._metrics["paths"][path] = self._metrics["paths"].get(path, 0) + 1
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        start_response(status, [("Content-Type", "application/json"),
                                ("Content-Length", str(len(body))),
                                ("X-Correlation-ID", correlation_id)])
        return [body]

    def _security_audit(self, action: str, reason: str, correlation_id: str):
        ledger = getattr(self.api, "ledger", None)
        if ledger is not None and hasattr(ledger, "tx"):
            try:
                ledger.tx.add_audit_event(
                    actor_type="admin_api", actor_id="unknown",
                    action=action, object_type="admin_security",
                    object_id=correlation_id, reason=reason,
                )
            except Exception:
                pass

    def _allowed(self, environ) -> bool:
        if self.allowed_origins is None:
            return True
        origin = environ.get("HTTP_ORIGIN", "")
        if not origin:
            # Same-origin GET/fetch requests and Telegram WebViews may omit
            # Origin. Reconstruct the public origin from the proxy headers
            # instead of treating a missing header as an allowed origin.
            scheme = environ.get("HTTP_X_FORWARDED_PROTO", "http").split(",", 1)[0].strip()
            host = environ.get("HTTP_HOST", "")
            origin = f"{scheme}://{host}" if host else ""
        if origin not in self.allowed_origins:
            # Keep the response deliberately generic, but expose the rejected
            # origin to the local server operator for deployment diagnosis.
            print(f"Admin origin rejected: {origin!r}", flush=True)
            return False
        return True

    def _rate_allowed(self, environ) -> bool:
        key = environ.get("REMOTE_ADDR", "unknown")
        now = time.time()
        window = self._rate_window[key]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.requests_per_minute:
            return False
        window.append(now)
        return True

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "")
        self._current_path = path
        self._metrics["requests"] += 1
        init_data = environ.get("HTTP_X_TELEGRAM_INIT_DATA", "")
        self._current_init_data = init_data
        correlation_id = environ.get("HTTP_X_CORRELATION_ID") or secrets.token_hex(8)
        if method == "GET" and path.startswith("/admin_web/"):
            relative = path[len("/admin_web/"):] or "index.html"
            candidate = (Path(__file__).parent / "admin_web" / relative).resolve()
            root = (Path(__file__).parent / "admin_web").resolve()
            if candidate.is_file() and str(candidate).startswith(str(root)):
                body = candidate.read_bytes()
                start_response("200 OK", [("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"), ("Content-Length", str(len(body)))])
                return [body]
            start_response("404 Not Found", [("Content-Type", "application/json")])
            return [b'{"error":"not found"}']
        try:
            if not self._allowed(environ):
                self._security_audit("ADMIN_ORIGIN_REJECTED", "origin not allowed", correlation_id)
                return self._response(start_response, "403 Forbidden", {"error": "origin not allowed"}, correlation_id)
            if not self._rate_allowed(environ):
                self._security_audit("ADMIN_RATE_LIMITED", "request rate exceeded", correlation_id)
                return self._response(start_response, "429 Too Many Requests", {"error": "rate limit exceeded"}, correlation_id)
            if method == "GET" and path == "/api/admin/dashboard":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                task_limit = int(query.get("task_limit", [20])[0])
                task_category = query.get("task_category", [None])[0]
                broadcast_status = query.get("broadcast_status", [None])[0]
                payload = self.api.dashboard(
                    init_data, task_limit=task_limit,
                    task_category=task_category,
                    broadcast_status=broadcast_status,
                )
                return self._response(start_response, "200 OK", payload, correlation_id)
            if method == "GET" and path == "/api/admin/transport/metrics":
                self.api.authenticate(init_data)
                return self._response(start_response, "200 OK", self.metrics(), correlation_id)
            if method == "GET" and path == "/api/admin/audit":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                payload = {"events": self.api.audit_events(
                    init_data,
                    object_type=query.get("object_type", [None])[0],
                    limit=int(query.get("limit", [50])[0]),
                )}
                return self._response(start_response, "200 OK", payload, correlation_id)
            if method == "GET" and path.startswith("/api/admin/referrals/"):
                period = unquote(path.split("/api/admin/referrals/", 1)[1])
                return self._response(start_response, "200 OK", self.api.referral_snapshot(init_data, period), correlation_id)
            if method == "GET" and path.startswith("/api/admin/enforcement/"):
                parts = [unquote(item) for item in path.strip("/").split("/")]
                if len(parts) != 5:
                    return self._response(start_response, "404 Not Found", {"error": "not found"}, correlation_id)
                payload = self.api.enforcement_details(init_data, parts[4], parts[3])
                return self._response(start_response, "200 OK", payload, correlation_id)
            if method == "GET" and path.startswith("/api/admin/performance/"):
                destination_id = unquote(path.split("/api/admin/performance/", 1)[1])
                query = parse_qs(environ.get("QUERY_STRING", ""))
                payload = self.api.performance_history(
                    init_data, destination_id,
                    limit=int(query.get("limit", [20])[0]),
                )
                return self._response(start_response, "200 OK", payload, correlation_id)
            if method == "POST":
                idempotency_key = environ.get("HTTP_X_IDEMPOTENCY_KEY", "").strip()
                if not idempotency_key or len(idempotency_key) > 128:
                    self._security_audit("ADMIN_IDEMPOTENCY_REJECTED", "missing or malformed idempotency key", correlation_id)
                    return self._response(start_response, "400 Bad Request", {"error": "X-Idempotency-Key required"}, correlation_id)
                self._current_idem_scope = path + hashlib.sha256(init_data.encode()).hexdigest()
                if environ.get("CONTENT_TYPE", "").split(";", 1)[0].lower() != "application/json":
                    return self._response(start_response, "415 Unsupported Media Type", {"error": "application/json required"}, correlation_id)
                body_length = int(environ.get("CONTENT_LENGTH") or 0)
                if body_length > self.max_body_bytes:
                    return self._response(start_response, "413 Request Entity Too Large", {"error": "request too large"}, correlation_id)
                raw = environ.get("wsgi.input").read(body_length) if body_length else b"{}"
                body = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                replay = self._cached_mutation(start_response, correlation_id, idempotency_key)
                if replay is not None:
                    return replay
                parts = [unquote(item) for item in path.strip("/").split("/")]
                if parts[:4] == ["api", "admin", "safety", "emergency"] and len(parts) == 4:
                    result = self.api.emergency_mode(init_data, bool(body.get("enabled")), body.get("reason", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "enforcement"] and len(parts) == 6:
                    result = self.api.enforce_entity(
                        init_data, parts[4], parts[3], parts[5], body.get("reason", ""),
                        body.get("duration_seconds"), body.get("notes", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "referrals"] and len(parts) == 5 and parts[4] == "approve":
                    result = self.api.approve_referral_ranking(init_data, parts[3], body.get("reason", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "broadcasts"] and len(parts) == 3:
                    result = self.api.create_reward_campaign(init_data, body.get("text", ""), body.get("scheduled_at"), body.get("title", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "broadcasts"] and len(parts) == 5 and parts[4] == "edit":
                    result = self.api.update_reward_draft(init_data, parts[3], body.get("title", ""), body.get("text", ""), body.get("scheduled_at"))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "broadcasts"] and len(parts) == 5 and parts[4] == "delete":
                    result = self.api.delete_reward_draft(init_data, parts[3])
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "broadcasts"] and len(parts) == 5 and parts[4] == "queue":
                    result = self.api.queue_reward_campaign(init_data, parts[3])
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "broadcasts"] and len(parts) == 5:
                    result = self.api.broadcast_action(init_data, parts[3], parts[4], body.get("reason", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
                if parts[:3] == ["api", "admin", "performance"] and len(parts) == 5 and parts[4] == "clear":
                    result = self.api.clear_performance_cooldown(init_data, parts[3], body.get("reason", ""))
                    return self._mutation_result(start_response, "200 OK", result, correlation_id, idempotency_key)
            return self._response(start_response, "404 Not Found", {"error": "not found"}, correlation_id)
        except AdminAuthorizationError as exc:
            self._security_audit("ADMIN_AUTH_REJECTED", str(exc), correlation_id)
            return self._response(start_response, "403 Forbidden", {"error": str(exc)}, correlation_id)
        except (ValueError, KeyError) as exc:
            return self._response(start_response, "400 Bad Request", {"error": str(exc)}, correlation_id)
        except Exception:
            return self._response(start_response, "500 Internal Server Error", {"error": "internal error"}, correlation_id)
