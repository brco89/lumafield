from scripts.verify_agent import expected_subset_mismatch


def test_expected_subset_accepts_server_defaults_but_keeps_lists_exact():
    actual = {
        "nodes": {
            "field": {
                "label": "02–03 RECORD + FIELD",
                "edge_order": ["field_to_evidence"],
                "server_default": None,
            }
        },
        "server_metadata": {"revision": 3},
    }
    expected = {
        "nodes": {
            "field": {
                "label": "02–03 RECORD + FIELD",
                "edge_order": ["field_to_evidence"],
            }
        }
    }

    assert expected_subset_mismatch(actual, expected) is None


def test_expected_subset_reports_nested_workflow_drift():
    actual = {
        "edges": {
            "field_to_evidence": {
                "source": "field",
                "target": "resolve",
            }
        }
    }
    expected = {
        "edges": {
            "field_to_evidence": {
                "source": "field",
                "target": "evidence",
            }
        }
    }

    mismatch = expected_subset_mismatch(actual, expected)

    assert mismatch == '$.edges.field_to_evidence.target: expected "evidence", got "resolve"'


def test_expected_subset_rejects_extra_list_members():
    mismatch = expected_subset_mismatch(
        {"tool_ids": ["tool_field", "tool_unexpected"]},
        {"tool_ids": ["tool_field"]},
    )

    assert mismatch == "$.tool_ids: expected 1 items, got 2"
