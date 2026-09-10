import pytest

from scripts.provision_agent import (build_conversation_config, build_tool,
                                     build_test, build_workflow,
                                     load_knowledge_documents)


def test_build_tool_uses_current_toolbox_schema():
    entry = {
        "name": "inspect",
        "method": "POST",
        "path": "/v1/inspections/{inspection_id}/responses",
        "description": "Records a response.",
        "parameters": {
            "inspection_id": {"type": "string", "description": "Opaque ID."},
            "expected_revision": {"type": "integer"},
            "assessment_id": {"type": "string", "nullable": True},
        },
    }
    headers = {
        "X-Agent-Key": {"secret_id": "secret_123"},
        "X-Tool-Session": {"variable_name": "secret__lumafield_session"},
        "X-Conversation-Id": {"variable_name": "system__conversation_id"},
    }

    tool = build_tool(entry, headers, "https://demo.example")

    assert tool["type"] == "webhook"
    assert tool["api_schema"]["url"] == "https://demo.example/v1/inspections/{inspection_id}/responses"
    assert tool["api_schema"]["path_params_schema"] == {
        "inspection_id": {"type": "string", "description": "Opaque ID."},
    }
    body = tool["api_schema"]["request_body_schema"]
    assert body["required"] == ["expected_revision"]
    assert body["properties"]["assessment_id"] == {
        "type": "string",
        "description": "Value for assessment id.",
    }
    assert tool["api_schema"]["request_headers"] == headers


def test_build_tool_forwards_interruption_mode():
    entry = {
        "name": "inspect",
        "method": "GET",
        "path": "/v1/inspect",
        "description": "Reads state.",
        "parameters": {},
        "interruption_mode": "disable_during_tool",
    }
    tool = build_tool(entry, {}, "https://demo.example")
    assert tool["interruption_mode"] == "disable_during_tool"


def test_conversation_config_keeps_patient_turn_settings():
    config = {
        "conversation_config": {
            "agent": {"first_message": "Hello", "language": "en", "prompt": {
                "llm": "gpt-5.6-sol", "temperature": 0.1, "reasoning_effort": "none",
            }},
            "tts": {"model_id": "eleven_turbo_v2_5"},
            "turn": {"turn_timeout": 30, "turn_eagerness": "patient"},
            "conversation": {"max_duration_seconds": 1200},
        }
    }
    result = build_conversation_config(config, "prompt completo", ["tool_1"])
    assert result["turn"] == {"turn_timeout": 30, "turn_eagerness": "patient"}
    assert result["conversation"]["max_duration_seconds"] == 1200
    assert result["agent"]["prompt"]["tool_ids"] == ["tool_1"]
    assert result["agent"]["prompt"]["llm"] == "gpt-5.6-sol"
    assert result["agent"]["prompt"]["reasoning_effort"] == "none"


def test_knowledge_manifest_loads_versioned_documents():
    manifest = {
        "documents": [{
            "name": "LumaField — test knowledge",
            "path": "agent/knowledge-base.md",
            "usage_mode": "auto",
        }]
    }

    documents = load_knowledge_documents(manifest)

    assert documents[0]["name"] == "LumaField — test knowledge"
    assert documents[0]["usage_mode"] == "auto"
    assert "What is being reconciled" in documents[0]["content"]


def test_workflow_resolves_names_and_restricts_tools_per_node():
    template = {
        "version": 1,
        "name": "test",
        "prevent_subagent_loops": True,
        "nodes": {
            "start_node": {"type": "start", "edge_order": ["start_to_field"]},
            "field": {
                "type": "override_agent",
                "label": "FIELD",
                "tool_names": ["record_observation"],
                "edge_order": ["field_to_reconcile"],
            },
            "reconcile": {
                "type": "tool",
                "tool_names": ["reconcile_asset"],
                "edge_order": [],
            },
        },
        "edges": {
            "start_to_field": {"source": "start_node", "target": "field"},
            "field_to_reconcile": {"source": "field", "target": "reconcile"},
        },
    }

    workflow = build_workflow(template, {
        "record_observation": "tool_field",
        "reconcile_asset": "tool_reconcile",
    })

    assert "version" not in workflow and "name" not in workflow
    assert workflow["nodes"]["field"]["conversation_config"]["agent"]["prompt"]["tool_ids"] == ["tool_field"]
    assert workflow["nodes"]["reconcile"]["tools"] == [{"tool_id": "tool_reconcile"}]
    assert all("tool_names" not in node for node in workflow["nodes"].values())


def test_workflow_rejects_an_unknown_tool_name():
    with pytest.raises(RuntimeError, match="unknown tools"):
        build_workflow({
            "nodes": {"field": {"type": "override_agent", "label": "FIELD", "tool_names": ["missing"]}},
            "edges": {},
        }, {})


def test_simulation_test_resolves_tool_mock_names():
    result = build_test({
        "name": "golden path",
        "type": "simulation",
        "chat_history": [],
        "simulation_scenario": "Run the scenario.",
        "success_conditions": ["The workflow completes."],
        "simulation_max_turns": 12,
        "tool_mocks": {
            "start_inspection": [{"mock_result": "{\"identity_status\":\"FOUND\"}"}],
        },
    }, {"start_inspection": "tool_start"})

    assert result["type"] == "simulation"
    assert result["tool_mock_config"] == {
        "mocking_strategy": "selected",
        "fallback_strategy": "raise_error",
        "mocked_tool_ids": ["tool_start"],
    }
    assert result["tool_mock_overrides"] == {
        "tool_start": [{"mock_result": "{\"identity_status\":\"FOUND\"}"}],
    }
