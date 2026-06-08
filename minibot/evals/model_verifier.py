"""Model-verifier interface with fake and OpenAI-compatible real modes."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from minibot.harness.model_client import _parse_dotenv


class ModelVerifier:
    """Provide a second verification channel beyond rules."""

    def __init__(
        self,
        *,
        mode: str = "fake",
        provider: str = "fake",
        base_url: str = "",
        api_key: str = "",
        model_name: str = "",
        config_source: str = "dedicated",
        startup_error: str | None = None,
    ) -> None:
        self.mode = mode
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.config_source = config_source
        self.startup_error = startup_error

    @classmethod
    def from_project_root(cls, project_root: Path) -> ModelVerifier:
        merged = cls._load_env(project_root)
        mode = (merged.get("MINIBOT_VERIFIER_MODE") or "fake").strip().lower() or "fake"
        if mode == "fake":
            return cls(mode="fake", provider="fake", model_name="fake", config_source="dedicated")

        dedicated = {
            "provider": (merged.get("MINIBOT_VERIFIER_PROVIDER") or "deepseek").strip() or "deepseek",
            "base_url": (merged.get("MINIBOT_VERIFIER_BASE_URL") or "").strip(),
            "api_key": (merged.get("MINIBOT_VERIFIER_API_KEY") or "").strip(),
            "model_name": (merged.get("MINIBOT_VERIFIER_MODEL_NAME") or "").strip(),
        }
        if all(dedicated.values()):
            return cls(
                mode="real",
                provider=dedicated["provider"],
                base_url=dedicated["base_url"],
                api_key=dedicated["api_key"],
                model_name=dedicated["model_name"],
                config_source="dedicated",
            )

        model_fallback = {
            "provider": (merged.get("MINIBOT_MODEL_PROVIDER") or "deepseek").strip() or "deepseek",
            "base_url": (merged.get("MINIBOT_MODEL_BASE_URL") or merged.get("MINIBOT_BASE_URL") or "").strip(),
            "api_key": (merged.get("MINIBOT_MODEL_API_KEY") or merged.get("MINIBOT_API_KEY") or "").strip(),
            "model_name": (merged.get("MINIBOT_MODEL_NAME") or "").strip(),
        }
        if all(model_fallback.values()):
            return cls(
                mode="real",
                provider=model_fallback["provider"],
                base_url=model_fallback["base_url"],
                api_key=model_fallback["api_key"],
                model_name=model_fallback["model_name"],
                config_source="model_config",
            )

        return cls(
            mode="real",
            provider=dedicated["provider"] or model_fallback["provider"],
            base_url=dedicated["base_url"] or model_fallback["base_url"],
            api_key="",
            model_name=dedicated["model_name"] or model_fallback["model_name"],
            config_source="dedicated" if any(dedicated.values()) else "model_config",
            startup_error="verifier_config_missing",
        )

    def describe(self) -> dict[str, object]:
        return {
            "verifier_mode": self.mode,
            "fake_verifier": self.mode == "fake",
            "verifier_provider": self.provider,
            "verifier_model_name": self.model_name,
            "verifier_config_source": self.config_source,
            "verifier_error": self.startup_error,
        }

    def verify(
        self,
        *,
        final_response: str,
        expected_behavior: list[str],
        run_record: dict[str, object],
    ) -> dict[str, object]:
        """Return a model-verifier style verdict."""

        if self.mode == "fake":
            passed = bool(final_response.strip())
            if any(str(item).startswith("final_response_contains:") for item in expected_behavior):
                passed = passed and any(
                    token.split(":", 1)[1] in final_response
                    for token in expected_behavior
                    if token.startswith("final_response_contains:")
                )
            return {
                "used_model": False,
                "passed": passed,
                "reason": "fake model verifier accepted structured response" if passed else "fake model verifier rejected response",
                "failure_category": None,
                "confidence": 0.2,
                "verifier_mode": "fake",
                "fake_verifier": True,
                "verifier_config_source": self.config_source,
            }

        if self.startup_error:
            return {
                "used_model": False,
                "passed": True,
                "reason": self.startup_error,
                "failure_category": self.startup_error,
                "confidence": 0.0,
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_config_source": self.config_source,
            }

        payload = self._build_payload(final_response=final_response, expected_behavior=expected_behavior, run_record=run_record)
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return {
                "used_model": True,
                "passed": True,
                "reason": "verifier_http_error",
                "failure_category": "verifier_http_error",
                "confidence": 0.0,
                "status_code": exc.code,
                "response_body_snippet": self._sanitize_text(body[:500]),
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_config_source": self.config_source,
            }
        except urllib.error.URLError as exc:
            return {
                "used_model": True,
                "passed": True,
                "reason": f"verifier_http_error: {exc.reason}",
                "failure_category": "verifier_http_error",
                "confidence": 0.0,
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_config_source": self.config_source,
            }

        content = response_payload["choices"][0]["message"].get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {
                "used_model": True,
                "passed": True,
                "reason": "verifier_parse_error",
                "failure_category": "verifier_parse_error",
                "confidence": 0.0,
                "raw_model_output": self._sanitize_text(content[:1000]),
                "verifier_mode": "real",
                "fake_verifier": False,
                "verifier_config_source": self.config_source,
            }

        return {
            "used_model": True,
            "passed": bool(parsed.get("passed")),
            "reason": str(parsed.get("verifier_reason") or ""),
            "failure_category": parsed.get("failure_category"),
            "confidence": float(parsed.get("confidence", 0.0) or 0.0),
            "raw_model_output": self._sanitize_text(content[:1000]),
            "verifier_mode": "real",
            "fake_verifier": False,
            "verifier_config_source": self.config_source,
        }

    def _build_payload(
        self,
        *,
        final_response: str,
        expected_behavior: list[str],
        run_record: dict[str, object],
    ) -> dict[str, object]:
        user_goal = str(run_record.get("user_input") or run_record.get("input") or "")
        system_prompt = (
            "You are a strict benchmark verifier. You must output valid JSON. "
            'Return exactly this schema: {"passed": true, "verifier_reason": "...", "failure_category": null, "confidence": 0.9}. '
            "Use failure_category only when the benchmark output clearly violates the expected assertions."
        )
        user_payload = {
            "user_goal": user_goal,
            "final_response": final_response,
            "tool_calls": run_record.get("tool_calls", []),
            "tool_results": run_record.get("tool_results", []),
            "expected_assertions": expected_behavior,
        }
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "stream": False,
        }

    def _sanitize_text(self, value: str) -> str:
        return value.replace(self.api_key, "***") if self.api_key else value

    @staticmethod
    def _load_env(project_root: Path) -> dict[str, str]:
        settings = _parse_dotenv(project_root / ".env")
        settings.update({key: value for key, value in os.environ.items() if key.startswith(("MINIBOT_", "TAVILY_", "FEISHU_", "LARK_"))})
        return settings
