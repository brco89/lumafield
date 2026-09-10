"""Provision the LumaField agent in the ElevenLabs workspace via the API.

Builds the agent from agent/system-prompt.md, agent/agent-config.json,
agent/tools.json, agent/workflow.json, agent/knowledge-base.json and its
referenced documents, agent/tests.json and agent/analysis.json. Webhook tools,
domain knowledge documents and native agent tests are synchronized before being attached to the agent. The static agent
key is stored as an ElevenLabs workspace secret; the per-session capability
and conversation ID come from dynamic variables. Writes the returned agent_id
back into .env.

Usage: .venv/Scripts/python scripts/provision_agent.py [--base-url URL] [--name NAME]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.setup_airtable import load_env  # noqa: E402

CREATE_URL = "https://api.elevenlabs.io/v1/convai/agents/create"
AGENTS_URL = "https://api.elevenlabs.io/v1/convai/agents"
TOOLS_URL = "https://api.elevenlabs.io/v1/convai/tools"
SECRETS_URL = "https://api.elevenlabs.io/v1/convai/secrets"
KNOWLEDGE_BASE_URL = "https://api.elevenlabs.io/v1/convai/knowledge-base"
TESTING_URL = "https://api.elevenlabs.io/v1/convai/agent-testing"
WEBHOOKS_URL = "https://api.elevenlabs.io/v1/workspace/webhooks"
AGENT_KEY_SECRET_NAME = "LumaField Agent Tool Key"
POST_CALL_WEBHOOK_NAME = "LumaField post-call audit"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def build_tool(entry: dict, headers: dict, base_url: str) -> dict:
    path = entry["path"]
    path_names = PLACEHOLDER.findall(path)
    properties, required = {}, []
    for name, spec in entry["parameters"].items():
        spec = dict(spec)
        # Optionality is represented by omission from `required`. ElevenLabs'
        # literal parameter schema accepts scalar types, not a JSON Schema
        # union such as ["string", "null"].
        optional = spec.pop("nullable", False)
        # Toolbox parameters must declare who supplies the value. A description
        # means the LLM supplies it; use a readable fallback for terse manifests.
        spec.setdefault("description", f"Value for {name.replace('_', ' ')}.")
        properties[name] = spec
        if not optional and name not in path_names:
            required.append(name)
    api_schema = {
        "url": base_url.rstrip("/") + path,
        "method": entry["method"],
        "request_headers": headers,
    }
    if path_names:
        api_schema["path_params_schema"] = {n: properties[n] for n in path_names}
    body_names = [n for n in properties if n not in path_names]
    if body_names and entry["method"] != "GET":
        api_schema["request_body_schema"] = {"type": "object",
                                             "properties": {n: properties[n] for n in body_names},
                                             "required": required}
    tool = {
        "type": "webhook",
        "name": entry["name"],
        "description": entry["description"],
        "response_timeout_secs": 45,
        "tool_error_handling_mode": "passthrough",
        "api_schema": api_schema,
    }
    for option in ("interruption_mode", "pre_tool_speech", "tool_call_sound"):
        if option in entry:
            tool[option] = entry[option]
    return tool


def build_conversation_config(config: dict, prompt: str, tool_ids: list[str]) -> dict:
    source = config["conversation_config"]
    agent = source["agent"]
    result = {
        "agent": {
            "first_message": agent["first_message"],
            "language": agent["language"],
            "prompt": {
                "prompt": prompt,
                "llm": agent["prompt"]["llm"],
                "temperature": agent["prompt"]["temperature"],
                "tool_ids": tool_ids,
            },
        },
        # Non-English agents require a multilingual TTS model; the API
        # default (eleven_flash_v2) is English-only and is rejected.
        "tts": source["tts"],
    }
    if "reasoning_effort" in agent["prompt"]:
        result["agent"]["prompt"]["reasoning_effort"] = agent["prompt"]["reasoning_effort"]
    for section in ("turn", "conversation"):
        if section in source:
            result[section] = source[section]
    return result


def build_workflow(
    template: dict,
    tool_ids_by_name: dict[str, str],
    knowledge_bases_by_name: dict[str, dict] | None = None,
) -> dict:
    """Resolve stable tool and knowledge names into workspace-specific locators."""
    workflow = json.loads(json.dumps(template))
    knowledge_bases_by_name = knowledge_bases_by_name or {}
    workflow.pop("version", None)
    workflow.pop("name", None)
    for node_id, node in workflow["nodes"].items():
        knowledge_names = node.pop("knowledge_base_names", None)
        if knowledge_names is not None:
            missing_knowledge = [name for name in knowledge_names if name not in knowledge_bases_by_name]
            if missing_knowledge:
                raise RuntimeError(
                    f"Workflow node {node_id!r} references unknown knowledge bases: {missing_knowledge}"
                )
            node["additional_knowledge_base"] = [
                knowledge_bases_by_name[name] for name in knowledge_names
            ]
        tool_names = node.pop("tool_names", None)
        if tool_names is None:
            continue
        missing = [name for name in tool_names if name not in tool_ids_by_name]
        if missing:
            raise RuntimeError(f"Workflow node {node_id!r} references unknown tools: {missing}")
        resolved = [tool_ids_by_name[name] for name in tool_names]
        if node["type"] == "tool":
            node["tools"] = [{"tool_id": tool_id} for tool_id in resolved]
        elif node["type"] == "override_agent":
            conversation = node.setdefault("conversation_config", {})
            agent = conversation.setdefault("agent", {})
            prompt = agent.setdefault("prompt", {})
            prompt["tool_ids"] = resolved
        elif resolved:
            raise RuntimeError(f"Workflow node {node_id!r} cannot own tools")
    return workflow


def request_json(client: httpx.Client, method: str, url: str, *, body=None) -> dict:
    response = client.request(method, url, json=body)
    if not 200 <= response.status_code < 300:
        print(response.status_code, response.text[:800])
        raise RuntimeError(f"ElevenLabs {method} {url} failed")
    return response.json()


def sync_agent_key_secret(client: httpx.Client, value: str) -> str:
    listed = request_json(client, "GET", SECRETS_URL + "?page_size=100")
    matches = [secret for secret in listed.get("secrets", [])
               if secret.get("name") == AGENT_KEY_SECRET_NAME]
    if len(matches) > 1:
        raise RuntimeError(f"More than one secret is named {AGENT_KEY_SECRET_NAME!r}")
    payload = {"type": "update" if matches else "new",
               "name": AGENT_KEY_SECRET_NAME, "value": value}
    if matches:
        secret_id = matches[0]["secret_id"]
        result = request_json(client, "PATCH", f"{SECRETS_URL}/{secret_id}", body=payload)
        print(f"Secret updated: {AGENT_KEY_SECRET_NAME}")
    else:
        result = request_json(client, "POST", SECRETS_URL, body=payload)
        print(f"Secret created: {AGENT_KEY_SECRET_NAME}")
    return result["secret_id"]


def sync_tools(client: httpx.Client, manifest: dict, base_url: str, secret_id: str) -> dict[str, str]:
    listed = request_json(client, "GET", TOOLS_URL + "?page_size=100")
    by_name = {}
    for tool in listed.get("tools", []):
        name = (tool.get("tool_config") or {}).get("name")
        if name:
            by_name.setdefault(name, []).append(tool)

    headers = {
        "X-Agent-Key": {"secret_id": secret_id},
        "X-Tool-Session": {"variable_name": "secret__lumafield_session"},
        "X-Conversation-Id": {"variable_name": "system__conversation_id"},
    }
    tool_ids = {}
    for entry in manifest["tools"]:
        accepted_names = {entry["name"], *entry.get("previous_names", [])}
        matches = [
            tool
            for accepted_name in accepted_names
            for tool in by_name.get(accepted_name, [])
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"More than one tool matches {entry['name']!r}: "
                f"{sorted(accepted_names)}"
            )
        payload = {"tool_config": build_tool(entry, headers, base_url)}
        if matches:
            tool_id = matches[0]["id"]
            result = request_json(client, "PATCH", f"{TOOLS_URL}/{tool_id}", body=payload)
            print(f"Tool updated: {entry['name']}")
        else:
            result = request_json(client, "POST", TOOLS_URL, body=payload)
            print(f"Tool created: {entry['name']}")
        tool_ids[entry["name"]] = result["id"]
    return tool_ids


def load_knowledge_documents(manifest: dict) -> list[dict]:
    documents = []
    seen_names = set()
    for entry in manifest.get("documents", []):
        name = entry["name"].strip()
        if not name or name in seen_names:
            raise RuntimeError(f"Invalid or duplicate Knowledge Base document name: {name!r}")
        seen_names.add(name)
        path = (ROOT / entry["path"]).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            raise RuntimeError(f"Knowledge Base document is outside the repository: {path}") from None
        if not path.is_file():
            raise RuntimeError(f"Knowledge Base document not found: {path}")
        usage_mode = entry.get("usage_mode", "auto")
        if usage_mode not in {"auto", "prompt"}:
            raise RuntimeError(f"Invalid Knowledge Base mode for {name!r}: {usage_mode!r}")
        documents.append({
            "name": name,
            "content": path.read_text(encoding="utf-8"),
            "usage_mode": usage_mode,
        })
    if not documents:
        raise RuntimeError("The Knowledge Base manifest contains no documents")
    return documents


def sync_knowledge_base(
    client: httpx.Client,
    documents: list[dict],
    retired_names: list[str] | None = None,
) -> dict[str, dict]:
    listed = request_json(client, "GET", f"{KNOWLEDGE_BASE_URL}?page_size=100")
    existing_by_name: dict[str, list[dict]] = {}
    for document in listed.get("documents", []):
        existing_by_name.setdefault(document.get("name", ""), []).append(document)

    active_names = {entry["name"] for entry in documents}
    retired_names = retired_names or []
    overlap = active_names.intersection(retired_names)
    if overlap:
        raise RuntimeError(f"Active documents are also marked as retired: {sorted(overlap)}")
    for name in retired_names:
        matches = existing_by_name.get(name, [])
        if len(matches) > 1:
            raise RuntimeError(f"More than one retired document is named {name!r}")
        if not matches:
            continue
        response = client.delete(f"{KNOWLEDGE_BASE_URL}/{matches[0]['id']}")
        if not 200 <= response.status_code < 300:
            print(response.status_code, response.text[:800])
            raise RuntimeError(f"Could not retire Knowledge Base document {name!r}")
        print(f"Knowledge Base retired: {name}")

    synchronized = {}
    for entry in documents:
        name = entry["name"]
        matches = existing_by_name.get(name, [])
        if len(matches) > 1:
            raise RuntimeError(f"More than one document is named {name!r}")
        if matches:
            document_id = matches[0]["id"]
            request_json(
                client,
                "PATCH",
                f"{KNOWLEDGE_BASE_URL}/{document_id}",
                body={"name": name, "content": entry["content"]},
            )
            print(f"Knowledge Base updated: {name}")
        else:
            created = request_json(
                client,
                "POST",
                f"{KNOWLEDGE_BASE_URL}/text",
                body={"name": name, "text": entry["content"]},
            )
            document_id = created["id"]
            print(f"Knowledge Base created: {name}")
        synchronized[name] = {
            "type": "text",
            "name": name,
            "id": document_id,
            "usage_mode": entry["usage_mode"],
        }
    return synchronized


def build_test(entry: dict, tool_ids_by_name: dict[str, str]) -> dict:
    payload = {
        "name": entry["name"],
        "type": entry["type"],
        "chat_history": entry.get("chat_history", []),
    }
    if "dynamic_variables" in entry:
        payload["dynamic_variables"] = entry["dynamic_variables"]
    if entry["type"] == "llm":
        for key in ("success_condition", "success_examples", "failure_examples"):
            if key in entry:
                payload[key] = entry[key]
        return payload
    if entry["type"] == "simulation":
        for key in (
            "success_condition", "success_conditions", "simulation_scenario",
            "simulation_max_turns", "simulation_environment", "evaluation_model",
            "simulated_user_model",
        ):
            if key in entry:
                payload[key] = entry[key]
        mocks = entry.get("tool_mocks", {})
        missing = [name for name in mocks if name not in tool_ids_by_name]
        if missing:
            raise RuntimeError(f"Test {entry['name']!r} references unknown mocks: {missing}")
        if mocks:
            mocked_ids = [tool_ids_by_name[name] for name in mocks]
            payload["tool_mock_config"] = {
                "mocking_strategy": "selected",
                "fallback_strategy": "raise_error",
                "mocked_tool_ids": mocked_ids,
            }
            payload["tool_mock_overrides"] = {
                tool_ids_by_name[name]: responses for name, responses in mocks.items()
            }
        return payload
    if entry["type"] != "tool":
        raise RuntimeError(f"Unsupported test type: {entry['type']!r}")
    tool_name = entry["tool_name"]
    if tool_name not in tool_ids_by_name:
        raise RuntimeError(f"Test {entry['name']!r} references an unknown tool: {tool_name}")
    payload["tool_call_parameters"] = {
        "referenced_tool": {"id": tool_ids_by_name[tool_name], "type": "webhook"},
        "parameters": entry.get("parameters", []),
        "verify_absence": entry.get("verify_absence", False),
    }
    payload["check_any_tool_matches"] = False
    return payload


def sync_tests(
    client: httpx.Client,
    manifest: dict,
    tool_ids_by_name: dict[str, str],
) -> list[dict]:
    listed = request_json(client, "GET", f"{TESTING_URL}?page_size=100")
    by_name = {}
    for test in listed.get("tests", []):
        if test.get("entity_type", "test") == "test":
            by_name.setdefault(test.get("name"), []).append(test)
    attached = []
    for entry in manifest["tests"]:
        matches = by_name.get(entry["name"], [])
        if len(matches) > 1:
            raise RuntimeError(f"More than one test is named {entry['name']!r}")
        payload = build_test(entry, tool_ids_by_name)
        if matches:
            test_id = matches[0]["id"]
            request_json(client, "PUT", f"{TESTING_URL}/{test_id}", body=payload)
            print(f"Test updated: {entry['name']}")
        else:
            result = request_json(client, "POST", f"{TESTING_URL}/create", body=payload)
            test_id = result["id"]
            print(f"Test created: {entry['name']}")
        attached.append({"test_id": test_id, "workflow_node_id": entry["workflow_node_id"]})
    return attached


def set_env_value(path: Path, key: str, value: str):
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(current):
        updated = pattern.sub(f"{key}={value}", current, count=1)
    else:
        separator = "" if not current or current.endswith("\n") else "\n"
        updated = f"{current}{separator}{key}={value}\n"
    path.write_text(updated, encoding="utf-8")


def sync_post_call_webhook(
    client: httpx.Client,
    base_url: str,
    configured_secret: str,
) -> tuple[str, str, bool]:
    desired_url = base_url.rstrip("/") + "/v1/webhooks/elevenlabs/post-call"
    listed = request_json(client, "GET", WEBHOOKS_URL)
    matches = [item for item in listed.get("webhooks", [])
               if item.get("name") == POST_CALL_WEBHOOK_NAME]
    if len(matches) > 1:
        raise RuntimeError(f"More than one webhook is named {POST_CALL_WEBHOOK_NAME!r}")
    if matches:
        webhook = matches[0]
        if webhook.get("webhook_url") == desired_url and configured_secret:
            request_json(
                client,
                "PATCH",
                f"{WEBHOOKS_URL}/{webhook['webhook_id']}",
                body={
                    "is_disabled": False,
                    "name": POST_CALL_WEBHOOK_NAME,
                    "retry_enabled": True,
                },
            )
            print(f"Post-call webhook verified: {POST_CALL_WEBHOOK_NAME}")
            return webhook["webhook_id"], configured_secret, False
        # Workspace webhook URLs and HMAC secrets cannot be recovered through
        # the API. Retire the old registration without deleting its history,
        # then create a fresh canonical webhook for the current public origin.
        retired_name = f"{POST_CALL_WEBHOOK_NAME} (retired {webhook['webhook_id'][-6:]})"
        request_json(
            client,
            "PATCH",
            f"{WEBHOOKS_URL}/{webhook['webhook_id']}",
            body={"is_disabled": True, "name": retired_name, "retry_enabled": False},
        )
        print(f"Previous post-call webhook disabled: {retired_name}")
    result = request_json(
        client,
        "POST",
        WEBHOOKS_URL,
        body={
            "settings": {
                "auth_type": "hmac",
                "name": POST_CALL_WEBHOOK_NAME,
                "webhook_url": desired_url,
            }
        },
    )
    secret = result.get("webhook_secret")
    if not secret:
        raise RuntimeError("ElevenLabs did not return the new webhook secret")
    request_json(
        client,
        "PATCH",
        f"{WEBHOOKS_URL}/{result['webhook_id']}",
        body={
            "is_disabled": False,
            "name": POST_CALL_WEBHOOK_NAME,
            "retry_enabled": True,
        },
    )
    print(f"Post-call webhook created: {POST_CALL_WEBHOOK_NAME}")
    return result["webhook_id"], secret, True


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    env = load_env()
    api_key = env["LUMAFIELD_ELEVENLABS_API_KEY"]
    base_url = args.base_url or env.get("LUMAFIELD_PUBLIC_API_URL", "")
    if not api_key:
        sys.exit("LUMAFIELD_ELEVENLABS_API_KEY is missing from .env")
    if not base_url.startswith("https://"):
        sys.exit("LUMAFIELD_PUBLIC_API_URL is missing or invalid; open the tunnel or pass --base-url")

    manifest = json.loads((ROOT / "agent" / "tools.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "agent" / "agent-config.json").read_text(encoding="utf-8"))
    workflow_template = json.loads((ROOT / "agent" / "workflow.json").read_text(encoding="utf-8"))
    tests_manifest = json.loads((ROOT / "agent" / "tests.json").read_text(encoding="utf-8"))
    analysis = json.loads((ROOT / "agent" / "analysis.json").read_text(encoding="utf-8"))
    knowledge_manifest = json.loads((ROOT / "agent" / "knowledge-base.json").read_text(encoding="utf-8"))
    knowledge_documents = load_knowledge_documents(knowledge_manifest)
    prompt = (ROOT / "agent" / "system-prompt.md").read_text(encoding="utf-8")

    with httpx.Client(headers={"xi-api-key": api_key}, timeout=60) as client:
        webhook_id, webhook_secret, webhook_secret_rotated = sync_post_call_webhook(
            client, base_url, env.get("LUMAFIELD_ELEVENLABS_WEBHOOK_SECRET", "")
        )
        if webhook_secret_rotated or not env.get("LUMAFIELD_ELEVENLABS_WEBHOOK_SECRET"):
            set_env_value(ROOT / ".env", "LUMAFIELD_ELEVENLABS_WEBHOOK_SECRET", webhook_secret)
            print(".env updated with the post-call HMAC secret")
        secret_id = sync_agent_key_secret(client, env["LUMAFIELD_AGENT_TOOL_KEY"])
        tool_ids_by_name = sync_tools(client, manifest, base_url, secret_id)
        knowledge_bases_by_name = sync_knowledge_base(
            client,
            knowledge_documents,
            knowledge_manifest.get("retired_documents", []),
        )
        attached_tests = sync_tests(client, tests_manifest, tool_ids_by_name)
        tool_ids = list(tool_ids_by_name.values())
        body = {
            "name": args.name or config["name"],
            "conversation_config": build_conversation_config(config, prompt, tool_ids),
            "workflow": build_workflow(
                workflow_template, tool_ids_by_name, knowledge_bases_by_name
            ),
            "platform_settings": {
                # This workspace has already been migrated to analysis-items.
                # Explicit null keeps the agent on the supported prompt-criteria
                # representation that is versioned in agent/analysis.json.
                "analysis_items": None,
                "evaluation": analysis["evaluation"],
                "summary_language": analysis["summary_language"],
                "analysis_llm": analysis["analysis_llm"],
                "testing": {"attached_tests": attached_tests},
                "workspace_overrides": {
                    "webhooks": {
                        "post_call_webhook_id": webhook_id,
                        "events": ["transcript"],
                        "transcript_format": "json",
                        "send_audio": False,
                    }
                },
            },
        }
        agent_id = env.get("LUMAFIELD_AGENT_ID", "")
        if agent_id:
            # The workspace link is a deliverable: keep the same agent and only
            # re-point its tools (e.g. after a tunnel restart changed the URL).
            request_json(client, "PATCH", f"{AGENTS_URL}/{agent_id}", body=body)
            print(f"Agent updated: {agent_id}")
        else:
            result = request_json(client, "POST", CREATE_URL, body=body)
            agent_id = result["agent_id"]
            print(f"Agent created: {agent_id}")

        current = request_json(client, "GET", f"{AGENTS_URL}/{agent_id}")
        attached = current["conversation_config"]["agent"]["prompt"].get("tool_ids", [])
        if attached != tool_ids:
            raise RuntimeError("The agent did not return exactly the provisioned tools")
        returned_nodes = (current.get("workflow") or {}).get("nodes", {})
        if set(returned_nodes) != set(body["workflow"]["nodes"]):
            raise RuntimeError("The agent did not return exactly the provisioned Workflow nodes")
        returned_tests = current.get("platform_settings", {}).get("testing", {}).get("attached_tests", [])
        if {item["test_id"] for item in returned_tests} != {item["test_id"] for item in attached_tests}:
            raise RuntimeError("The agent did not return exactly the provisioned tests")
        returned_criteria = current.get("platform_settings", {}).get("evaluation", {}).get("criteria", [])
        if {item["id"] for item in returned_criteria} != {
            item["id"] for item in analysis["evaluation"]["criteria"]
        }:
            raise RuntimeError("The agent did not return exactly the provisioned evaluation criteria")

    env_path, text = ROOT / ".env", (ROOT / ".env").read_text(encoding="utf-8")
    if f"LUMAFIELD_AGENT_ID={agent_id}" not in text:
        updated = re.sub(r"LUMAFIELD_AGENT_ID=.*", f"LUMAFIELD_AGENT_ID={agent_id}", text, count=1)
        env_path.write_text(updated, encoding="utf-8")
        print(".env updated with LUMAFIELD_AGENT_ID")
    print(
        f"Tools: {len(tool_ids)} · workflow nodes: {len(body['workflow']['nodes'])} · "
        f"KB: {len(knowledge_bases_by_name)} · tests: {len(attached_tests)} · evals: "
        f"{len(analysis['evaluation']['criteria'])} · base: {base_url}"
    )


if __name__ == "__main__":
    main()
