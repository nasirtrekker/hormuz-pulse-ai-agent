"""Model Armor layer: managed prompt-injection, sensitive-data and responsible-AI screening.

Two decoupled templates, so each side can be tuned and audited on its own:
  MODEL_ARMOR_INPUT_TEMPLATE   applied to user prompts and to untrusted web research
  MODEL_ARMOR_OUTPUT_TEMPLATE  applied to the final model response

Disabled unless both the location and the template for a side are configured.
MODEL_ARMOR_FAIL_OPEN=true lets requests through when the service itself errors
(default is fail closed).
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def _cfg(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled(template_env: str) -> bool:
    return bool(_cfg("MODEL_ARMOR_LOCATION") and _cfg(template_env)
                and (_cfg("GOOGLE_CLOUD_PROJECT")))


def fail_open() -> bool:
    return _cfg("MODEL_ARMOR_FAIL_OPEN").lower() == "true"


def _get_client():
    global _client
    if _client is None:
        from google.cloud import modelarmor_v1
        location = _cfg("MODEL_ARMOR_LOCATION")
        _client = modelarmor_v1.ModelArmorClient(
            transport="rest",
            client_options={"api_endpoint": f"modelarmor.{location}.rep.googleapis.com"},
        )
    return _client


def _template_path(template_env: str) -> str:
    return (f"projects/{_cfg('GOOGLE_CLOUD_PROJECT')}/locations/"
            f"{_cfg('MODEL_ARMOR_LOCATION')}/templates/{_cfg(template_env)}")


def _matched_filters(result) -> list[str]:
    names = []
    for name, fr in result.filter_results.items():
        # Each entry wraps exactly one filter-specific result with its own match state.
        for field in ("rai_filter_result", "sdp_filter_result", "pi_and_jailbreak_filter_result",
                      "malicious_uri_filter_result", "csam_filter_filter_result",
                      "virus_scan_filter_result"):
            sub = getattr(fr, field, None)
            if sub is not None and field == "sdp_filter_result":
                sub = getattr(sub, "inspect_result", None)
            if sub is not None and getattr(getattr(sub, "match_state", None), "name", "") == "MATCH_FOUND":
                names.append(name)
                break
    return names


def screen(kind: str, text: str, user_prompt: str = "") -> Optional[str]:
    """Return a block reason, or None if the text may pass.

    kind is "input" or "output". Returns None when the side is not configured.
    """
    from google.cloud import modelarmor_v1 as ma

    template_env = "MODEL_ARMOR_INPUT_TEMPLATE" if kind == "input" else "MODEL_ARMOR_OUTPUT_TEMPLATE"
    if not enabled(template_env) or not text.strip():
        return None
    try:
        client = _get_client()
        name = _template_path(template_env)
        if kind == "input":
            response = client.sanitize_user_prompt(request=ma.SanitizeUserPromptRequest(
                name=name, user_prompt_data=ma.DataItem(text=text)))
        else:
            response = client.sanitize_model_response(request=ma.SanitizeModelResponseRequest(
                name=name, model_response_data=ma.DataItem(text=text), user_prompt=user_prompt))
    except Exception as exc:  # network, IAM, quota, bad template name
        logger.error("Model Armor %s check failed: %s", kind, type(exc).__name__)
        return None if fail_open() else "the safety service is unavailable"

    result = response.sanitization_result
    if result.filter_match_state == ma.FilterMatchState.MATCH_FOUND:
        matched = ", ".join(_matched_filters(result)) or "a safety filter"
        logger.warning("Model Armor %s blocked: %s", kind, matched)
        return f"Model Armor flagged it ({matched})"
    return None
