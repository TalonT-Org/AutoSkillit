"""Shared contract builders for recipe invocation-binding tests."""


def _manifest() -> dict[str, object]:
    return {
        "skills": {
            "dry-walkthrough": {
                "inputs": [
                    {"name": "plan_path", "type": "file_path", "required": True},
                    {"name": "issue_url", "type": "string", "required": True},
                    {"name": "optional_note", "type": "string"},
                    {"name": "audit_cycle_path", "type": "file_path", "required": True},
                    {
                        "name": "plan_disposition_path",
                        "type": "file_path",
                        "required": True,
                    },
                    {"name": "enabled", "type": "boolean"},
                    {"name": "round", "type": "integer"},
                ]
            }
        }
    }


def _required_inputs() -> dict[str, str]:
    return {
        "plan_path": "/tmp/plans/current plan.md",
        "issue_url": "https://example.test/issues/42?x=a&y=b",
        "audit_cycle_path": "/tmp/audit/cycle.json",
        "plan_disposition_path": "/tmp/audit/plan disposition.json",
    }
