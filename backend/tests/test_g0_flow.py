import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from app.adapters.business import DemoBusinessSource
from app.config import Settings
from app.main import make_app
from app.storage import Store


class SuccessfulExternal:
    configured = True

    def __init__(self):
        self.calls = []

    def dispatch(self, operation_id, payload):
        self.calls.append((operation_id, payload))
        return {
            "external_system": "Airtable", "record_id": "rec_work_18427",
            "display_reference": "rec_work_18427", "status": "REQUESTED",
        }


def queue_asset(client, session, pole_id):
    response = client.get(
        "/v1/assets",
        headers={"Authorization": f"Bearer {session['session_token']}"},
    )
    assert response.status_code == 200, response.text
    return next(asset for asset in response.json()["assets"] if asset["pole_id"] == pole_id)


def test_non_ascii_demo_token_is_rejected_without_server_error(harness):
    client, *_ = harness
    response = client.post(
        "/v1/sessions",
        headers=[(b"X-Demo-Access", "invalid".encode("utf-8"))],
        json={"request_id": "bad-unicode-token"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_real_session_binding_and_photo_assessment(harness):
    client, session, tool_headers, inspection, image, vision = harness
    iid = inspection["inspection_id"]
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1",
                          headers={"Authorization": f"Bearer {session['session_token']}"},
                          files={"file": ("deceptive-name.png", image, "application/octet-stream")})
    assert receipt.status_code == 200, receipt.text
    receipt = receipt.json()
    assert receipt["mime_type"] == "image/jpeg"
    assert receipt["inspection_revision"] == 2
    assessed = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                           json={"evidence_id": receipt["evidence_id"], "expected_revision": 2})
    assert assessed.status_code == 200, assessed.text
    result = assessed.json()
    assert result["assessment"]["visual_classification"] == "LED"
    assert result["assessment_acceptable"] is True
    assert result["allowed_next_actions"] == []
    assert result["state"] == "AWAITING_OPERATOR_RESPONSE"
    assert result["inspection_revision"] == 3
    assert len(vision.inputs) == 1
    state = client.get(f"/v1/inspections/{iid}", headers={"Authorization": f"Bearer {session['session_token']}"}).json()
    assert state["state"] == "AWAITING_OPERATOR_RESPONSE"


def test_agent_reads_inspection_state_with_tool_session(harness):
    # get_inspection_state is an agent tool: it authenticates with the
    # X-Agent-Key trio, not the browser Bearer capability.
    client, _, tool_headers, inspection, _, _ = harness
    iid = inspection["inspection_id"]
    state = client.get(f"/v1/inspections/{iid}", headers=tool_headers)
    assert state.status_code == 200, state.text
    assert state.json()["state"] == "OPEN"
    forged = {**tool_headers, "X-Agent-Key": "b" * 40}
    assert client.get(f"/v1/inspections/{iid}", headers=forged).status_code == 401


def test_pole_number_is_canonicalized_without_prefix(harness):
    _, _, _, inspection, _, _ = harness
    # The shared fixture still sends the old format to prove compatibility.
    assert inspection["pole_id"] == "18427"
    assert inspection["asset_id"] == "A-18427"
    assert inspection["fixture_id"] == "F-18427-01"


def test_corrected_pole_supersedes_an_unknown_identity_attempt(harness):
    client, _, tool_headers, inspection, *_ = harness
    unknown = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={
            "request_id": "correct-pole-to-unknown",
            "pole_id": "1848",
            "supersedes_inspection_id": inspection["inspection_id"],
            "expected_previous_revision": inspection["revision"],
        },
    )
    assert unknown.status_code == 200, unknown.text
    unknown_body = unknown.json()
    assert unknown_body["identity_status"] == "UNKNOWN"
    assert unknown_body["allowed_next_actions"] == ["VERIFY_FIXTURE"]

    corrected = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={
            "request_id": "correct-pole-to-18428",
            "pole_id": "18428",
            "supersedes_inspection_id": unknown_body["inspection_id"],
            "expected_previous_revision": unknown_body["revision"],
        },
    )
    assert corrected.status_code == 200, corrected.text
    corrected_body = corrected.json()
    assert corrected_body["identity_status"] == "FOUND"
    assert corrected_body["pole_id"] == "18428"
    assert corrected_body["state"] == "AWAITING_OBSERVATION"
    assert corrected_body["allowed_next_actions"] == ["RECORD_FIELD_OBSERVATION"]


def test_asset_queue_is_projected_from_latest_inspection(harness):
    client, session, _, inspection, _, _ = harness

    asset = queue_asset(client, session, "18427")

    assert asset["queue_source"] == "LATEST_INSPECTION"
    assert asset["operational"]["latest_inspection_id"] == inspection["inspection_id"]
    assert asset["operational"]["latest_inspection_state"] == "OPEN"
    assert asset["operational"]["queue_status"] == "IN_VERIFICATION"
    assert asset["operational"]["next_step"] == "CAPTURE_EVIDENCE"

    untouched = queue_asset(client, session, "18428")
    assert untouched["queue_source"] == "NO_INSPECTION"
    assert untouched["operational"]["queue_status"] == "NOT_STARTED"
    assert untouched["operational"]["next_step"] == "START_INSPECTION"
    assert untouched["operational"]["latest_inspection_id"] is None


def test_specific_asset_record_is_read_only_and_scoped_to_one_pole(harness):
    client, session, tool_headers, *_ = harness

    response = client.post(
        "/v1/asset-records/lookup",
        headers=tool_headers,
        json={"pole_id": "18429"},
    )

    assert response.status_code == 200, response.text
    record = response.json()
    assert record["identity_status"] == "FOUND"
    assert record["pole_id"] == "18429"
    assert record["registry"]["registered_subtype"] == "HPS"
    assert record["registry"]["lamp_power_w"] == 70
    assert record["registry"]["auxiliary_power_w"] == 30
    assert record["registry"]["registered_power_w"] == 100
    assert record["operational"]["work_order_id"] == "OS-2026-0702-29"
    assert record["operational"]["work_order_status"] == "OPEN"
    assert record["operational"]["queue_status"] == "NOT_STARTED"
    assert record["operational"]["latest_inspection_id"] is None
    assert record["latest_inspection"] is None

    inspected = client.post(
        "/v1/asset-records/lookup",
        headers=tool_headers,
        json={"pole_id": "18427"},
    )
    assert inspected.status_code == 200, inspected.text
    latest = inspected.json()["latest_inspection"]
    assert latest["inspection_id"] is not None
    assert latest["pole_id"] == "18427"
    assert latest["state"] == "OPEN"
    assert latest["field_observation"]["classification"] == "LED"
    assert latest["evidence_count"] == 0
    assert latest["decision"] is None
    assert latest["operation"] is None

    browser = client.get(
        "/v1/assets/P-18429",
        headers={"Authorization": f"Bearer {session['session_token']}"},
    )
    assert browser.status_code == 200
    assert browser.json()["pole_id"] == "18429"

    unknown = client.post(
        "/v1/asset-records/lookup",
        headers=tool_headers,
        json={"pole_id": "99999"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "UNKNOWN_ASSET"


def test_inspection_summary_is_all_persisted_inspections_for_browser_and_agent(harness):
    client, session, tool_headers, *_ = harness
    auth = {"Authorization": f"Bearer {session['session_token']}"}

    browser_summary = client.get("/v1/analytics/inspections", headers=auth)
    agent_summary = client.get("/v1/analytics/inspections", headers=tool_headers)

    assert browser_summary.status_code == 200, browser_summary.text
    assert agent_summary.status_code == 200, agent_summary.text
    assert browser_summary.json() == agent_summary.json()
    body = browser_summary.json()
    assert body["scope"] == "ALL_PERSISTED_INSPECTIONS"
    assert body["metrics"]["total_inspections"] == 1
    assert body["metrics"]["unique_poles"] == 1
    assert body["metrics"]["in_progress"] == 1
    assert body["metrics"]["reconciled"] == 0
    assert body["metrics"]["divergent_from_registry"] == 0
    assert body["suggested_question"] == "How many inspections found a discrepancy with the utility record?"
    assert body["suggested_action"] == "Start a new inspection"


def test_inspection_history_returns_readable_dossiers_without_session_secrets(harness):
    client, session, _, inspection, *_ = harness
    response = client.get(
        "/v1/inspections",
        headers={"Authorization": f"Bearer {session['session_token']}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "ALL_PERSISTED_INSPECTIONS"
    assert len(body["inspections"]) == 1
    item = body["inspections"][0]
    assert item["inspection_id"] == inspection["inspection_id"]
    assert item["pole_id"] == "18427"
    assert item["queue_status"] == "IN_VERIFICATION"
    assert item["evidence_count"] == 0
    assert "session_id" not in item
    assert "conversation_id" not in item


def test_duplicate_normalized_image_is_idempotent(harness):
    client, session, _, inspection, image, _ = harness
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    iid = inspection["inspection_id"]
    first = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                        files={"file": ("one.png", image, "image/png")}).json()
    second = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                         files={"file": ("two.jpg", image, "image/jpeg")})
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["evidence_id"] == first["evidence_id"]


def test_same_photo_cannot_be_reused_for_another_pole(harness):
    client, first_session, _, first_inspection, image, _ = harness
    first_auth = {"Authorization": f"Bearer {first_session['session_token']}"}
    uploaded = client.post(
        f"/v1/inspections/{first_inspection['inspection_id']}/evidence?expected_revision=1",
        headers=first_auth,
        files={"file": ("first.png", image, "image/png")},
    )
    assert uploaded.status_code == 200

    second_session = client.post(
        "/v1/sessions", headers={"X-Demo-Access": "demo"}, json={"request_id": "request-reuse"},
    ).json()
    second_auth = {"Authorization": f"Bearer {second_session['session_token']}"}
    assert client.post(
        f"/v1/sessions/{second_session['session_id']}/bind",
        headers=second_auth,
        json={"conversation_id": "conv-reuse"},
    ).status_code == 200
    second_tools = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": second_session["tool_session_capability"],
        "X-Conversation-Id": "conv-reuse",
    }
    second = client.post("/v1/inspections/start", headers=second_tools, json={
        "request_id": "inspect-reuse", "pole_id": "18428", "operator_classification": "LEGACY",
        "operator_utterance": "It looks legacy.",
    }).json()
    rejected = client.post(
        f"/v1/inspections/{second['inspection_id']}/evidence?expected_revision=1",
        headers=second_auth,
        files={"file": ("second.png", image, "image/png")},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "EVIDENCE_REUSED"
    assert "18427" in rejected.json()["message"]


def test_same_photo_can_be_reused_in_a_later_inspection_of_the_same_pole(harness):
    client, first_session, _, first_inspection, image, _ = harness
    first_auth = {"Authorization": f"Bearer {first_session['session_token']}"}
    assert client.post(
        f"/v1/inspections/{first_inspection['inspection_id']}/evidence?expected_revision=1",
        headers=first_auth,
        files={"file": ("first.png", image, "image/png")},
    ).status_code == 200

    second_session = client.post(
        "/v1/sessions", headers={"X-Demo-Access": "demo"}, json={"request_id": "request-repeat-same-pole"},
    ).json()
    second_auth = {"Authorization": f"Bearer {second_session['session_token']}"}
    assert client.post(
        f"/v1/sessions/{second_session['session_id']}/bind",
        headers=second_auth,
        json={"conversation_id": "conv-repeat-same-pole"},
    ).status_code == 200
    second_tools = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": second_session["tool_session_capability"],
        "X-Conversation-Id": "conv-repeat-same-pole",
    }
    second = client.post("/v1/inspections/start", headers=second_tools, json={
        "request_id": "inspect-repeat-same-pole",
        "pole_id": "18427",
        "operator_classification": "LED",
        "operator_utterance": "It looks like LED.",
    }).json()
    accepted = client.post(
        f"/v1/inspections/{second['inspection_id']}/evidence?expected_revision=1",
        headers=second_auth,
        files={"file": ("repeat.png", image, "image/png")},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["duplicate"] is False


def test_revision_and_session_boundaries(harness):
    client, session, tool_headers, inspection, image, _ = harness
    iid = inspection["inspection_id"]
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    bad = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=99", headers=auth,
                      files={"file": ("x.png", image, "image/png")})
    # The stale request is rejected after ownership and pixel validation.
    assert bad.status_code == 409 and bad.json()["code"] == "STALE_REVISION"
    wrong = {**tool_headers, "X-Conversation-Id": "conv-other"}
    assert client.post("/v1/inspections/start", headers=wrong, json={
        "request_id": "inspect-002", "pole_id": "P-18427", "operator_classification": "LED",
        "operator_utterance": "It looks like LED.",
    }).status_code == 409


def test_visual_request_has_no_business_context():
    from app.adapters.vision import build_visual_request
    request = build_visual_request(b"normalized-pixels", "gpt-5.5")
    serialized = str(request)
    # The rubric may mention excluded concepts to make the boundary explicit. It
    # must never contain a value from the current business record or transcript.
    assert "P-18427" not in serialized
    assert "operator_utterance" not in serialized
    assert "registered_power_w" not in serialized
    assert "filename" not in serialized.lower()


def test_visible_equipment_code_does_not_gate_visual_evidence(harness):
    from app.models import VisualResult

    client, session, tool_headers, inspection, image, vision = harness

    def assessment_with_equipment_code(_image):
        return VisualResult(
            visual_classification="LED",
            visual_confidence="HIGH",
            evidence_quality="GOOD",
            evidence_origin="FIELD_PLAUSIBLE",
            color_temperature_cue="COOL_WHITE",
            visible_identifiers=["NB-2023-01756"],
            visual_cues=["Integrated LED emitter array", "Equipment label is legible"],
            limitations=[],
            retake_guidance=None,
            observed_power_w=100,
            power_evidence="Label reads Potencia: 100W",
        )

    vision.assess = assessment_with_equipment_code
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    receipt = client.post(
        f"/v1/inspections/{inspection['inspection_id']}/evidence?expected_revision=1",
        headers=auth,
        files={"file": ("equipment-label.png", image, "image/png")},
    ).json()
    assessed = client.post(
        f"/v1/inspections/{inspection['inspection_id']}/assessments",
        headers=tool_headers,
        json={"evidence_id": receipt["evidence_id"], "expected_revision": 2},
    )
    assert assessed.status_code == 200, assessed.text
    result = assessed.json()
    assert result["assessment"]["identity_consistency"] == "NOT_CHECKED"
    assert result["assessment"]["observed_power_w"] == 100
    assert result["assessment_acceptable"] is True
    assert result["state"] == "AWAITING_OPERATOR_RESPONSE"


def test_third_inconclusive_photo_advances_to_manual_review(harness):
    from app.models import VisualResult

    client, session, tool_headers, inspection, _, vision = harness

    def inconclusive(_image):
        return VisualResult(
            visual_classification="INCONCLUSIVE",
            visual_confidence="LOW",
            evidence_quality="LIMITED",
            evidence_origin="FIELD_PLAUSIBLE",
            color_temperature_cue="UNCERTAIN",
            visible_identifiers=[],
            visual_cues=["Visible luminaire without discriminating details"],
            limitations=["Distance prevents identifying the technology"],
            retake_guidance="Frame the luminaire more closely without leaving a safe position.",
            observed_power_w=None,
            power_evidence=None,
        )

    vision.assess = inconclusive
    iid = inspection["inspection_id"]
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    revision = 1
    assessed = None
    for color in ("white", "gray", "black"):
        image = io.BytesIO()
        Image.new("RGB", (64, 64), color).save(image, "PNG")
        receipt = client.post(
            f"/v1/inspections/{iid}/evidence?expected_revision={revision}",
            headers=auth,
            files={"file": ("attempt.png", image.getvalue(), "image/png")},
        ).json()
        revision = receipt["inspection_revision"]
        assessed_response = client.post(
            f"/v1/inspections/{iid}/assessments",
            headers=tool_headers,
            json={"evidence_id": receipt["evidence_id"], "expected_revision": revision},
        )
        assert assessed_response.status_code == 200, assessed_response.text
        assessed = assessed_response.json()
        revision = assessed["inspection_revision"]

    assert assessed is not None
    assert assessed["state"] == "NEEDS_EVIDENCE"
    assert assessed["allowed_next_actions"] == ["RECONCILE"]

    # Snapshots written by visual-v3 ended here with no next action. A read
    # exposes the safe migration so an in-flight demo can recover in place.
    with client.app.state.service.store.transaction() as db:
        row = db.execute("SELECT snapshot FROM inspections WHERE id=?", (iid,)).fetchone()
        legacy_snapshot = json.loads(row["snapshot"])
        legacy_snapshot["allowed_next_actions"] = []
        db.execute("UPDATE inspections SET snapshot=? WHERE id=?", (json.dumps(legacy_snapshot), iid))
    recovered = client.get(f"/v1/inspections/{iid}", headers=tool_headers).json()
    assert recovered["allowed_next_actions"] == ["RECONCILE"]
    assert recovered["recovery_hint"] == "RECONCILE_EXHAUSTED_EVIDENCE"

    reconciled = client.post(
        f"/v1/inspections/{iid}/reconcile",
        headers=tool_headers,
        json={"expected_revision": revision},
    )
    assert reconciled.status_code == 200, reconciled.text
    state = reconciled.json()
    assert state["state"] == "DECIDED"
    assert state["decision"]["disposition"] == "MANUAL_REVIEW"
    assert state["decision"]["reason_codes"] == ["INSUFFICIENT_EVIDENCE"]
    assert state["allowed_next_actions"] == ["PREPARE_ACTION"]


def test_acceptance_reconciles_against_register_and_blocks_replacement(harness):
    client, session, tool_headers, inspection, image, _ = harness
    iid = inspection["inspection_id"]
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("fixture.png", image, "image/png")}).json()
    assessment = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                             json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    accepted = client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-001", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessment["assessment"]["assessment_id"], "operator_utterance": "I confirm that it is LED.",
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "OPEN"
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4})
    assert reconciled.status_code == 200, reconciled.text
    state = reconciled.json()
    assert state["registry"]["classification"] == "LEGACY"
    assert state["decision"]["disposition"] == "REGISTRY_MISMATCH"
    assert state["outcome"] == "REGISTRY_MISMATCH_FLAGGED"
    assert "PREPARE_ACTION" in state["allowed_next_actions"]


def test_reconciliation_flow_loads_registry_before_field_observation(tmp_path):
    from conftest import CapturingVision, FakeVoice

    settings = Settings(
        data_dir=tmp_path,
        demo_access_token="demo",
        agent_tool_key="a" * 40,
        session_signing_key="s" * 40,
        agent_id="agent_demo",
        vision_api_key="v" * 40,
    )
    client = TestClient(make_app(
        settings=settings,
        voice=FakeVoice(),
        vision=CapturingVision("LED"),
        store=Store(settings.data_dir),
    ))
    session = client.post(
        "/v1/sessions",
        headers={"X-Demo-Access": "demo"},
        json={"request_id": "request-reconcile-start"},
    ).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(
        f"/v1/sessions/{session['session_id']}/bind",
        headers=auth,
        json={"conversation_id": "conv-reconcile-start"},
    )
    tool_headers = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": session["tool_session_capability"],
        "X-Conversation-Id": "conv-reconcile-start",
    }

    started = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={"request_id": "inspect-reconcile-start", "pole_id": "18427"},
    )
    assert started.status_code == 200, started.text
    state = started.json()
    assert state["workflow_version"] == "field-reconciliation-v2"
    assert state["state"] == "AWAITING_OBSERVATION"
    assert state["allowed_next_actions"] == ["RECORD_FIELD_OBSERVATION"]
    assert state["registry"] == {
        "classification": "LEGACY",
        "lamp_power_w": 70,
        "auxiliary_type": "BALLAST",
        "auxiliary_power_w": 30,
        "registered_power_w": 100,
        "registered_subtype": "HPS",
        "last_updated_at": "2025-11-04",
        "revision": "register-2025-11-04",
    }
    assert state["operational"]["work_order_status"] == "COMPLETED"
    assert state["operational"]["registry_sync_status"] == "PENDING"
    assert state["billing"]["daily_minutes"] == 687
    assert state["billing"]["time_basis"] == "FICTIONAL_MUNICIPAL_PARAMETER"

    observed = client.post(
        f"/v1/inspections/{state['inspection_id']}/field-observation",
        headers=tool_headers,
        json={
            "request_id": "field-observation-001",
            "expected_revision": 1,
            "operator_classification": "LED",
            "operator_utterance": "It looks like LED.",
        },
    )
    assert observed.status_code == 200, observed.text
    assert observed.json()["revision"] == 2
    assert observed.json()["allowed_next_actions"] == ["UPLOAD_EVIDENCE"]


def test_reconciliation_flow_preserves_matching_load_and_flags_technology_mismatch(tmp_path):
    from conftest import CapturingVision, FakeVoice
    from app.models import VisualResult

    settings = Settings(
        data_dir=tmp_path,
        demo_access_token="demo",
        agent_tool_key="a" * 40,
        session_signing_key="s" * 40,
        agent_id="agent_demo",
        vision_api_key="v" * 40,
    )
    vision = CapturingVision("LED")
    client = TestClient(make_app(
        settings=settings,
        voice=FakeVoice(),
        vision=vision,
        store=Store(settings.data_dir),
    ))
    session = client.post(
        "/v1/sessions",
        headers={"X-Demo-Access": "demo"},
        json={"request_id": "request-power-delta"},
    ).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(
        f"/v1/sessions/{session['session_id']}/bind",
        headers=auth,
        json={"conversation_id": "conv-power-delta"},
    )
    tool_headers = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": session["tool_session_capability"],
        "X-Conversation-Id": "conv-power-delta",
    }
    inspection = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={"request_id": "inspect-power-delta", "pole_id": "18427"},
    ).json()
    iid = inspection["inspection_id"]
    client.post(
        f"/v1/inspections/{iid}/field-observation",
        headers=tool_headers,
        json={
            "request_id": "field-observation-power",
            "expected_revision": 1,
            "operator_classification": "LED",
            "operator_utterance": "It looks like LED.",
        },
    )

    first_image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(first_image, "PNG")
    first_receipt = client.post(
        f"/v1/inspections/{iid}/evidence?expected_revision=2",
        headers=auth,
        files={"file": ("general.png", first_image.getvalue(), "image/png")},
    ).json()
    first_assessment = client.post(
        f"/v1/inspections/{iid}/assessments",
        headers=tool_headers,
        json={"evidence_id": first_receipt["evidence_id"], "expected_revision": 3},
    )
    assert first_assessment.status_code == 200, first_assessment.text
    first_state = first_assessment.json()
    assert first_state["allowed_next_actions"] == ["UPLOAD_EVIDENCE"]
    assert "label" in first_state["message"].lower()

    def label_assessment(_image):
        return VisualResult(
            visual_classification="INCONCLUSIVE",
            visual_confidence="LOW",
            evidence_quality="GOOD",
            evidence_origin="FIELD_PLAUSIBLE",
            color_temperature_cue="UNCERTAIN",
            visible_identifiers=["NB-2023-01756"],
            visual_cues=["Legible technical label"],
            limitations=["The photo shows the label, not the complete luminaire"],
            retake_guidance=None,
            observed_power_w=100,
            power_evidence="Rated power: 100 W",
        )

    vision.assess = label_assessment
    second_image = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(second_image, "PNG")
    second_receipt = client.post(
        f"/v1/inspections/{iid}/evidence?expected_revision=4",
        headers=auth,
        files={"file": ("label.png", second_image.getvalue(), "image/png")},
    ).json()
    second_assessment = client.post(
        f"/v1/inspections/{iid}/assessments",
        headers=tool_headers,
        json={"evidence_id": second_receipt["evidence_id"], "expected_revision": 5},
    )
    assert second_assessment.status_code == 200, second_assessment.text
    assert second_assessment.json()["assessment_acceptable"] is True
    assert second_assessment.json()["allowed_next_actions"] == ["RECONCILE"]

    reconciled = client.post(
        f"/v1/inspections/{iid}/reconcile",
        headers=tool_headers,
        json={"expected_revision": 6},
    )
    assert reconciled.status_code == 200, reconciled.text
    state = reconciled.json()
    decision = state["decision"]
    assert decision["reconciliation_status"] == "DIVERGENT"
    assert decision["registered_classification"] == "LEGACY"
    assert decision["field_classification"] == "LED"
    assert decision["registered_power_w"] == 100
    assert decision["observed_power_w"] == 100
    assert decision["power_delta_w"] == 0
    assert decision["estimated_cycle_energy_delta_kwh"] == 0
    assert decision["technology_status"] == "MISMATCH"
    assert decision["power_status"] == "MATCH"

    summary = client.get("/v1/analytics/inspections", headers=tool_headers)
    assert summary.status_code == 200, summary.text
    metrics = summary.json()["metrics"]
    assert metrics["total_inspections"] == 1
    assert metrics["reconciled"] == 1
    assert metrics["divergent_from_registry"] == 1
    assert metrics["field_led"] == 1
    assert metrics["power_verified"] == 1
    assert metrics["verified_power_delta_w"] == 0
    assert metrics["estimated_cycle_energy_delta_kwh"] == 0

    queued = queue_asset(client, session, "18427")
    assert queued["operational"]["queue_status"] == "DIVERGENCE_CONFIRMED"
    assert queued["operational"]["next_step"] == "PREPARE_UPDATE"
    assert queued["operational"]["latest_reconciliation_status"] == "DIVERGENT"

    proposal = client.post(
        f"/v1/inspections/{iid}/proposals",
        headers=tool_headers,
        json={"request_id": "proposal-registry-review", "expected_revision": 6},
    )
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["action_type"] == "CREATE_REGISTRY_REVIEW"
    assert "100 W" in proposal.json()["effect_summary"]


def test_golden_photo_can_verify_led_and_100_w_in_one_assessment(tmp_path):
    from conftest import CapturingVision, FakeVoice
    from app.models import VisualResult

    settings = Settings(
        data_dir=tmp_path,
        demo_access_token="demo",
        agent_tool_key="a" * 40,
        session_signing_key="s" * 40,
        agent_id="agent_demo",
        vision_api_key="v" * 40,
    )
    vision = CapturingVision("LED")

    def combined_assessment(_image):
        return VisualResult(
            visual_classification="LED",
            visual_confidence="HIGH",
            evidence_quality="GOOD",
            evidence_origin="FIELD_PLAUSIBLE",
            color_temperature_cue="COOL_WHITE",
            visible_identifiers=[],
            visual_cues=["Integrated LED emitter array", "Complete luminaire visible"],
            limitations=[],
            retake_guidance=None,
            observed_power_w=100,
            power_evidence="Luminaire label reads Potencia: 100W",
        )

    vision.assess = combined_assessment
    client = TestClient(make_app(
        settings=settings,
        voice=FakeVoice(),
        vision=vision,
        store=Store(settings.data_dir),
    ))
    session = client.post(
        "/v1/sessions",
        headers={"X-Demo-Access": "demo"},
        json={"request_id": "request-golden-single-photo"},
    ).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(
        f"/v1/sessions/{session['session_id']}/bind",
        headers=auth,
        json={"conversation_id": "conv-golden-single-photo"},
    )
    tools = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": session["tool_session_capability"],
        "X-Conversation-Id": "conv-golden-single-photo",
    }
    inspection = client.post(
        "/v1/inspections/start",
        headers=tools,
        json={"request_id": "inspect-golden-single-photo", "pole_id": "18427"},
    ).json()
    iid = inspection["inspection_id"]
    observed = client.post(
        f"/v1/inspections/{iid}/field-observation",
        headers=tools,
        json={
            "request_id": "observe-golden-single-photo",
            "expected_revision": 1,
            "operator_classification": "LED",
            "operator_utterance": "It looks like LED.",
        },
    ).json()
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    receipt = client.post(
        f"/v1/inspections/{iid}/evidence?expected_revision={observed['revision']}",
        headers=auth,
        files={"file": ("golden.png", image.getvalue(), "image/png")},
    ).json()
    assessed = client.post(
        f"/v1/inspections/{iid}/assessments",
        headers=tools,
        json={"evidence_id": receipt["evidence_id"], "expected_revision": receipt["inspection_revision"]},
    ).json()
    assert assessed["allowed_next_actions"] == ["RECONCILE"]
    assert assessed["assessment"]["observed_power_w"] == 100

    reconciled = client.post(
        f"/v1/inspections/{iid}/reconcile",
        headers=tools,
        json={"expected_revision": assessed["inspection_revision"]},
    ).json()
    decision = reconciled["decision"]
    assert decision["reconciliation_status"] == "DIVERGENT"
    assert decision["technology_status"] == "MISMATCH"
    assert decision["power_status"] == "MATCH"
    assert decision["power_delta_w"] == 0


def test_unavailable_label_keeps_power_unverified_and_continues_to_registry_review(tmp_path):
    from conftest import CapturingVision, FakeVoice

    settings = Settings(
        data_dir=tmp_path,
        demo_access_token="demo",
        agent_tool_key="a" * 40,
        session_signing_key="s" * 40,
        agent_id="agent_demo",
        vision_api_key="v" * 40,
    )
    client = TestClient(make_app(
        settings=settings,
        voice=FakeVoice(),
        vision=CapturingVision("LED"),
        store=Store(settings.data_dir),
    ))
    session = client.post(
        "/v1/sessions",
        headers={"X-Demo-Access": "demo"},
        json={"request_id": "request-no-label"},
    ).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(
        f"/v1/sessions/{session['session_id']}/bind",
        headers=auth,
        json={"conversation_id": "conv-no-label"},
    )
    tool_headers = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": session["tool_session_capability"],
        "X-Conversation-Id": "conv-no-label",
    }
    inspection = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={"request_id": "inspect-no-label", "pole_id": "18427"},
    ).json()
    iid = inspection["inspection_id"]
    observed = client.post(
        f"/v1/inspections/{iid}/field-observation",
        headers=tool_headers,
        json={
            "request_id": "field-no-label",
            "expected_revision": 1,
            "operator_classification": "LED",
            "operator_utterance": "It looks like LED.",
        },
    ).json()

    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    receipt = client.post(
        f"/v1/inspections/{iid}/evidence?expected_revision={observed['revision']}",
        headers=auth,
        files={"file": ("general.png", image.getvalue(), "image/png")},
    ).json()
    assessed = client.post(
        f"/v1/inspections/{iid}/assessments",
        headers=tool_headers,
        json={
            "evidence_id": receipt["evidence_id"],
            "expected_revision": receipt["inspection_revision"],
        },
    )
    assert assessed.status_code == 200, assessed.text
    assessed_state = assessed.json()
    assert assessed_state["allowed_next_actions"] == ["UPLOAD_EVIDENCE"]
    assert "power" in assessed_state["message"].lower()

    unavailable = client.post(
        f"/v1/inspections/{iid}/operator-responses",
        headers=tool_headers,
        json={
            "request_id": "label-unavailable",
            "expected_revision": assessed_state["inspection_revision"],
            "kind": "DECLINE_RETAKE",
            "operator_utterance": "I don't know. I can't see a label from here.",
        },
    )
    assert unavailable.status_code == 200, unavailable.text
    unavailable_state = unavailable.json()
    assert unavailable_state["state"] == "OPEN"
    assert unavailable_state["allowed_next_actions"] == ["RECONCILE"]
    assert "power will remain unverified" in unavailable_state["message"].lower()

    reconciled = client.post(
        f"/v1/inspections/{iid}/reconcile",
        headers=tool_headers,
        json={"expected_revision": unavailable_state["revision"]},
    )
    assert reconciled.status_code == 200, reconciled.text
    final_state = reconciled.json()
    decision = final_state["decision"]
    assert decision["reconciliation_status"] == "DIVERGENT"
    assert decision["disposition"] == "REGISTRY_MISMATCH"
    assert decision["field_classification"] == "LED"
    assert decision["observed_power_w"] is None
    assert decision["power_delta_w"] is None
    assert decision["estimated_cycle_energy_delta_kwh"] is None
    assert decision["power_status"] == "UNVERIFIED"
    assert decision["reason_codes"] == ["TECHNOLOGY_MISMATCH", "POWER_UNVERIFIED"]
    assert final_state["allowed_next_actions"] == ["PREPARE_ACTION"]

    proposal = client.post(
        f"/v1/inspections/{iid}/proposals",
        headers=tool_headers,
        json={
            "request_id": "proposal-no-label",
            "expected_revision": final_state["revision"],
        },
    )
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["action_type"] == "CREATE_REGISTRY_REVIEW"
    assert "not yet verified" in proposal.json()["effect_summary"].lower()


def test_technician_correction_supersedes_pending_proposal_and_reconciles_again(tmp_path):
    from conftest import CapturingVision, FakeVoice

    settings = Settings(
        data_dir=tmp_path,
        demo_access_token="demo",
        agent_tool_key="a" * 40,
        session_signing_key="s" * 40,
        agent_id="agent_demo",
        vision_api_key="v" * 40,
    )
    client = TestClient(make_app(
        settings=settings,
        voice=FakeVoice(),
        vision=CapturingVision("LEGACY"),
        store=Store(settings.data_dir),
    ))
    session = client.post(
        "/v1/sessions",
        headers={"X-Demo-Access": "demo"},
        json={"request_id": "request-correct-observation"},
    ).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(
        f"/v1/sessions/{session['session_id']}/bind",
        headers=auth,
        json={"conversation_id": "conv-correct-observation"},
    )
    tool_headers = {
        "X-Agent-Key": "a" * 40,
        "X-Tool-Session": session["tool_session_capability"],
        "X-Conversation-Id": "conv-correct-observation",
    }
    inspection = client.post(
        "/v1/inspections/start",
        headers=tool_headers,
        json={"request_id": "inspect-correct-observation", "pole_id": "18427"},
    ).json()
    iid = inspection["inspection_id"]
    observed = client.post(
        f"/v1/inspections/{iid}/field-observation",
        headers=tool_headers,
        json={
            "request_id": "field-before-correction",
            "expected_revision": 1,
            "operator_classification": "LED",
            "operator_utterance": "It looks like LED.",
        },
    ).json()

    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    receipt = client.post(
        f"/v1/inspections/{iid}/evidence?expected_revision={observed['revision']}",
        headers=auth,
        files={"file": ("general.png", image.getvalue(), "image/png")},
    ).json()
    assessed = client.post(
        f"/v1/inspections/{iid}/assessments",
        headers=tool_headers,
        json={
            "evidence_id": receipt["evidence_id"],
            "expected_revision": receipt["inspection_revision"],
        },
    ).json()
    assert assessed["allowed_next_actions"] == ["RECONCILE"]
    assessment_id = assessed["assessment"]["assessment_id"]

    first_reconciliation = client.post(
        f"/v1/inspections/{iid}/reconcile",
        headers=tool_headers,
        json={"expected_revision": assessed["inspection_revision"]},
    ).json()
    assert first_reconciliation["decision"]["reconciliation_status"] == "INCONCLUSIVE"
    assert first_reconciliation["decision"]["reason_codes"] == ["HUMAN_AI_DISAGREEMENT"]

    proposal = client.post(
        f"/v1/inspections/{iid}/proposals",
        headers=tool_headers,
        json={
            "request_id": "proposal-before-correction",
            "expected_revision": first_reconciliation["revision"],
        },
    )
    assert proposal.status_code == 201, proposal.text
    proposal_id = proposal.json()["proposal_id"]
    pending = client.get(f"/v1/inspections/{iid}", headers=auth).json()
    assert pending["state"] == "AWAITING_CONFIRMATION"
    assert pending["active_proposal_id"] == proposal_id

    corrected = client.post(
        f"/v1/inspections/{iid}/operator-responses",
        headers=tool_headers,
        json={
            "request_id": "correct-observation-to-legacy",
            "expected_revision": pending["revision"],
            "kind": "ACCEPT_VISUAL",
            "assessment_id": assessment_id,
            "operator_utterance": "Actually, I made a mistake. It is legacy, not LED.",
        },
    )
    assert corrected.status_code == 200, corrected.text
    corrected_state = corrected.json()
    assert corrected_state["state"] == "DECIDED"
    assert corrected_state["allowed_next_actions"] == ["PREPARE_ACTION"]
    assert corrected_state["active_proposal_id"] is None
    assert corrected_state["original_observation"]["classification"] == "LED"
    assert corrected_state["current_observation"]["classification"] == "LEGACY"
    assert corrected_state["decision"]["reconciliation_status"] == "COMPATIBLE"
    assert corrected_state["decision"]["registered_classification"] == "LEGACY"
    assert corrected_state["decision"]["field_classification"] == "LEGACY"
    assert "HUMAN_AI_DISAGREEMENT" not in corrected_state["decision"]["reason_codes"]

    stale_confirmation = client.post(
        f"/v1/proposals/{proposal_id}/confirmation",
        headers=tool_headers,
        json={
            "expected_revision": corrected_state["revision"],
            "decision": "CONFIRM",
            "operator_utterance": "Yes, I confirm.",
        },
    )
    assert stale_confirmation.status_code == 409
    assert stale_confirmation.json()["code"] == "PROPOSAL_NOT_ACTIVE"


def test_missing_business_snapshot_fails_closed_before_decision(harness):
    client, session, tool_headers, inspection, image, _ = harness
    iid = inspection["inspection_id"]
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("fixture.png", image, "image/png")}).json()
    assessment = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                             json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-missing-business", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessment["assessment"]["assessment_id"], "operator_utterance": "I confirm.",
    })

    class MissingBusiness:
        def resolve(self, fixture_id):
            return None

    client.app.state.service.business = MissingBusiness()
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4})
    assert reconciled.status_code == 503
    assert reconciled.json()["code"] == "BUSINESS_DATA_UNAVAILABLE"


def test_matching_led_reconciles_to_local_completion(harness):
    client, _, _, _, image, vision = harness
    session = client.post("/v1/sessions", headers={"X-Demo-Access": "demo"}, json={"request_id": "request-led"}).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    client.post(f"/v1/sessions/{session['session_id']}/bind", headers=auth,
                json={"conversation_id": "conv-led"})
    tool_headers = {"X-Agent-Key": "a" * 40, "X-Tool-Session": session["tool_session_capability"], "X-Conversation-Id": "conv-led"}
    started = client.post("/v1/inspections/start", headers=tool_headers, json={
        "request_id": "inspect-led", "pole_id": "P-18430", "operator_classification": "LED",
        "operator_utterance": "It looks like LED.",
    })
    assert started.status_code == 200, started.text
    iid = started.json()["inspection_id"]
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("led.png", image, "image/png")}).json()
    assessed = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                           json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-led", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessed["assessment"]["assessment_id"], "operator_utterance": "I confirm that it is LED.",
    })
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4})
    assert reconciled.status_code == 200, reconciled.text
    state = reconciled.json()
    assert state["state"] == "COMPLETED"
    assert state["decision"]["disposition"] == "NO_ACTION"
    assert state["outcome"] == "NO_ACTION_LED_CONFIRMED"
    queued = queue_asset(client, session, "18430")
    assert queued["operational"]["queue_status"] == "RESOLVED"
    assert queued["operational"]["next_step"] == "COMPLETED"
    assert vision.classification == "LED"


def test_proposal_confirmation_reserves_operation_without_claiming_external_success(harness):
    client, session, tool_headers, inspection, image, _ = harness
    iid = inspection["inspection_id"]
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("fixture.png", image, "image/png")}).json()
    assessment = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                             json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-proposal", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessment["assessment"]["assessment_id"], "operator_utterance": "I confirm.",
    })
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4}).json()
    assert reconciled["revision"] == 5
    proposal = client.post(f"/v1/inspections/{iid}/proposals", headers=tool_headers, json={
        "request_id": "proposal-001", "expected_revision": 5,
    })
    assert proposal.status_code == 201, proposal.text
    proposal = proposal.json()
    assert proposal["action_type"] == "CREATE_EXCEPTION"
    assert proposal["status"] == "ACTIVE"
    confirmed = client.post(f"/v1/proposals/{proposal['proposal_id']}/confirmation", headers=tool_headers, json={
        "expected_revision": 5, "decision": "CONFIRM", "operator_utterance": "Yes, create the review case.",
    })
    assert confirmed.status_code == 202, confirmed.text
    body = confirmed.json()
    assert body["operation"]["status"] == "QUEUED"
    assert body["inspection"]["state"] == "ACTION_PENDING"
    # No Airtable credentials are present in the test harness, so the service
    # reports a local reservation and never invents an external record ID.
    assert body["operation"]["external_reference"] is None
    queued = queue_asset(client, session, "18427")
    assert queued["operational"]["queue_status"] == "REQUEST_PENDING"
    assert queued["operational"]["next_step"] == "TRACK_REQUEST"
    assert queued["operational"]["active_operation_status"] == "QUEUED"
    repeated = client.post(f"/v1/proposals/{proposal['proposal_id']}/confirmation", headers=tool_headers, json={
        "expected_revision": 5, "decision": "CONFIRM", "operator_utterance": "Yes.",
    })
    assert repeated.status_code == 202
    assert repeated.json()["replayed"] is True


def test_existing_order_is_returned_without_creating_a_duplicate(tmp_path):
    from conftest import CapturingVision, FakeVoice

    settings = Settings(
        data_dir=tmp_path, demo_access_token="demo", agent_tool_key="a" * 40,
        session_signing_key="s" * 40, agent_id="agent_demo", vision_api_key="v" * 40,
    )
    vision = CapturingVision("LEGACY")
    business = DemoBusinessSource(existing_orders={
        "F-18429-01": {
            "external_system": "Airtable", "record_id": "rec_existing_18429",
            "display_reference": "rec_existing_18429", "status": "OPEN",
        }
    })
    client = TestClient(make_app(settings=settings, voice=FakeVoice(), vision=vision,
                                 store=Store(settings.data_dir), business=business))
    session = client.post("/v1/sessions", headers={"X-Demo-Access": "demo"},
                          json={"request_id": "request-existing"}).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    assert client.post(f"/v1/sessions/{session['session_id']}/bind", headers=auth,
                       json={"conversation_id": "conv-existing"}).status_code == 200
    tool_headers = {"X-Agent-Key": "a" * 40, "X-Tool-Session": session["tool_session_capability"],
                    "X-Conversation-Id": "conv-existing"}
    started = client.post("/v1/inspections/start", headers=tool_headers, json={
        "request_id": "inspect-existing", "pole_id": "P-18429", "operator_classification": "LEGACY",
        "operator_utterance": "It is a legacy luminaire.",
    })
    assert started.status_code == 200, started.text
    iid = started.json()["inspection_id"]
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("legacy.png", image.getvalue(), "image/png")}).json()
    assessment = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                             json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    accepted = client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-existing", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessment["assessment"]["assessment_id"],
        "operator_utterance": "I confirm that it is legacy.",
    })
    assert accepted.status_code == 200, accepted.text
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4})
    assert reconciled.status_code == 200, reconciled.text
    state = reconciled.json()
    assert state["state"] == "COMPLETED"
    assert state["decision"]["disposition"] == "EXISTING_ORDER"
    assert state["outcome"] == "EXISTING_WORK_ORDER_FOUND"
    assert state["existing_order"]["record_id"] == "rec_existing_18429"
    assert "PREPARE_ACTION" not in state["allowed_next_actions"]


def test_confirmed_eligible_replacement_finishes_only_after_external_receipt(tmp_path):
    from conftest import CapturingVision, FakeVoice

    settings = Settings(
        data_dir=tmp_path, demo_access_token="demo", agent_tool_key="a" * 40,
        session_signing_key="s" * 40, agent_id="agent_demo", vision_api_key="v" * 40,
    )
    external = SuccessfulExternal()
    client = TestClient(make_app(settings=settings, voice=FakeVoice(), vision=CapturingVision("LEGACY"),
                                 store=Store(settings.data_dir), external=external))
    session = client.post("/v1/sessions", headers={"X-Demo-Access": "demo"},
                          json={"request_id": "request-dispatch"}).json()
    auth = {"Authorization": f"Bearer {session['session_token']}"}
    assert client.post(f"/v1/sessions/{session['session_id']}/bind", headers=auth,
                       json={"conversation_id": "conv-dispatch"}).status_code == 200
    tool_headers = {"X-Agent-Key": "a" * 40, "X-Tool-Session": session["tool_session_capability"],
                    "X-Conversation-Id": "conv-dispatch"}
    started = client.post("/v1/inspections/start", headers=tool_headers, json={
        "request_id": "inspect-dispatch", "pole_id": "P-18427", "operator_classification": "LEGACY",
        "operator_utterance": "It is a legacy luminaire.",
    })
    assert started.status_code == 200, started.text
    iid = started.json()["inspection_id"]
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    receipt = client.post(f"/v1/inspections/{iid}/evidence?expected_revision=1", headers=auth,
                          files={"file": ("legacy.png", image.getvalue(), "image/png")}).json()
    assessment = client.post(f"/v1/inspections/{iid}/assessments", headers=tool_headers,
                             json={"evidence_id": receipt["evidence_id"], "expected_revision": 2}).json()
    accepted = client.post(f"/v1/inspections/{iid}/operator-responses", headers=tool_headers, json={
        "request_id": "response-dispatch", "expected_revision": 3, "kind": "ACCEPT_VISUAL",
        "assessment_id": assessment["assessment"]["assessment_id"],
        "operator_utterance": "I confirm that it is legacy.",
    })
    assert accepted.status_code == 200, accepted.text
    reconciled = client.post(f"/v1/inspections/{iid}/reconcile", headers=tool_headers,
                             json={"expected_revision": 4}).json()
    assert reconciled["decision"]["disposition"] == "REPLACEMENT"
    proposal = client.post(f"/v1/inspections/{iid}/proposals", headers=tool_headers, json={
        "request_id": "proposal-dispatch", "expected_revision": 5,
    }).json()
    confirmed = client.post(f"/v1/proposals/{proposal['proposal_id']}/confirmation", headers=tool_headers, json={
        "expected_revision": 5, "decision": "CONFIRM", "operator_utterance": "Yes, create the replacement request.",
    })
    assert confirmed.status_code == 202, confirmed.text
    body = confirmed.json()
    assert body["operation"]["status"] == "SUCCEEDED"
    assert body["operation"]["external_reference"]["record_id"] == "rec_work_18427"
    assert body["inspection"]["state"] == "COMPLETED"
    assert body["inspection"]["outcome"] == "REPLACEMENT_ORDER_CREATED"
    queued = queue_asset(client, session, "18427")
    assert queued["operational"]["queue_status"] == "REQUEST_SENT"
    assert queued["operational"]["next_step"] == "TRACK_REQUEST"
    assert queued["operational"]["active_operation_status"] == "SUCCEEDED"
    assert len(external.calls) == 1
    assert external.calls[0][1]["action_type"] == "CREATE_REPLACEMENT"
