"""HTTP channel implemented with the Python standard library."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from .base import BaseChannel, ChannelMessage

_APPROVE_PATH_RE = re.compile(r"^/approvals/([^/]+)/approve$")
_REJECT_PATH_RE = re.compile(r"^/approvals/([^/]+)/reject$")


@dataclass(slots=True)
class HttpErrorResponse(Exception):
    """Structured HTTP error used to keep handler code branch-based and quiet."""

    status_code: int
    error: str


class HttpChannel(BaseChannel):
    """Expose a minimal HTTP interface backed by the shared AgentLoop."""

    channel_name = "http"

    def __init__(
        self,
        agent_loop,
        status_service=None,
        benchmark_runner=None,
        approval_store=None,
        auth_token: str | None = None,
    ) -> None:
        super().__init__(agent_loop)
        self.status_service = status_service
        self.benchmark_runner = benchmark_runner
        self.approval_store = approval_store
        self.auth_token = auth_token.strip() if auth_token else None

    def create_server(self, host: str, port: int) -> ThreadingHTTPServer:
        """Create the HTTP server so tests can run it on an ephemeral port."""

        channel = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._discard_body()
                if self.path == "/status":
                    self._send_json(channel._handle_status())
                    return
                if self.path == "/approvals":
                    auth_error = channel._check_auth(self.headers)
                    if auth_error is not None:
                        status, payload = auth_error
                        self._send_json(payload, status=status)
                        return
                    self._send_json(channel._handle_approvals_list())
                    return
                self._send_json({"error": "not_found"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                try:
                    if self.path == "/chat":
                        self._send_json(
                            channel._handle_chat(self._read_json(), request_path=self.path)
                        )
                        return
                    if self.path == "/benchmark/run":
                        try:
                            self._send_json(channel._handle_benchmark_run(self._read_json()))
                        except Exception as exc:  # noqa: BLE001
                            self._send_json(
                                {
                                    "status": "error",
                                    "error": str(exc),
                                    "failure_category": "benchmark_error",
                                },
                                status=500,
                            )
                        return
                    approve_match = _APPROVE_PATH_RE.match(self.path)
                    if approve_match:
                        auth_error = channel._check_auth(self.headers)
                        if auth_error is not None:
                            status, payload = auth_error
                            self._send_json(payload, status=status)
                            return
                        self._send_json(channel._handle_approval_approve(approve_match.group(1)))
                        return
                    reject_match = _REJECT_PATH_RE.match(self.path)
                    if reject_match:
                        auth_error = channel._check_auth(self.headers)
                        if auth_error is not None:
                            status, payload = auth_error
                            self._send_json(payload, status=status)
                            return
                        self._send_json(channel._handle_approval_reject(reject_match.group(1)))
                        return
                    self._send_json({"error": "not_found"}, status=404)
                except HttpErrorResponse as exc:
                    self._send_json({"error": exc.error}, status=exc.status_code)

            def do_PUT(self) -> None:  # noqa: N802
                self._discard_body()
                self._send_json({"error": "method_not_allowed"}, status=405)

            def do_DELETE(self) -> None:  # noqa: N802
                self._discard_body()
                self._send_json({"error": "method_not_allowed"}, status=405)

            def do_PATCH(self) -> None:  # noqa: N802
                self._discard_body()
                self._send_json({"error": "method_not_allowed"}, status=405)

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _read_json(self) -> dict[str, object]:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise HttpErrorResponse(status_code=400, error="invalid_json") from exc

            def _discard_body(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length > 0:
                    self.rfile.read(content_length)

            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer((host, port), Handler)

    def run(self, host: str, port: int) -> None:
        """Start the HTTP server."""

        server = self.create_server(host, port)
        bound_host, bound_port = server.server_address
        print(f"MiniBot HTTP listening on http://{bound_host}:{bound_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    def _handle_status(self) -> dict[str, object]:
        """Return the structured status response."""

        if self.status_service is None:
            return {"status": "ok", "channel": self.channel_name}
        return json.loads(self.status_service.collect().to_json())

    def _handle_chat(self, payload: dict[str, object], request_path: str) -> dict[str, object]:
        """Convert the HTTP request into `ChannelMessage` and route it to AgentLoop."""

        content = self._extract_message_content(payload)
        if content is None:
            raise HttpErrorResponse(status_code=400, error="missing_message")
        result = self.dispatch_message(
            ChannelMessage(
                channel=self.channel_name,
                user_id=str(payload.get("user_id", "http-user")),
                session_id=str(payload.get("session_id", str(uuid4()))),
                content=content,
                metadata={"request_path": request_path},
            )
        )
        return {"response": result.response, "run_id": result.run_id, "channel": self.channel_name}

    def _handle_benchmark_run(self, payload: dict[str, object]) -> dict[str, object]:
        """Run benchmark via the current runner or return a structured not-ready response."""

        if self.benchmark_runner is None:
            return {
                "status": "not_ready",
                "reason": "benchmark runner not wired into HTTP channel yet",
                "report": None,
            }
        report = self.benchmark_runner.run(
            category=str(payload["category"]) if payload.get("category") else None,
        )
        return {"status": "ok", "reason": None, "report": report}

    # ---- approval endpoints ----

    def _handle_approvals_list(self) -> dict[str, object]:
        """GET /approvals — list pending approvals."""
        if self.approval_store is None:
            return {"approvals": [], "error": "approval_store_not_available"}
        try:
            pending = self.approval_store.list_pending()
        except Exception as exc:
            return {"approvals": [], "error": str(exc)}
        approvals = []
        for item in pending:
            approvals.append(
                {
                    "approval_id": str(item.get("approval_id", "")),
                    "tool_name": str(item.get("tool_name", "")),
                    "arguments": dict(item.get("arguments", {})),
                    "risk_level": str(item.get("risk_level", "")),
                    "status": str(item.get("status", "pending")),
                    "created_at": str(item.get("created_at", "")),
                }
            )
        return {"approvals": approvals}

    def _handle_approval_approve(self, approval_id: str) -> dict[str, object]:
        """POST /approvals/{id}/approve — approve a pending request."""
        if self.approval_store is None:
            raise HttpErrorResponse(status_code=503, error="approval_store_not_available")
        try:
            record = self.approval_store.approve(approval_id)
        except Exception as exc:
            raise HttpErrorResponse(status_code=404, error=str(exc)) from exc
        return {
            "approval_id": str(record.get("approval_id", "")),
            "status": str(record.get("status", "")),
        }

    def _handle_approval_reject(self, approval_id: str) -> dict[str, object]:
        """POST /approvals/{id}/reject — reject a pending request."""
        if self.approval_store is None:
            raise HttpErrorResponse(status_code=503, error="approval_store_not_available")
        try:
            record = self.approval_store.reject(approval_id)
        except Exception as exc:
            raise HttpErrorResponse(status_code=404, error=str(exc)) from exc
        return {
            "approval_id": str(record.get("approval_id", "")),
            "status": str(record.get("status", "")),
        }

    # ---- auth helpers ----

    def _check_auth(self, headers) -> tuple[int, dict[str, object]] | None:
        """Return ``(status, error_payload)`` when auth fails, or ``None`` when ok.

        When ``auth_token`` is not configured auth is skipped entirely
        (backward-compatible local-dev behaviour).
        """
        if not self.auth_token:
            return None
        auth_header = headers.get("Authorization", "").strip()
        if not auth_header:
            return (401, {"error": "unauthorized"})
        expected = f"Bearer {self.auth_token}"
        if auth_header != expected:
            return (403, {"error": "forbidden"})
        return None

    # ---- message helpers ----

    @staticmethod
    def _extract_message_content(payload: dict[str, object]) -> str | None:
        """Resolve message payload field priority: message > content > text."""

        for key in ("message", "content", "text"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None
