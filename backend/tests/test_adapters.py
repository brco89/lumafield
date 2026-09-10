import json

import httpx
import pytest
from pathlib import Path

from app.adapters.airtable import (
    AirtableGateway,
    ExternalDefiniteFailure,
    ExternalIntegrityError,
    ExternalOutcomeUnknown,
)
from app.adapters.vision import (GoogleVision, OpenAIVision, OpenRouterVision, PROMPT_VERSION,
                                 build_visual_request, build_vision)
from app.config import Settings
from app.models import DomainError


def action_payload():
    return {
        "action_type": "CREATE_REPLACEMENT",
        "inspection_id": "ins_123",
        "fixture_id": "F-18427-01",
        "pole_id": "P-18427",
        "contract_id": "IP-2026-014",
        "decision_id": "dec_123",
        "payload_hash": "hash_123",
        "effect_summary": "Create a replacement request.",
    }


def test_airtable_dispatch_returns_provider_reference_and_scopes_payload():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"id": "rec_created"}, request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    reference = gateway.dispatch("op_123", action_payload())

    assert reference == {
        "external_system": "Airtable",
        "record_id": "rec_created",
        "display_reference": "rec_created",
        "status": "REQUESTED",
    }
    assert "/v0/app_base/LumaField%20Actions" in seen["url"]
    assert seen["headers"]["authorization"] == "Bearer token"
    assert '"External Operation ID":"op_123"' in seen["json"]
    assert '"Payload Hash":"hash_123"' in seen["json"]


@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
def test_airtable_dispatch_treats_transport_like_responses_as_unknown(status_code):
    def handler(request):
        return httpx.Response(status_code, request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    with pytest.raises(ExternalOutcomeUnknown):
        gateway.dispatch("op_123", action_payload())


def test_airtable_dispatch_keeps_definite_rejection_distinct():
    def handler(request):
        return httpx.Response(422, request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    with pytest.raises(ExternalDefiniteFailure) as error:
        gateway.dispatch("op_123", action_payload())
    assert error.value.code == "AIRTABLE_HTTP_422"


def test_airtable_lookup_reconciles_one_exact_match_and_zero_matches():
    responses = iter([
        {"records": [{"id": "rec_existing", "fields": {"Status": "OPEN"}}]},
        {"records": []},
    ])

    def handler(request):
        return httpx.Response(200, json=next(responses), request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    assert gateway.lookup("op_123")["record_id"] == "rec_existing"
    assert gateway.lookup("op_missing") is None


def test_airtable_lookup_rejects_multiple_exact_matches():
    def handler(request):
        return httpx.Response(200, json={"records": [
            {"id": "rec_a", "fields": {}},
            {"id": "rec_b", "fields": {}},
        ]}, request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    with pytest.raises(ExternalIntegrityError):
        gateway.lookup("op_123")


def test_airtable_lookup_treats_retryable_provider_response_as_unknown():
    def handler(request):
        return httpx.Response(425, request=request)

    gateway = AirtableGateway("token", "app_base", "LumaField Actions", httpx.MockTransport(handler))
    with pytest.raises(ExternalOutcomeUnknown):
        gateway.lookup("op_123")


VISUAL_JSON = {
    "visual_classification": "LEGACY", "visual_confidence": "HIGH", "evidence_quality": "GOOD",
    "evidence_origin": "FIELD_PLAUSIBLE", "color_temperature_cue": "WARM_AMBER",
    "visible_identifiers": [],
    "visual_cues": ["HPS lamp visible inside the housing"], "limitations": [],
    "retake_guidance": None, "observed_power_w": None, "power_evidence": None,
}


def vision_response(payload=VISUAL_JSON, status="completed"):
    import json as jsonlib
    return {
        "status": status,
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": jsonlib.dumps(payload)},
        ]}],
    }


def test_medium_confidence_limited_field_photo_is_sufficient_for_demo_flow():
    from app.models import VisualResult

    result = VisualResult(
        visual_classification="LEGACY",
        visual_confidence="MEDIUM",
        evidence_quality="LIMITED",
        evidence_origin="FIELD_PLAUSIBLE",
        color_temperature_cue="WARM_AMBER",
        visible_identifiers=[],
        visual_cues=["Amber light combined with a deep reflector"],
        limitations=["Luminaire photographed from a distance"],
        retake_guidance=None,
        observed_power_w=None,
        power_evidence=None,
    )

    assert result.sufficient is True


def test_openai_vision_request_is_blinded_and_strictly_typed():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json=vision_response(), request=request)

    gateway = OpenAIVision("key", "gpt-5.5", httpx.MockTransport(handler))
    result = gateway.assess(b"normalized-pixels")

    assert result.visual_classification == "LEGACY"
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["headers"]["authorization"] == "Bearer key"
    assert '"model":"gpt-5.5"' in seen["json"]
    assert '"data:image/jpeg;base64,bm9ybWFsaXplZC1waXhlbHM="' in seen["json"]
    assert '"strict":true' in seen["json"]
    assert f'"name":"{PROMPT_VERSION}"' in seen["json"]
    # The blinded boundary: no business context may ride along with the pixels.
    request = build_visual_request(b"normalized-pixels", "gpt-5.5")
    serialized = str(request)
    assert "P-18427" not in serialized and "operator_utterance" not in serialized


def test_openai_vision_fails_closed_on_transport_errors():
    for body in [
        lambda: httpx.Response(500, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
        lambda: httpx.Response(200, json=vision_response(status="incomplete"), request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
        lambda: httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]},
        ]}, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
        lambda: httpx.Response(200, json={"status": "completed", "output": []},
                               request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
    ]:
        gateway = OpenAIVision("key", "gpt-5.5", httpx.MockTransport(lambda request: body()))
        with pytest.raises(DomainError) as error:
            gateway.assess(b"normalized-pixels")
        assert error.value.code == "VISION_UNAVAILABLE" and error.value.retryable


def test_openai_vision_requires_credentials():
    gateway = OpenAIVision("", "gpt-5.5")
    with pytest.raises(DomainError) as error:
        gateway.assess(b"normalized-pixels")
    assert error.value.code == "VISION_NOT_CONFIGURED" and not error.value.retryable


def google_vision_response(payload=VISUAL_JSON):
    import json as jsonlib
    return {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": jsonlib.dumps(payload)}]},
        }],
    }


def test_google_vision_request_and_parse():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json=google_vision_response(), request=request)

    gateway = GoogleVision("gkey", "gemini-2.5-pro", httpx.MockTransport(handler))
    result = gateway.assess(b"normalized-pixels")

    assert result.visual_classification == "LEGACY"
    assert seen["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro")
    assert seen["headers"]["x-goog-api-key"] == "gkey"
    assert '"data:image/jpeg;base64' not in seen["json"]  # inlineData, not a data URI
    assert '"inlineData"' in seen["json"] and "responseJsonSchema" in seen["json"]


def test_google_vision_fails_closed_on_incomplete_or_blocked():
    for body in [
        {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
        {"candidates": []},
    ]:
        gateway = GoogleVision("gkey", "gemini-2.5-pro",
                               httpx.MockTransport(lambda request: httpx.Response(200, json=body, request=request)))
        with pytest.raises(DomainError) as error:
            gateway.assess(b"normalized-pixels")
        assert error.value.code == "VISION_UNAVAILABLE" and error.value.retryable


def test_openrouter_vision_request_and_parse():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(VISUAL_JSON)}}],
        }, request=request)

    gateway = OpenRouterVision("orkey", "openai/gpt-5.5-mini", httpx.MockTransport(handler))
    result = gateway.assess(b"normalized-pixels")

    assert result.visual_classification == "LEGACY"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer orkey"
    assert '"model":"openai/gpt-5.5-mini"' in seen["json"]
    assert '"data:image/jpeg;base64,' in seen["json"]
    assert '"json_schema"' in seen["json"]


def test_openrouter_vision_fails_closed_on_refusal_and_error_body():
    for body in [
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "no", "content": None}}]},
        {"error": {"message": "insufficient credits"}},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    ]:
        gateway = OpenRouterVision("orkey", "openai/gpt-5.5-mini",
                                   httpx.MockTransport(lambda request: httpx.Response(200, json=body, request=request)))
        with pytest.raises(DomainError) as error:
            gateway.assess(b"normalized-pixels")
        assert error.value.code == "VISION_UNAVAILABLE" and error.value.retryable


def test_build_vision_selects_provider_and_rejects_unknown():
    def settings(**overrides):
        return Settings(data_dir=Path("."), **overrides)

    assert isinstance(build_vision(settings(vision_provider="google", google_api_key="gkey")), GoogleVision)
    assert isinstance(build_vision(settings(vision_provider="openai", vision_api_key="okey")), OpenAIVision)
    assert isinstance(build_vision(settings(vision_provider="openrouter", openrouter_api_key="rkey")), OpenRouterVision)
    with pytest.raises(DomainError):
        build_vision(settings(vision_provider="azure"))
