"""One-shot Airtable provisioning for the LumaField demo.

Reads LUMAFIELD_AIRTABLE_TOKEN (and BASE_ID if already set) from .env,
discovers or confirms the base, creates the "LumaField Actions" table with
the exact field names the adapter writes, and proves the write path with a
real create/read/delete round-trip through AirtableGateway itself.

Usage: .venv/Scripts/python scripts/setup_airtable.py
"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FIELDS = [
    "External Operation ID",  # primary
    "Action Type", "Inspection ID", "Fixture ID", "Pole ID",
    "Contract ID", "Decision ID", "Payload Hash", "Effect Summary", "Status",
]
TABLE_NAME = "LumaField Actions"


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    env = load_env()
    token = env.get("LUMAFIELD_AIRTABLE_TOKEN", "")
    if not token:
        sys.exit("LUMAFIELD_AIRTABLE_TOKEN is missing from .env")
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=20, headers=headers) as client:

        # 1. Discover the base unless BASE_ID is already pinned.
        base_id = env.get("LUMAFIELD_AIRTABLE_BASE_ID", "")
        if not base_id:
            listing = client.get("https://api.airtable.com/v0/meta/bases")
            if listing.status_code == 403:
                sys.exit("The token cannot list bases (base.creator is missing). Open the base in Airtable "
                         "and set LUMAFIELD_AIRTABLE_BASE_ID in .env to the appXXXXXXXX value from the URL.")
            listing.raise_for_status()
            bases = listing.json().get("bases", [])
            named = [b for b in bases if b["name"].lower() == "lumafield"]
            if len(named) == 1:
                base_id = named[0]["id"]
            elif len(bases) == 1:
                base_id = bases[0]["id"]
            else:
                options = ", ".join(f'{b["name"]} ({b["id"]})' for b in bases)
                sys.exit(f"Multiple bases found; set LUMAFIELD_AIRTABLE_BASE_ID in .env. Found: {options}")
        print(f"[1/4] Base: {base_id}")

        # 2. Ensure the table exists with exactly the fields the adapter writes.
        tables = client.get(f"https://api.airtable.com/v0/meta/bases/{base_id}/tables").json().get("tables", [])
        existing = next((t for t in tables if t["name"] == TABLE_NAME), None)
        if existing:
            missing = [f for f in FIELDS if f not in {field["name"] for field in existing["fields"]}]
            for field in missing:
                response = client.post(
                    f"https://api.airtable.com/v0/meta/bases/{base_id}/tables/{existing['id']}/fields",
                    json={"name": field, "type": "singleLineText"})
                response.raise_for_status()
                print(f"      field created: {field}")
            table_id = existing["id"]
        else:
            created = client.post(f"https://api.airtable.com/v0/meta/bases/{base_id}/tables", json={
                "name": TABLE_NAME,
                "fields": [{"name": f, "type": "singleLineText"} for f in FIELDS],
            })
            created.raise_for_status()
            table_id = created.json()["id"]
            print(f"      table created with {len(FIELDS)} fields")
        print(f"[2/4] Table '{TABLE_NAME}' ready ({table_id})")

        # 3. Prove the real write path through the project's own adapter.
        sys.path.insert(0, str(ROOT / "backend"))
        from app.adapters.airtable import AirtableGateway
        gateway = AirtableGateway(token, base_id, TABLE_NAME)
        payload = {
            "action_type": "VALIDATION_ONLY", "inspection_id": "ins_setup_check",
            "fixture_id": None, "pole_id": None, "contract_id": None,
            "decision_id": None, "payload_hash": "setup",
            "effect_summary": "Setup validation row; deleted immediately.",
        }
        reference = gateway.dispatch("op_setup_check", payload)
        record_id = reference["record_id"]
        looked_up = gateway.lookup("op_setup_check")
        assert looked_up and looked_up["record_id"] == record_id
        print(f"[3/4] Real write + read passed: {record_id}")

        # 4. Remove the validation row; leave the table empty for the demo.
        deleted = client.delete(f"https://api.airtable.com/v0/{base_id}/{TABLE_NAME}/{record_id}")
        deleted.raise_for_status()
        print("[4/4] Validation row removed; table is clean")

    if env.get("LUMAFIELD_AIRTABLE_BASE_ID") != base_id:
        env_path, text = ROOT / ".env", (ROOT / ".env").read_text(encoding="utf-8")
        updated = text.replace("LUMAFIELD_AIRTABLE_BASE_ID=",
                               f"LUMAFIELD_AIRTABLE_BASE_ID={base_id}", 1)
        env_path.write_text(updated, encoding="utf-8")
        print(f".env updated with LUMAFIELD_AIRTABLE_BASE_ID={base_id}")
    print("Airtable is ready for the demo.")


if __name__ == "__main__":
    main()
