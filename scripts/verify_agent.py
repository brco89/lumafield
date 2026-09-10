"""Qualify the live LumaField agent against its versioned manifests.

Usage:
    .venv/Scripts/python scripts/verify_agent.py
    .venv/Scripts/python scripts/verify_agent.py --golden-repeat-count 3 --repeat-count 5

The verifier reads the ElevenLabs deployment back and compares every
versioned field: tool contracts, the full conversation prompt/configuration,
Knowledge Base contents, workflow nodes/edges/stage authority, native tests,
post-call criteria and the signed webhook. It then runs three independent
execution gates: the complete catalog once, the golden simulation repeatedly,
and every critical focused test repeatedly.

No workspace state is mutated other than ElevenLabs test invocation records.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.provision_agent import (  # noqa: E402
    AGENT_KEY_SECRET_NAME,
    AGENTS_URL,
    KNOWLEDGE_BASE_URL,
    POST_CALL_WEBHOOK_NAME,
    SECRETS_URL,
    TESTING_URL,
    TOOLS_URL,
    WEBHOOKS_URL,
    build_conversation_config,
    build_test,
    build_tool,
    build_workflow,
    load_knowledge_documents,
)
from scripts.setup_airtable import load_env  # noqa: E402

INVOCATIONS_URL = "https://api.elevenlabs.io/v1/convai/test-invocations"
TERMINAL_TEST_STATUSES = {"passed", "failed", "error", "cancelled"}


class VerificationFailure(RuntimeError):
    pass


def read_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def get_json(client, url):
    response = client.get(url)
    if not 200 <= response.status_code < 300:
        raise VerificationFailure(f"GET {url} returned HTTP {response.status_code}")
    return response.json()


def get_text(client, url):
    response = client.get(url)
    if not 200 <= response.status_code < 300:
        raise VerificationFailure(f"GET {url} returned HTTP {response.status_code}")
    return response.text


def _short(value):
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return rendered if len(rendered) <= 140 else rendered[:137] + "..."


def expected_subset_mismatch(actual, expected, path="$"):
    """Return the first drift from a versioned payload, ignoring API defaults.

    ElevenLabs enriches returned objects with nullable/default fields. Dicts
    therefore require every expected key and value while allowing extra server
    fields. Lists stay exact because order and membership carry configuration
    meaning for tools, workflow edges, criteria and test mocks.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: expected object, got {_short(actual)}"
        for key, value in expected.items():
            if key not in actual:
                return f"{path}.{key}: missing"
            mismatch = expected_subset_mismatch(actual[key], value, f"{path}.{key}")
            if mismatch:
                return mismatch
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{path}: expected list, got {_short(actual)}"
        if len(actual) != len(expected):
            return f"{path}: expected {len(expected)} items, got {len(actual)}"
        for index, value in enumerate(expected):
            mismatch = expected_subset_mismatch(actual[index], value, f"{path}[{index}]")
            if mismatch:
                return mismatch
        return None
    if actual != expected:
        return f"{path}: expected {_short(expected)}, got {_short(actual)}"
    return None


def report(label, passed, detail=""):
    state = "PASS" if passed else "FAIL"
    suffix = f"  {detail}" if detail else ""
    print(f"{label:<34} {state}{suffix}")
    if not passed:
        raise VerificationFailure(f"{label}: {detail}" if detail else label)


def report_manifest(label, actual, expected, success_detail):
    mismatch = expected_subset_mismatch(actual, expected)
    report(label, mismatch is None, success_detail if mismatch is None else mismatch)


def unique_named(items, name):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise VerificationFailure(f"Expected exactly one {name!r}, found {len(matches)}")
    return matches[0]


def test_catalog(client):
    listed = get_json(client, f"{TESTING_URL}?page_size=100")
    return {
        item["id"]: item
        for item in listed.get("tests", [])
        if item.get("entity_type", "test") == "test"
    }


def invoke_tests(client, agent_id, tests, repeat_count, timeout_seconds):
    response = client.post(
        f"{AGENTS_URL}/{agent_id}/run-tests",
        json={"tests": tests, "repeat_count": repeat_count},
    )
    if not 200 <= response.status_code < 300:
        raise VerificationFailure(
            f"Native test invocation returned HTTP {response.status_code}: {response.text[:400]}"
        )
    invocation_id = response.json()["id"]
    expected_runs = len(tests) * repeat_count
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        invocation = get_json(client, f"{INVOCATIONS_URL}/{invocation_id}")
        runs = invocation.get("test_runs", [])
        if len(runs) >= expected_runs and all(
            run.get("status") in TERMINAL_TEST_STATUSES for run in runs
        ):
            return invocation
        time.sleep(2)
    raise VerificationFailure(
        f"Native tests did not finish within {timeout_seconds}s ({invocation_id})"
    )


def assert_test_run(label, invocation, expected_runs):
    runs = invocation.get("test_runs", [])
    passed = [run for run in runs if run.get("status") == "passed"]
    if len(passed) == expected_runs:
        report(label, True, f"{len(passed)}/{expected_runs}")
        return
    print(f"{label:<34} FAIL  {len(passed)}/{expected_runs}")
    for run in runs:
        if run.get("status") == "passed":
            continue
        rationale = (run.get("condition_result") or {}).get("rationale") or {}
        summary = rationale.get("summary") if isinstance(rationale, dict) else str(rationale)
        print(
            f"  - {run.get('test_name', run.get('test_id'))}: "
            f"{run.get('status')} — {summary or 'no rationale'}"
        )
    raise VerificationFailure(label)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-repeat-count", type=int, default=3)
    parser.add_argument("--repeat-count", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--skip-native-tests", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.golden_repeat_count <= 50:
        parser.error("--golden-repeat-count must be between 1 and 50")
    if not 1 <= args.repeat_count <= 50:
        parser.error("--repeat-count must be between 1 and 50")

    env = load_env()
    api_key = env.get("LUMAFIELD_ELEVENLABS_API_KEY")
    agent_id = env.get("LUMAFIELD_AGENT_ID")
    base_url = env.get("LUMAFIELD_PUBLIC_API_URL", "").rstrip("/")
    if not api_key or not agent_id:
        raise SystemExit("LUMAFIELD_ELEVENLABS_API_KEY and LUMAFIELD_AGENT_ID are required")
    if not base_url.startswith("https://"):
        raise SystemExit("LUMAFIELD_PUBLIC_API_URL must be the provisioned HTTPS origin")

    config = read_json("agent/agent-config.json")
    tools_manifest = read_json("agent/tools.json")
    workflow_manifest = read_json("agent/workflow.json")
    knowledge_manifest = read_json("agent/knowledge-base.json")
    tests_manifest = read_json("agent/tests.json")
    analysis_manifest = read_json("agent/analysis.json")
    prompt = (ROOT / "agent" / "system-prompt.md").read_text(encoding="utf-8")
    knowledge_documents = load_knowledge_documents(knowledge_manifest)

    try:
        with httpx.Client(headers={"xi-api-key": api_key}, timeout=60) as client:
            live_tools = get_json(client, f"{TOOLS_URL}?page_size=100").get("tools", [])
            live_secrets = get_json(client, f"{SECRETS_URL}?page_size=100").get("secrets", [])
            secret = unique_named(live_secrets, AGENT_KEY_SECRET_NAME)
            secret_id = secret.get("secret_id") or secret.get("id")
            if not secret_id:
                raise VerificationFailure(f"Workspace secret {AGENT_KEY_SECRET_NAME!r} has no ID")
            headers = {
                "X-Agent-Key": {"secret_id": secret_id},
                "X-Tool-Session": {"variable_name": "secret__lumafield_session"},
                "X-Conversation-Id": {"variable_name": "system__conversation_id"},
            }
            tool_ids_by_name = {}
            tool_drift = None
            for entry in tools_manifest["tools"]:
                matches = [
                    item for item in live_tools
                    if (item.get("tool_config") or {}).get("name") == entry["name"]
                ]
                if len(matches) != 1:
                    raise VerificationFailure(
                        f"Expected exactly one tool {entry['name']!r}, found {len(matches)}"
                    )
                live_tool = matches[0]
                tool_ids_by_name[entry["name"]] = live_tool["id"]
                expected_tool = build_tool(entry, headers, base_url)
                mismatch = expected_subset_mismatch(
                    live_tool.get("tool_config"), expected_tool, f"$.{entry['name']}"
                )
                tool_drift = tool_drift or mismatch
            report(
                "Webhook tool contracts",
                tool_drift is None,
                f"{len(tool_ids_by_name)} exact" if tool_drift is None else tool_drift,
            )

            tool_ids = [tool_ids_by_name[item["name"]] for item in tools_manifest["tools"]]
            agent = get_json(client, f"{AGENTS_URL}/{agent_id}")
            expected_conversation = build_conversation_config(config, prompt, tool_ids)
            report_manifest(
                "Provisioned conversation config",
                {"name": agent.get("name"), "conversation_config": agent.get("conversation_config")},
                {"name": config["name"], "conversation_config": expected_conversation},
                f"{config['conversation_config']['agent']['prompt']['llm']} · "
                f"{config['conversation_config']['agent']['language']} · full prompt",
            )

            live_documents = get_json(
                client, f"{KNOWLEDGE_BASE_URL}?page_size=100"
            ).get("documents", [])
            document_locators = {}
            kb_drift = None
            for expected_document in knowledge_documents:
                live_document = unique_named(live_documents, expected_document["name"])
                live_content = get_text(
                    client,
                    f"{KNOWLEDGE_BASE_URL}/{live_document['id']}/content",
                )
                if live_content.replace("\r\n", "\n").strip() != expected_document[
                    "content"
                ].replace("\r\n", "\n").strip():
                    kb_drift = kb_drift or f"content drift in {expected_document['name']!r}"
                document_locators[expected_document["name"]] = {
                    "type": "text",
                    "name": expected_document["name"],
                    "id": live_document["id"],
                    "usage_mode": expected_document["usage_mode"],
                }
            live_document_names = {item.get("name") for item in live_documents}
            retired_present = set(knowledge_manifest.get("retired_documents", [])) & live_document_names
            if retired_present:
                kb_drift = kb_drift or f"retired documents still active: {sorted(retired_present)}"
            report(
                "Knowledge Base contents",
                kb_drift is None,
                (
                    f"{len(document_locators)} exact · "
                    f"{len(knowledge_manifest.get('retired_documents', []))} retired absent"
                    if kb_drift is None else kb_drift
                ),
            )

            expected_workflow = build_workflow(
                workflow_manifest, tool_ids_by_name, document_locators
            )
            report_manifest(
                "Workflow definition",
                agent.get("workflow"),
                expected_workflow,
                f"{len(expected_workflow['nodes'])} nodes · edges/prompts/authority exact",
            )

            attached = (
                agent.get("platform_settings", {})
                .get("testing", {})
                .get("attached_tests", [])
            )
            catalog = test_catalog(client)
            attached_by_id = {item["test_id"]: item for item in attached}
            expected_test_ids = set()
            attached_by_name = {}
            test_drift = None
            for entry in tests_manifest["tests"]:
                matches = [item for item in catalog.values() if item.get("name") == entry["name"]]
                if len(matches) != 1:
                    test_drift = test_drift or (
                        f"expected one native test {entry['name']!r}, found {len(matches)}"
                    )
                    continue
                summary = matches[0]
                test_id = summary["id"]
                expected_test_ids.add(test_id)
                attachment = attached_by_id.get(test_id)
                if not attachment:
                    test_drift = test_drift or f"{entry['name']!r} is not attached"
                    continue
                if attachment.get("workflow_node_id") != entry["workflow_node_id"]:
                    test_drift = test_drift or (
                        f"{entry['name']!r} attached to {attachment.get('workflow_node_id')!r}, "
                        f"expected {entry['workflow_node_id']!r}"
                    )
                expected_test = build_test(entry, tool_ids_by_name)
                live_test = get_json(client, f"{TESTING_URL}/{test_id}")
                mismatch = expected_subset_mismatch(
                    live_test, expected_test, f"$.{entry['name']}"
                )
                test_drift = test_drift or mismatch
                attached_by_name[entry["name"]] = attachment
            if set(attached_by_id) != expected_test_ids:
                test_drift = test_drift or "attached native test IDs differ from the manifest"
            report(
                "Native test definitions",
                test_drift is None,
                f"{len(expected_test_ids)} exact + attached" if test_drift is None else test_drift,
            )

            platform_settings = agent.get("platform_settings", {})
            expected_analysis = {
                "evaluation": analysis_manifest["evaluation"],
                "summary_language": analysis_manifest["summary_language"],
                "analysis_llm": analysis_manifest["analysis_llm"],
            }
            report_manifest(
                "Post-call evaluation config",
                platform_settings,
                expected_analysis,
                f"{len(analysis_manifest['evaluation']['criteria'])} criteria exact",
            )

            webhooks = get_json(client, WEBHOOKS_URL).get("webhooks", [])
            live_webhook = unique_named(webhooks, POST_CALL_WEBHOOK_NAME)
            expected_webhook = {
                "name": POST_CALL_WEBHOOK_NAME,
                "webhook_url": base_url + config["provisioning"]["post_call_webhook"],
                "is_disabled": False,
                "is_auto_disabled": False,
                "auth_type": "hmac",
                "retry_enabled": True,
            }
            webhook_drift = expected_subset_mismatch(live_webhook, expected_webhook)
            expected_override = {
                "post_call_webhook_id": live_webhook["webhook_id"],
                "events": ["transcript"],
                "transcript_format": "json",
                "send_audio": False,
            }
            live_override = (
                platform_settings.get("workspace_overrides", {})
                .get("webhooks", {})
            )
            webhook_drift = webhook_drift or expected_subset_mismatch(
                live_override, expected_override, "$.agent_webhook_override"
            )
            report(
                "Signed post-call webhook",
                webhook_drift is None,
                "HMAC · URL/events/attachment exact" if webhook_drift is None else webhook_drift,
            )

            if args.skip_native_tests:
                return

            ordered = [attached_by_name[item["name"]] for item in tests_manifest["tests"]]
            smoke = invoke_tests(client, agent_id, ordered, 1, args.timeout_seconds)
            assert_test_run("Native catalog smoke", smoke, len(ordered))

            golden_names = [
                item["name"] for item in tests_manifest["tests"] if item.get("golden")
            ]
            if not golden_names:
                raise VerificationFailure("No test is marked golden in agent/tests.json")
            golden = [attached_by_name[name] for name in golden_names]
            golden_run = invoke_tests(
                client,
                agent_id,
                golden,
                args.golden_repeat_count,
                args.timeout_seconds,
            )
            assert_test_run(
                "Golden path simulation",
                golden_run,
                len(golden) * args.golden_repeat_count,
            )

            critical_names = [
                item["name"] for item in tests_manifest["tests"] if item.get("critical")
            ]
            if not critical_names:
                raise VerificationFailure("No test is marked critical in agent/tests.json")
            critical = [attached_by_name[name] for name in critical_names]
            stability = invoke_tests(
                client,
                agent_id,
                critical,
                args.repeat_count,
                args.timeout_seconds,
            )
            assert_test_run(
                "Critical stability",
                stability,
                len(critical) * args.repeat_count,
            )
    except VerificationFailure as exc:
        print(f"\nAgent qualification failed: {exc}")
        raise SystemExit(1) from None

    print("\nAgent qualification passed.")


if __name__ == "__main__":
    main()
