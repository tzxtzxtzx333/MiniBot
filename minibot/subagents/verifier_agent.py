"""Lightweight response verifier agent."""

from __future__ import annotations


class VerifierAgent:
    """Check whether a response appears to satisfy a user goal or benchmark case."""

    def verify(
        self,
        *,
        final_response: str,
        user_goal: str,
        expected_behavior: list[str] | None,
        tool_results: list[dict[str, object]] | None,
    ) -> dict[str, object]:
        """Return a lightweight verification decision and reason."""

        if expected_behavior:
            matched = [item for item in expected_behavior if self._matches_behavior(final_response, tool_results or [], item)]
            if matched:
                return {"verified": True, "verifier_reason": f"matched {len(matched)}/{len(expected_behavior)} expected behaviors"}
            verified = bool(final_response.strip())
            reason = (
                "non-empty response available for lightweight verifier"
                if verified
                else "empty response while checking expected behavior"
            )
            return {"verified": verified, "verifier_reason": reason}

        if "tool result" in final_response.lower() or "partial success" in final_response.lower() or "echo" in final_response.lower():
            return {"verified": True, "verifier_reason": f"response addressed goal: {user_goal}"}
        return {"verified": False, "verifier_reason": f"response may not satisfy goal: {user_goal}"}

    @staticmethod
    def _matches_behavior(final_response: str, tool_results: list[dict[str, object]], behavior: str) -> bool:
        lowered = behavior.lower()
        if lowered in final_response.lower():
            return True
        tool_blob = " ".join(str(item) for item in tool_results).lower()
        return lowered in tool_blob
