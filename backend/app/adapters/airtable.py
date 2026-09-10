from urllib.parse import quote

import httpx


class ExternalNotConfigured(Exception):
    pass


class ExternalOutcomeUnknown(Exception):
    pass


class ExternalDefiniteFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ExternalIntegrityError(Exception):
    pass


class AirtableGateway:
    """Airtable action adapter; business policy remains in the domain service."""

    def __init__(self, token, base_id, table_name, transport=None):
        self.token, self.base_id, self.table_name, self.transport = token, base_id, table_name, transport

    @property
    def configured(self):
        return bool(self.token and self.base_id and self.table_name)

    @property
    def endpoint(self):
        if not self.configured:
            raise ExternalNotConfigured()
        return f"https://api.airtable.com/v0/{quote(self.base_id, safe='')}/{quote(self.table_name, safe='')}"

    def _client(self):
        return httpx.Client(timeout=20, transport=self.transport, headers={"Authorization": f"Bearer {self.token}"})

    def dispatch(self, operation_id, payload):
        """Create exactly once from the local operation identity; never retries here."""
        try:
            with self._client() as client:
                response = client.post(self.endpoint, json={"fields": {
                    "External Operation ID": operation_id,
                    "Action Type": payload["action_type"],
                    "Inspection ID": payload["inspection_id"],
                    "Fixture ID": payload.get("fixture_id") or "",
                    "Pole ID": payload.get("pole_id") or "",
                    "Contract ID": payload.get("contract_id") or "",
                    "Decision ID": payload.get("decision_id") or "",
                    "Payload Hash": payload["payload_hash"],
                    "Effect Summary": payload["effect_summary"],
                    "Status": "REQUESTED",
                }})
                if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                    raise ExternalOutcomeUnknown()
                if not 200 <= response.status_code < 300:
                    raise ExternalDefiniteFailure(f"AIRTABLE_HTTP_{response.status_code}")
                body = response.json()
                record_id = body.get("id")
                if not isinstance(record_id, str) or not record_id:
                    raise ExternalOutcomeUnknown()
                return {"external_system": "Airtable", "record_id": record_id,
                        "display_reference": record_id, "status": "REQUESTED"}
        except ExternalOutcomeUnknown:
            raise
        except ExternalDefiniteFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, ValueError, KeyError, TypeError, AttributeError):
            raise ExternalOutcomeUnknown() from None

    def lookup(self, operation_id):
        """Read by exact operation ID; zero matches stays unknown."""
        formula = "{External Operation ID}='" + operation_id.replace("'", "\\'") + "'"
        try:
            with self._client() as client:
                response = client.get(self.endpoint, params={"filterByFormula": formula, "maxRecords": 3})
                if response.status_code >= 500 or response.status_code in {408, 425, 429}:
                    raise ExternalOutcomeUnknown()
                if not 200 <= response.status_code < 300:
                    raise ExternalDefiniteFailure(f"AIRTABLE_HTTP_{response.status_code}")
                records = response.json().get("records", [])
                if len(records) == 0:
                    return None
                if len(records) > 1:
                    raise ExternalIntegrityError()
                record = records[0]
                return {"external_system": "Airtable", "record_id": record["id"],
                        "display_reference": record["id"], "status": record.get("fields", {}).get("Status", "REQUESTED")}
        except (ExternalOutcomeUnknown, ExternalDefiniteFailure, ExternalIntegrityError):
            raise
        except (httpx.TimeoutException, httpx.NetworkError, ValueError, KeyError, TypeError, AttributeError):
            raise ExternalOutcomeUnknown() from None
