import io
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.adapters.vision import PROMPT_VERSION
from app.config import Settings
from app.main import make_app
from app.models import VisualResult
from app.storage import Store


class FakeVoice:
    def conversation_token(self):
        return "conversation-token"


class CapturingVision:
    model_id = "test-vision"
    prompt_version = PROMPT_VERSION

    def __init__(self, classification="LED"):
        self.inputs = []
        self.classification = classification

    def assess(self, image):
        self.inputs.append(image)
        return VisualResult(
            visual_classification=self.classification, visual_confidence="HIGH", evidence_quality="GOOD",
            evidence_origin="FIELD_PLAUSIBLE", color_temperature_cue="COOL_WHITE",
            visible_identifiers=[],
            visual_cues=["Visible emitter modules"], limitations=[], retake_guidance=None,
            observed_power_w=None, power_evidence=None,
        )


@pytest.fixture()
def harness(tmp_path):
    settings = Settings(
        data_dir=Path(tmp_path), demo_access_token="demo", agent_tool_key="a" * 40,
        session_signing_key="s" * 40, agent_id="agent_demo", vision_api_key="v" * 40,
        elevenlabs_webhook_secret="post-call-secret",
    )
    vision = CapturingVision()
    app = make_app(settings=settings, voice=FakeVoice(), vision=vision, store=Store(settings.data_dir))
    client = TestClient(app)
    session = client.post("/v1/sessions", headers={"X-Demo-Access": "demo"}, json={"request_id": "request-001"}).json()
    bind = client.post(f"/v1/sessions/{session['session_id']}/bind", headers={"Authorization": f"Bearer {session['session_token']}"}, json={"conversation_id": "conv-001"})
    assert bind.status_code == 200
    tool_headers = {"X-Agent-Key": "a" * 40, "X-Tool-Session": session["tool_session_capability"], "X-Conversation-Id": "conv-001"}
    started = client.post("/v1/inspections/start", headers=tool_headers, json={
        "request_id": "inspect-001", "pole_id": "P-18427", "operator_classification": "LED",
        "operator_utterance": "It looks like LED.",
    })
    assert started.status_code == 200, started.text
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    return client, session, tool_headers, started.json(), image.getvalue(), vision
