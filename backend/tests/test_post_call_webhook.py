import hashlib
import hmac
import json
import time


def signed(payload, secret="post-call-secret", timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256
    ).hexdigest()
    return raw, f"t={timestamp},v0={signature}"


def payload(agent_id="agent_demo", summary="Reconciliation completed safely."):
    return {
        "type": "post_call_transcription",
        "event_timestamp": int(time.time()),
        "data": {
            "agent_id": agent_id,
            "agent_name": "LumaField - field asset reconciliation",
            "conversation_id": "conv-001",
            "status": "done",
            "version_id": "version-18",
            "branch_id": "main-branch",
            "transcript": [
                {"role": "agent", "message": "What is the pole number?", "time_in_call_secs": 0},
                {"role": "user", "message": "18427", "time_in_call_secs": 2},
            ],
            "metadata": {"start_time_unix_secs": int(time.time()) - 12, "call_duration_secs": 12},
            "analysis": {
                "call_successful": "success",
                "transcript_summary": summary,
                "evaluation_criteria_results": {
                    "no_unsupported_power_claim": {
                        "criteria_id": "no_unsupported_power_claim",
                        "result": "success",
                        "rationale": "No wattage was invented.",
                    }
                },
            },
        },
    }


def test_post_call_webhook_is_verified_correlated_and_idempotent(harness):
    client, session, _tool_headers, inspection, _image, _vision = harness
    raw, signature = signed(payload())
    first = client.post(
        "/v1/webhooks/elevenlabs/post-call",
        content=raw,
        headers={"ElevenLabs-Signature": signature, "Content-Type": "application/json"},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"status": "received", "conversation_id": "conv-001", "associated": True}

    updated_payload = payload(summary="Summary updated after retry.")
    raw, signature = signed(updated_payload)
    retry = client.post(
        "/v1/webhooks/elevenlabs/post-call",
        content=raw,
        headers={"ElevenLabs-Signature": signature, "Content-Type": "application/json"},
    )
    assert retry.status_code == 200

    dossier = client.get(
        f"/v1/inspections/{inspection['inspection_id']}",
        headers={"Authorization": f"Bearer {session['session_token']}"},
    ).json()
    post_call = dossier["conversation_analysis"]
    assert post_call["conversation_id"] == "conv-001"
    assert post_call["agent_name"] == "LumaField - field asset reconciliation"
    assert post_call["version_id"] == "version-18"
    assert post_call["duration_seconds"] == 12
    assert post_call["analysis"]["call_successful"] == "success"
    assert post_call["analysis"]["transcript_summary"] == "Summary updated after retry."
    assert len([event for event in dossier["events"]
                if event["kind"] == "POST_CALL_ANALYSIS_RECEIVED"]) == 2


def test_post_call_webhook_rejects_invalid_or_expired_signatures(harness):
    client = harness[0]
    raw, _signature = signed(payload())
    invalid = client.post(
        "/v1/webhooks/elevenlabs/post-call",
        content=raw,
        headers={"ElevenLabs-Signature": "t=1,v0=invalid"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_post_call_webhook_rejects_another_agent(harness):
    client = harness[0]
    raw, signature = signed(payload(agent_id="another-agent"))
    response = client.post(
        "/v1/webhooks/elevenlabs/post-call",
        content=raw,
        headers={"ElevenLabs-Signature": signature},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "WRONG_WEBHOOK_AGENT"
