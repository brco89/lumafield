import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from .adapters.airtable import (ExternalDefiniteFailure, ExternalIntegrityError,
                                ExternalNotConfigured, ExternalOutcomeUnknown)
from .adapters.business import DemoBusinessSource
from .media import normalize_image
from .models import DomainError


def now_iso(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat()


def new_id(prefix):
    return prefix + "_" + secrets.token_hex(12)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def intent_hash(model):
    return digest(json.dumps(model.model_dump(), sort_keys=True, ensure_ascii=False))


def require_secret(expected, supplied):
    # compare_digest rejects non-ASCII ``str`` values. Headers are untrusted
    # input, so compare UTF-8 bytes and return a normal 401 for any Unicode
    # credential instead of leaking a 500.
    if (not expected or not supplied
            or not secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))):
        raise DomainError("UNAUTHORIZED", "Missing or invalid credential.", 401)


class Inspections:
    def __init__(self, settings, store, voice, vision, business=None, external=None, clock=time.time):
        self.settings, self.store = settings, store
        self.voice, self.vision, self.clock = voice, vision, clock
        self.business = business or DemoBusinessSource()
        self.external = external

    def _capability(self, session_id, purpose):
        return hmac.new(self.settings.session_signing_key.encode(),
                        f"{purpose}:{session_id}".encode(), hashlib.sha256).hexdigest()

    def create_session(self, request, authorization):
        require_secret("Bearer " + self.settings.demo_access_token if self.settings.demo_access_token else "", authorization)
        if len(self.settings.session_signing_key) < 32 or len(self.settings.agent_tool_key) < 32:
            raise DomainError("AUTH_NOT_CONFIGURED", "Configure the session and tool keys on the server.", 503)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE request_id=?", (request.request_id,)).fetchone()
            if row:
                if row["intent"] != intent_hash(request):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "This key was already used with different content.")
                self._check_expiry(row)
                session_id, expires = row["id"], row["expires_at"]
            else:
                session_id = new_id("ses")
                expires = self.clock() + self.settings.session_ttl_seconds
                db.execute("INSERT INTO sessions(id,request_id,intent,browser_hash,tool_hash,expires_at) VALUES(?,?,?,?,?,?)",
                           (session_id, request.request_id, intent_hash(request),
                            digest(self._capability(session_id, "browser")),
                            digest(self._capability(session_id, "tool")), expires))
        return {"session_id": session_id, "session_token": self._capability(session_id, "browser"),
                "tool_session_capability": self._capability(session_id, "tool"),
                "agent_id": self.settings.agent_id, "expires_at": now_iso(expires)}

    def create_voice_credential(self, session, session_id):
        self._own_session(session, session_id)
        token = self.voice.conversation_token()
        return {"agent_id": self.settings.agent_id,
                "voice_credential_type": "CONVERSATION_TOKEN",
                "voice_credential": token}

    def _check_expiry(self, session):
        if session["expires_at"] <= self.clock():
            raise DomainError("SESSION_EXPIRED", "Open a new inspection session.", 401)

    def authenticate_browser(self, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise DomainError("SESSION_REQUIRED", "Open the inspection console.", 401)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE browser_hash=?", (digest(authorization[7:]),)).fetchone()
        if not row:
            raise DomainError("UNAUTHORIZED", "Invalid session.", 401)
        self._check_expiry(row)
        return dict(row)

    def authenticate_tool(self, key, capability, conversation_id):
        require_secret(self.settings.agent_tool_key, key)
        if not capability or not conversation_id or len(conversation_id) > 160:
            raise DomainError("SESSION_REQUIRED", "Use the console to start a session with voice and photos.", 401)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE tool_hash=?", (digest(capability),)).fetchone()
            if not row:
                raise DomainError("UNAUTHORIZED", "Invalid session.", 401)
            self._check_expiry(row)
            self._bind(db, row, conversation_id, verified=True)
            return dict(db.execute("SELECT * FROM sessions WHERE id=?", (row["id"],)).fetchone())

    def _bind(self, db, row, conversation_id, verified=False):
        if row["conversation_id"] and row["conversation_id"] != conversation_id:
            raise DomainError("CONVERSATION_MISMATCH", "This session belongs to another conversation.")
        try:
            db.execute("UPDATE sessions SET conversation_id=?,verified=MAX(verified,?) WHERE id=?",
                       (conversation_id, int(verified), row["id"]))
        except sqlite3.IntegrityError:
            raise DomainError("CONVERSATION_MISMATCH", "This conversation is already bound to another session.") from None

    def bind(self, session, session_id, conversation_id):
        self._own_session(session, session_id)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            self._bind(db, row, conversation_id)
            return {"session_id": session_id, "conversation_id": conversation_id,
                    "binding_status": "VERIFIED" if row["verified"] else "PROVISIONAL"}

    @staticmethod
    def _own_session(session, session_id):
        if session["id"] != session_id:
            raise DomainError("NOT_FOUND", "Session not found.", 404)

    def _inspection(self, db, session, inspection_id):
        row = db.execute("SELECT * FROM inspections WHERE id=? AND session_id=?",
                         (inspection_id, session["id"])).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Inspection not found.", 404)
        return json.loads(row["snapshot"])

    @staticmethod
    def _save(db, snapshot):
        db.execute("UPDATE inspections SET revision=?,snapshot=? WHERE id=?",
                   (snapshot["revision"], json.dumps(snapshot), snapshot["inspection_id"]))

    @staticmethod
    def _revision(snapshot, expected):
        if snapshot["revision"] != expected:
            raise DomainError("STALE_REVISION", "The inspection changed. Retrieve the current state.",
                              current_revision=snapshot["revision"])

    @staticmethod
    def _open(snapshot):
        if snapshot["state"] == "COMPLETED":
            raise DomainError("INSPECTION_CLOSED", "The inspection is closed.")
        if snapshot["identity_status"] != "FOUND":
            raise DomainError("VERIFY_FIXTURE", "Confirm the pole identity before taking a photo.")

    def start_inspection(self, session, request):
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            previous_request = db.execute("SELECT intent,snapshot FROM inspections WHERE session_id=? AND request_id=?",
                                          (session["id"], request.request_id)).fetchone()
            if previous_request:
                if previous_request["intent"] != intent_hash(request):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "This key was already used with different content.")
                return self._enrich(db, json.loads(previous_request["snapshot"]))
            active = db.execute("SELECT snapshot FROM inspections WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                                (session["id"],)).fetchone()
            if request.supersedes_inspection_id:
                old = self._inspection(db, session, request.supersedes_inspection_id)
                self._revision(old, request.expected_previous_revision)
                if not active or json.loads(active["snapshot"])["inspection_id"] != old["inspection_id"]:
                    raise DomainError("NOT_ACTIVE", "Only the current inspection can be superseded.")
                if old["state"] == "COMPLETED":
                    raise DomainError("INSPECTION_CLOSED", "The previous inspection is already closed.")
                old.update(state="COMPLETED", outcome="SUPERSEDED_IDENTITY", allowed_next_actions=["START_NEXT_INSPECTION"])
                self._save(db, old)
                self.store.event(db, session["id"], old["inspection_id"], "IDENTITY_SUPERSEDED", timestamp, {})
            elif active and json.loads(active["snapshot"])["state"] != "COMPLETED":
                raise DomainError("ACTIVE_INSPECTION", "Continue the current inspection or explicitly supersede its identity.")
            entered_pole_id = request.pole_id.strip().upper()
            # Field users identify poles by the printed number only. Continue
            # accepting the old P- prefix for compatibility, but never persist
            # or display it as part of the operator-facing identifier.
            pole_id = entered_pole_id[2:] if entered_pole_id.startswith("P-") else entered_pole_id
            known = pole_id in {str(n) for n in range(18427, 18432)}
            fixture = f"F-{pole_id}-01" if known else None
            if request.fixture_selector and request.fixture_selector != fixture:
                known, fixture = False, None
            business = self.business.resolve(fixture) if known else None
            if known and not business:
                raise DomainError(
                    "BUSINESS_DATA_UNAVAILABLE",
                    "The utility record for this luminaire could not be loaded.",
                    503,
                    True,
                )
            observation = None
            if request.operator_classification is not None:
                observation = {
                    "observation_id": new_id("obs"),
                    "classification": request.operator_classification,
                    "operator_utterance": request.operator_utterance,
                    "conversation_id": session["conversation_id"],
                    "created_at": timestamp,
                    "capture_channel": "AGENT_REPORTED_VOICE",
                }
            reconciliation_flow = observation is None
            snapshot = {
                "inspection_id": new_id("ins"), "session_id": session["id"], "conversation_id": session["conversation_id"],
                "pole_id": pole_id, "asset_id": f"A-{pole_id}" if known else None, "fixture_id": fixture,
                "identity_status": "FOUND" if known else "UNKNOWN", "revision": 1,
                "state": "AWAITING_OBSERVATION" if known and reconciliation_flow else "OPEN",
                "workflow_version": "field-reconciliation-v2" if reconciliation_flow else "legacy-v1",
                "original_observation": observation, "current_observation": observation,
                "assessment_ids": [], "remaining_image_attempts": 3,
                "identity": (business or {}).get("identity"),
                "registry": (business or {}).get("registry") if reconciliation_flow else None,
                "operational": (business or {}).get("operational") if reconciliation_flow else None,
                "contract": (business or {}).get("contract") if reconciliation_flow else None,
                "billing": (business or {}).get("billing") if reconciliation_flow else None,
                "decision": None, "existing_order": None, "active_proposal_id": None, "active_operation_id": None,
                "operator_response": None, "outcome": None,
                "allowed_next_actions": (
                    ["RECORD_FIELD_OBSERVATION"] if known and reconciliation_flow
                    else ["UPLOAD_EVIDENCE"] if known
                    else ["VERIFY_FIXTURE"]
                ),
                "message": (
                    "Utility record loaded. Record what appears to be installed at this point."
                    if known and reconciliation_flow
                    else "Upload a clear photo of the luminaire."
                    if known
                    else "Pole not found in the Santa Aurora registry. Check the number."
                ),
            }
            db.execute("INSERT INTO inspections(id,session_id,request_id,intent,revision,snapshot) VALUES(?,?,?,?,?,?)",
                       (snapshot["inspection_id"], session["id"], request.request_id, intent_hash(request), 1, json.dumps(snapshot)))
            self.store.event(db, session["id"], snapshot["inspection_id"], "INSPECTION_STARTED", timestamp,
                             {"pole_id": pole_id, "conversation_id": session["conversation_id"]})
            return self._enrich(db, snapshot)

    @staticmethod
    def _queue_projection(snapshot, operation):
        """Project the latest persisted workflow state onto the field queue."""
        state = snapshot.get("state")
        decision = snapshot.get("decision") or {}
        allowed = set(snapshot.get("allowed_next_actions") or [])
        operation_status = operation["status"] if operation else None
        outcome = snapshot.get("outcome")

        if operation_status == "QUEUED" or state == "ACTION_PENDING":
            return "REQUEST_PENDING", "TRACK_REQUEST"
        if state == "ACTION_UNKNOWN" or operation_status == "OUTCOME_UNKNOWN":
            return "REQUEST_UNCONFIRMED", "TRACK_REQUEST"
        if operation_status == "DISPATCHING":
            return "REQUEST_PENDING", "TRACK_REQUEST"
        if state == "COMPLETED":
            if outcome in {"REGISTRY_REVIEW_CREATED", "REPLACEMENT_ORDER_CREATED"}:
                return "REQUEST_SENT", "TRACK_REQUEST"
            if outcome == "MANUAL_REVIEW_REQUIRED":
                return "REVIEW_REQUIRED", "REVIEW"
            if outcome == "ACTION_DECLINED" and decision.get("reconciliation_status") == "DIVERGENT":
                return "DIVERGENCE_CONFIRMED", "CONFIRM_UPDATE"
            return "RESOLVED", "COMPLETED"
        if state == "AWAITING_CONFIRMATION":
            return "DIVERGENCE_CONFIRMED", "CONFIRM_UPDATE"
        if decision:
            reconciliation = decision.get("reconciliation_status")
            if reconciliation == "DIVERGENT":
                return "DIVERGENCE_CONFIRMED", "PREPARE_UPDATE"
            if reconciliation in {"INCONCLUSIVE", "UNKNOWN"}:
                return "REVIEW_REQUIRED", "REVIEW"
            if state == "DECIDED" or "PREPARE_ACTION" in allowed:
                return "READY_TO_CONFIRM", "RESOLVE"
        if "RECONCILE" in allowed:
            return "IN_VERIFICATION", "RECONCILE"
        if state in {"OPEN", "NEEDS_EVIDENCE", "AWAITING_OPERATOR_RESPONSE"}:
            return "IN_VERIFICATION", "CAPTURE_EVIDENCE"
        return "IN_VERIFICATION", "VERIFY_FIELD"

    def list_assets(self, _session):
        lister = getattr(self.business, "list_assets", None)
        assets = lister() if lister else []
        if not assets:
            return {"municipality": getattr(self.business, "municipality", "Santa Aurora"),
                    "assets": []}

        # A pole is persistent; inspections are versioned events over it. The
        # newest inspection wins regardless of which browser session produced
        # it, and its current operation is the final authority for queue state.
        latest_by_fixture = {}
        with self.store.connect() as db:
            for row in db.execute("SELECT id,snapshot FROM inspections ORDER BY rowid DESC"):
                snapshot = json.loads(row["snapshot"])
                fixture_id = snapshot.get("fixture_id")
                if fixture_id and fixture_id not in latest_by_fixture:
                    operation = db.execute(
                        "SELECT * FROM operations WHERE inspection_id=? ORDER BY rowid DESC LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    latest_by_fixture[fixture_id] = (snapshot, operation)

        for asset in assets:
            latest = latest_by_fixture.get(asset["fixture_id"])
            if not latest:
                # Operational metadata (work orders, registry synchronization,
                # etc.) belongs to the pole dossier. Queue progress belongs to
                # persisted inspections. A fresh database must therefore show
                # every pole at the beginning of the workflow, regardless of
                # domain signals already present in the source record.
                operational = asset.setdefault("operational", {})
                operational.update({
                    "queue_status": "NOT_STARTED",
                    "next_step": "START_INSPECTION",
                    "latest_inspection_id": None,
                    "latest_inspection_state": None,
                    "latest_reconciliation_status": None,
                    "active_operation_status": None,
                })
                asset["queue_source"] = "NO_INSPECTION"
                continue
            snapshot, operation = latest
            queue_status, next_step = self._queue_projection(snapshot, operation)
            operational = asset.setdefault("operational", {})
            operational.update({
                "queue_status": queue_status,
                "next_step": next_step,
                "latest_inspection_id": snapshot["inspection_id"],
                "latest_inspection_state": snapshot.get("state"),
                "latest_reconciliation_status": (snapshot.get("decision") or {}).get("reconciliation_status"),
                "active_operation_status": operation["status"] if operation else None,
            })
            asset["queue_source"] = "LATEST_INSPECTION"

        return {"municipality": getattr(self.business, "municipality", "Santa Aurora"),
                "assets": assets}

    def get_asset_record(self, session, pole_id):
        """Return one pole dossier without creating or advancing an inspection."""
        canonical_pole_id = pole_id.strip().upper()
        if canonical_pole_id.startswith("P-"):
            canonical_pole_id = canonical_pole_id[2:]
        for asset in self.list_assets(session)["assets"]:
            if asset["pole_id"] == canonical_pole_id:
                latest_inspection = next(
                    (
                        inspection
                        for inspection in self.inspection_history(session)["inspections"]
                        if inspection["pole_id"] == canonical_pole_id
                    ),
                    None,
                )
                return {
                    "identity_status": "FOUND",
                    **asset,
                    "latest_inspection": latest_inspection,
                }
        raise DomainError(
            "UNKNOWN_ASSET",
            f"Pole {canonical_pole_id} was not found in the Santa Aurora registry.",
            status=404,
        )

    def inspection_summary(self, _session):
        """Return deterministic portfolio totals from every persisted inspection event."""
        metrics = {
            "total_inspections": 0,
            "unique_poles": 0,
            "in_progress": 0,
            "reconciled": 0,
            "divergent_from_registry": 0,
            "registry_confirmed": 0,
            "registry_compatible": 0,
            "manual_review_required": 0,
            "unknown_assets": 0,
            "field_led": 0,
            "field_legacy": 0,
            "power_verified": 0,
            "external_requests_created": 0,
            "external_requests_pending": 0,
            "external_requests_unconfirmed": 0,
            "excluded_superseded_identity_attempts": 0,
            "verified_power_delta_w": 0,
            "estimated_cycle_energy_delta_kwh": 0.0,
        }
        poles = set()
        with self.store.connect() as db:
            rows = db.execute("SELECT snapshot FROM inspections ORDER BY rowid").fetchall()
            for row in rows:
                snapshot = json.loads(row["snapshot"])
                if snapshot.get("outcome") == "SUPERSEDED_IDENTITY":
                    metrics["excluded_superseded_identity_attempts"] += 1
                    continue
                metrics["total_inspections"] += 1
                if snapshot.get("pole_id"):
                    poles.add(snapshot["pole_id"])
                if snapshot.get("state") != "COMPLETED":
                    metrics["in_progress"] += 1

                decision = snapshot.get("decision") or {}
                status = decision.get("reconciliation_status")
                disposition = decision.get("disposition")
                if decision:
                    metrics["reconciled"] += 1
                if status == "DIVERGENT" or disposition == "REGISTRY_MISMATCH":
                    metrics["divergent_from_registry"] += 1
                if status == "CONFIRMED" or snapshot.get("outcome") == "NO_ACTION_LED_CONFIRMED":
                    metrics["registry_confirmed"] += 1
                elif status == "COMPATIBLE":
                    metrics["registry_compatible"] += 1
                if disposition == "MANUAL_REVIEW" or snapshot.get("outcome") == "MANUAL_REVIEW_REQUIRED":
                    metrics["manual_review_required"] += 1
                if snapshot.get("identity_status") == "UNKNOWN" or disposition == "UNKNOWN_ASSET":
                    metrics["unknown_assets"] += 1

                field_classification = (decision.get("field_classification")
                                        or (snapshot.get("original_observation") or {}).get("classification"))
                if field_classification == "LED":
                    metrics["field_led"] += 1
                elif field_classification == "LEGACY":
                    metrics["field_legacy"] += 1

                if decision.get("observed_power_w") is not None:
                    metrics["power_verified"] += 1
                power_delta = decision.get("power_delta_w")
                if isinstance(power_delta, (int, float)) and not isinstance(power_delta, bool):
                    metrics["verified_power_delta_w"] += power_delta
                energy_delta = decision.get("estimated_cycle_energy_delta_kwh")
                if isinstance(energy_delta, (int, float)) and not isinstance(energy_delta, bool):
                    metrics["estimated_cycle_energy_delta_kwh"] += energy_delta

            operation_counts = dict(db.execute(
                "SELECT status, COUNT(*) AS count FROM operations GROUP BY status"
            ).fetchall())

        metrics["unique_poles"] = len(poles)
        metrics["external_requests_created"] = operation_counts.get("SUCCEEDED", 0)
        metrics["external_requests_pending"] = (
            operation_counts.get("QUEUED", 0) + operation_counts.get("DISPATCHING", 0)
        )
        metrics["external_requests_unconfirmed"] = operation_counts.get("OUTCOME_UNKNOWN", 0)
        metrics["estimated_cycle_energy_delta_kwh"] = round(
            metrics["estimated_cycle_energy_delta_kwh"], 2
        )
        return {
            "scope": "ALL_PERSISTED_INSPECTIONS",
            "scope_label": "All recorded inspections",
            "metrics": metrics,
            "suggested_question": "How many inspections found a discrepancy with the utility record?",
            "suggested_action": "Start a new inspection",
        }

    def inspection_history(self, _session):
        """Project persisted inspection events into a browser-readable history."""
        inspections = []
        with self.store.connect() as db:
            rows = db.execute(
                """
                SELECT i.id, i.snapshot,
                       (SELECT e.created_at FROM events e
                        WHERE e.inspection_id=i.id AND e.kind='INSPECTION_STARTED'
                        ORDER BY e.id LIMIT 1) AS created_at
                FROM inspections i ORDER BY i.rowid DESC
                """
            ).fetchall()
            for row in rows:
                snapshot = json.loads(row["snapshot"])
                if snapshot.get("outcome") == "SUPERSEDED_IDENTITY":
                    continue
                operation = db.execute(
                    "SELECT * FROM operations WHERE inspection_id=? ORDER BY rowid DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                queue_status, next_step = self._queue_projection(snapshot, operation)
                external_reference = None
                if operation and operation["external_reference"]:
                    external_reference = json.loads(operation["external_reference"])
                assessment = db.execute(
                    "SELECT result FROM assessments WHERE inspection_id=? ORDER BY rowid DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                evidence_count = db.execute(
                    "SELECT COUNT(*) FROM evidence WHERE inspection_id=?",
                    (row["id"],),
                ).fetchone()[0]
                inspections.append({
                    "inspection_id": snapshot["inspection_id"],
                    "pole_id": snapshot.get("pole_id"),
                    "asset_id": snapshot.get("asset_id"),
                    "created_at": row["created_at"],
                    "state": snapshot.get("state"),
                    "outcome": snapshot.get("outcome"),
                    "revision": snapshot.get("revision"),
                    "queue_status": queue_status,
                    "next_step": next_step,
                    "identity": snapshot.get("identity"),
                    "registry": snapshot.get("registry"),
                    "operational": snapshot.get("operational"),
                    "field_observation": snapshot.get("original_observation"),
                    "latest_assessment": json.loads(assessment["result"]) if assessment else None,
                    "decision": snapshot.get("decision"),
                    "evidence_count": evidence_count,
                    "operation": ({
                        "status": operation["status"],
                        "action_type": operation["action_type"],
                        "external_reference": external_reference,
                    } if operation else None),
                })
        return {"scope": "ALL_PERSISTED_INSPECTIONS", "inspections": inspections}

    def record_field_observation(self, session, inspection_id, request):
        """Record the field hypothesis after the registry has been read aloud."""
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._open(snapshot)
            for row in db.execute(
                "SELECT detail FROM events WHERE inspection_id=? AND kind='FIELD_OBSERVATION_RECORDED'",
                (inspection_id,),
            ):
                if json.loads(row["detail"]).get("request_id") == request.request_id:
                    return self._enrich(db, snapshot)
            self._revision(snapshot, request.expected_revision)
            if snapshot["state"] != "AWAITING_OBSERVATION":
                raise DomainError("INVALID_STATE", "The field observation has already been recorded.")
            observation = {
                "observation_id": new_id("obs"),
                "classification": request.operator_classification,
                "operator_utterance": request.operator_utterance,
                "conversation_id": session["conversation_id"],
                "created_at": timestamp,
                "capture_channel": "AGENT_REPORTED_VOICE",
            }
            snapshot["original_observation"] = observation
            snapshot["current_observation"] = observation
            snapshot["revision"] += 1
            snapshot.update(
                state="OPEN",
                allowed_next_actions=["UPLOAD_EVIDENCE"],
                message="Observation recorded. Take a general photo of the luminaire.",
            )
            self._save(db, snapshot)
            self.store.event(
                db,
                session["id"],
                inspection_id,
                "FIELD_OBSERVATION_RECORDED",
                timestamp,
                {
                    "request_id": request.request_id,
                    "classification": request.operator_classification,
                    "revision": snapshot["revision"],
                },
            )
            return self._enrich(db, snapshot)

    def upload(self, session, inspection_id, expected_revision, raw):
        # Check ownership before performing image work.
        with self.store.connect() as db:
            self._inspection(db, session, inspection_id)
        normalized, sha = normalize_image(raw)
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._open(snapshot)
            old = db.execute("SELECT receipt FROM evidence WHERE inspection_id=? AND sha256=?", (inspection_id, sha)).fetchone()
            if old:
                receipt = json.loads(old["receipt"])
                return {**receipt, "duplicate": True, "inspection_revision": snapshot["revision"]}
            if "UPLOAD_EVIDENCE" not in snapshot.get("allowed_next_actions", []):
                raise DomainError("UPLOAD_NOT_AVAILABLE", "The inspection is not waiting for a photo.")
            reused = db.execute(
                "SELECT i.snapshot FROM evidence e JOIN inspections i ON i.id=e.inspection_id "
                "WHERE e.sha256=? AND e.inspection_id<>? LIMIT 1",
                (sha, inspection_id),
            ).fetchone()
            if reused:
                previous = json.loads(reused["snapshot"])
                if previous["pole_id"] != snapshot["pole_id"]:
                    raise DomainError(
                        "EVIDENCE_REUSED",
                        f"This same photo was already used for pole {previous['pole_id']}. Upload a photo of the current pole.",
                    )
            self._revision(snapshot, expected_revision)
            count = db.execute("SELECT COUNT(*) FROM evidence WHERE inspection_id=?", (inspection_id,)).fetchone()[0]
            if count >= 3:
                raise DomainError("EVIDENCE_LIMIT", "The three-photo limit has been reached. Manual review is required.")
            unassessed = db.execute("SELECT e.id FROM evidence e LEFT JOIN assessments a ON a.evidence_id=e.id WHERE e.inspection_id=? AND a.id IS NULL",
                                    (inspection_id,)).fetchone()
            if unassessed:
                raise DomainError("ASSESS_PENDING_EVIDENCE", "Wait for the photo already submitted to be assessed.")
            evidence_id, timestamp = new_id("evd"), now_iso(self.clock())
            path = self.store.media_dir / (evidence_id + ".jpg")
            # Orphaned files on a crash are harmless and never served publicly.
            with path.open("xb") as media:
                media.write(normalized)
            snapshot["revision"] += 1
            snapshot.update(state="NEEDS_EVIDENCE", allowed_next_actions=["ASSESS_EVIDENCE"],
                            message="Photo received. Waiting for visual assessment.")
            receipt = {"evidence_id": evidence_id, "inspection_id": inspection_id,
                       "inspection_revision": snapshot["revision"], "sha256": sha, "mime_type": "image/jpeg",
                       "size_bytes": len(normalized), "accepted_at": timestamp, "duplicate": False}
            db.execute("INSERT INTO evidence VALUES(?,?,?,?,?)", (evidence_id, inspection_id, sha, json.dumps(receipt), str(path)))
            self._save(db, snapshot)
            self.store.event(db, session["id"], inspection_id, "EVIDENCE_ACCEPTED", timestamp,
                             {"evidence_id": evidence_id, "sha256": sha, "revision": snapshot["revision"]})
            return receipt

    def assess(self, session, inspection_id, request):
        with self.store.assessment_lock:
            return self._assess_serialized(session, inspection_id, request)

    def _assess_serialized(self, session, inspection_id, request):
        with self.store.connect() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._open(snapshot)
            evidence = db.execute("SELECT * FROM evidence WHERE id=? AND inspection_id=?", (request.evidence_id, inspection_id)).fetchone()
            if not evidence:
                raise DomainError("NOT_FOUND", "Photo not found in this inspection.", 404)
            existing = db.execute("SELECT result FROM assessments WHERE evidence_id=?", (request.evidence_id,)).fetchone()
            if existing:
                assessment = json.loads(existing["result"])
                return {"inspection_id": inspection_id, "inspection_revision": snapshot["revision"],
                        "assessment": assessment, "assessment_acceptable": self._assessment_usable(assessment),
                        "state": snapshot["state"], "allowed_next_actions": snapshot["allowed_next_actions"],
                        "message": snapshot["message"], "replayed": True}
            self._revision(snapshot, request.expected_revision)
        # This is the sole adapter boundary: never pass session, snapshot, file name
        # or previous assessments. The provider is intentionally stateless.
        from pathlib import Path
        result = self.vision.assess(Path(evidence["media_path"]).read_bytes())
        self._check_expiry(session)
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._open(snapshot)
            self._revision(snapshot, request.expected_revision)
            # The upload endpoint already binds the evidence to the active,
            # revision-scoped inspection. Text visible in the pixels may be a
            # model, serial number or electrical rating; it is not a reliable
            # pole identifier and must never gate visual evidence.
            assessment = {**result.model_dump(), "identity_consistency": "NOT_CHECKED",
                          "assessment_id": new_id("asm"), "evidence_id": request.evidence_id,
                          "attempt_number": len(snapshot["assessment_ids"]) + 1,
                          "model_id": self.vision.model_id, "prompt_version": self.vision.prompt_version,
                          "created_at": now_iso(self.clock())}
            db.execute("INSERT INTO assessments VALUES(?,?,?,?)",
                       (assessment["assessment_id"], request.evidence_id, inspection_id, json.dumps(assessment)))
            snapshot["assessment_ids"].append(assessment["assessment_id"])
            snapshot["revision"] += 1
            snapshot["remaining_image_attempts"] = 3 - len(snapshot["assessment_ids"])
            sufficient = result.sufficient
            power_usable = self._power_sufficient(assessment)
            if snapshot.get("workflow_version") == "field-reconciliation-v2":
                committed = [json.loads(row["result"]) for row in db.execute(
                    "SELECT result FROM assessments WHERE inspection_id=? ORDER BY rowid",
                    (inspection_id,),
                )]
                class_assessments = [item for item in committed if self._sufficient(item)]
                classes = {item["visual_classification"] for item in class_assessments}
                power_assessments = [item for item in committed if self._power_sufficient(item)]
                attempts_exhausted = snapshot["remaining_image_attempts"] == 0

                if len(classes) > 1:
                    snapshot["state"] = "OPEN"
                    snapshot["allowed_next_actions"] = ["RECONCILE"]
                    snapshot["message"] = "The photos conflict with each other. I will record the need for review."
                elif not classes:
                    snapshot["state"] = "NEEDS_EVIDENCE"
                    snapshot["allowed_next_actions"] = ["RECONCILE"] if attempts_exhausted else ["UPLOAD_EVIDENCE"]
                    if attempts_exhausted:
                        snapshot["message"] = "All three photos were inconclusive. Manual review is required."
                    elif result.evidence_origin != "FIELD_PLAUSIBLE":
                        snapshot["message"] = "The image does not appear to be valid field evidence. Upload a real photo of this pole."
                    elif power_usable:
                        snapshot["message"] = "The power rating is legible, but a general photo of the luminaire is still required."
                    else:
                        snapshot["message"] = result.retake_guidance or "The luminaire is still not identifiable. Try another photo from a safe position."
                else:
                    field_class = next(iter(classes))
                    registry_class = (snapshot.get("registry") or {}).get("classification")
                    needs_power = field_class != registry_class and not power_assessments
                    if needs_power and not attempts_exhausted:
                        snapshot["state"] = "NEEDS_EVIDENCE"
                        snapshot["allowed_next_actions"] = ["UPLOAD_EVIDENCE"]
                        snapshot["message"] = (
                            "Technology differs from the utility record. Photograph the luminaire label or model "
                            "to verify installed power."
                        )
                    else:
                        snapshot["state"] = "OPEN"
                        snapshot["allowed_next_actions"] = ["RECONCILE"]
                        snapshot["message"] = (
                            "Technology and power verified. I will compare field evidence with the utility record."
                            if power_assessments
                            else "Technology verified. I will compare the evidence with the utility record."
                        )
            else:
                # Compatibility path for inspections created by the previous
                # agent contract, which asked for an observation before start.
                attempts_exhausted = not sufficient and snapshot["remaining_image_attempts"] == 0
                snapshot["state"] = "AWAITING_OPERATOR_RESPONSE" if sufficient else "NEEDS_EVIDENCE"
                snapshot["allowed_next_actions"] = (
                    ["UPLOAD_EVIDENCE"] if not sufficient and not attempts_exhausted
                    else ["RECONCILE"] if attempts_exhausted
                    else []
                )
                if sufficient:
                    snapshot["message"] = "Visual assessment recorded. The business decision belongs to the next stage."
                elif not snapshot["remaining_image_attempts"]:
                    snapshot["message"] = "All three photos were inconclusive. I will prepare a manual-review route; no external request has been created."
                elif result.evidence_origin != "FIELD_PLAUSIBLE":
                    snapshot["message"] = "The image does not appear to be valid field evidence. Upload a real photo of this pole."
                else:
                    snapshot["message"] = "The photo is insufficient. Upload another image from a safe position."
            self._save(db, snapshot)
            self.store.event(db, session["id"], inspection_id, "VISUAL_ASSESSMENT_COMMITTED", assessment["created_at"],
                             {"assessment_id": assessment["assessment_id"], "evidence_id": request.evidence_id,
                              "model_id": self.vision.model_id, "revision": snapshot["revision"]})
            return {"inspection_id": inspection_id, "inspection_revision": snapshot["revision"],
                    "assessment": assessment, "assessment_acceptable": sufficient or power_usable,
                    "state": snapshot["state"], "allowed_next_actions": snapshot["allowed_next_actions"],
                    "message": snapshot["message"], "replayed": False}

    def record_operator_response(self, session, inspection_id, request):
        """Append a human response; it never creates an external action."""
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._open(snapshot)
            for row in db.execute(
                "SELECT detail FROM events WHERE inspection_id=? AND kind='OPERATOR_RESPONSE_RECORDED'",
                (inspection_id,),
            ):
                if json.loads(row["detail"]).get("request_id") == request.request_id:
                    return self._enrich(db, snapshot)
            self._revision(snapshot, request.expected_revision)
            correction_states = {"DECIDED", "AWAITING_CONFIRMATION"}
            is_pre_dispatch_correction = (
                snapshot["state"] in correction_states and request.kind == "ACCEPT_VISUAL"
            )
            if snapshot["state"] not in {
                "AWAITING_OPERATOR_RESPONSE", "NEEDS_EVIDENCE", "OPEN", *correction_states,
            }:
                raise DomainError("INVALID_STATE", "A response is not expected in this state.")
            if snapshot["state"] in correction_states and not is_pre_dispatch_correction:
                raise DomainError(
                    "INVALID_STATE",
                    "Only an evidence-supported observation correction is accepted before dispatch.",
                )
            assessment = None
            if request.assessment_id:
                row = db.execute("SELECT result FROM assessments WHERE id=? AND inspection_id=?",
                                 (request.assessment_id, inspection_id)).fetchone()
                if not row:
                    raise DomainError("NOT_FOUND", "This assessment does not belong to the inspection.", 404)
                assessment = json.loads(row["result"])
            if request.kind == "ACCEPT_VISUAL":
                if not assessment or assessment["visual_classification"] == "INCONCLUSIVE":
                    raise DomainError("ASSESSMENT_NOT_ACCEPTABLE", "Only a defined visual classification can be accepted.")
                if not self._sufficient(assessment):
                    raise DomainError("ASSESSMENT_NOT_SUFFICIENT", "The photo does not contain sufficient evidence for endorsement.")
                observation = dict(snapshot["current_observation"])
                observation.update(
                    observation_id=new_id("obs"), classification=assessment["visual_classification"],
                    operator_utterance=request.operator_utterance, assessment_id=request.assessment_id,
                    created_at=timestamp, capture_channel="AGENT_REPORTED_VOICE",
                )
                snapshot["current_observation"] = observation
            superseded_proposal_id = None
            if is_pre_dispatch_correction:
                superseded_proposal_id = snapshot.get("active_proposal_id")
                if superseded_proposal_id:
                    db.execute(
                        "UPDATE proposals SET status='SUPERSEDED' WHERE id=? AND status='ACTIVE'",
                        (superseded_proposal_id,),
                    )
                snapshot.update(
                    decision=None,
                    outcome=None,
                    active_proposal_id=None,
                )
            snapshot["operator_response"] = {
                "response_id": new_id("rsp"), "kind": request.kind,
                "assessment_id": request.assessment_id, "operator_utterance": request.operator_utterance,
                "conversation_id": session["conversation_id"], "created_at": timestamp,
                "capture_channel": "AGENT_REPORTED_VOICE",
            }
            snapshot["revision"] += 1
            message = (
                "Technician correction recorded. The previous proposal was withdrawn. "
                "I will reconcile the corrected observation with the stored evidence."
                if is_pre_dispatch_correction and request.kind == "ACCEPT_VISUAL"
                else
                "No additional label photo is available. Installed power will remain unverified. "
                "I will reconcile the available evidence with the utility record."
                if snapshot.get("workflow_version") == "field-reconciliation-v2"
                and request.kind == "DECLINE_RETAKE"
                else "Response recorded. I will reconcile the evidence with the utility record."
            )
            snapshot.update(state="OPEN", allowed_next_actions=["RECONCILE"], message=message)
            self._save(db, snapshot)
            self.store.event(db, session["id"], inspection_id, "OPERATOR_RESPONSE_RECORDED", timestamp,
                             {"request_id": request.request_id, "kind": request.kind,
                              "assessment_id": request.assessment_id, "revision": snapshot["revision"],
                              "superseded_proposal_id": superseded_proposal_id})
            response = self._enrich(db, snapshot)
            corrected_revision = snapshot["revision"]
        if is_pre_dispatch_correction:
            return self.reconcile(session, inspection_id, corrected_revision)
        return response

    @staticmethod
    def _sufficient(assessment):
        return (assessment["visual_classification"] != "INCONCLUSIVE"
                and assessment["visual_confidence"] in {"HIGH", "MEDIUM"}
                and assessment["evidence_quality"] in {"GOOD", "LIMITED"}
                and assessment.get("evidence_origin") == "FIELD_PLAUSIBLE")

    @staticmethod
    def _power_sufficient(assessment):
        return (assessment.get("observed_power_w") is not None
                and bool((assessment.get("power_evidence") or "").strip())
                and assessment.get("evidence_quality") in {"GOOD", "LIMITED"}
                and assessment.get("evidence_origin") == "FIELD_PLAUSIBLE")

    @classmethod
    def _assessment_usable(cls, assessment):
        return cls._sufficient(assessment) or cls._power_sufficient(assessment)

    def reconcile(self, session, inspection_id, expected_revision):
        """Persist the decision computed from stored evidence and business snapshots."""
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            if snapshot["state"] in {"COMPLETED", "DECIDED", "AWAITING_CONFIRMATION", "ACTION_PENDING", "ACTION_UNKNOWN"}:
                self._revision(snapshot, expected_revision)
                return self._enrich(db, snapshot)
            self._revision(snapshot, expected_revision)

            # Business data is loaded after evidence is committed, except for an
            # unknown-asset review. It never enters the visual request builder.
            business = self.business.resolve(snapshot["fixture_id"]) if snapshot["identity_status"] == "FOUND" else None
            if snapshot["identity_status"] == "FOUND" and not business:
                raise DomainError("BUSINESS_DATA_UNAVAILABLE",
                                  "The utility record and contract for this luminaire could not be loaded.",
                                  503, True)
            if business:
                if (snapshot["registry"] != business["registry"]
                        or snapshot["contract"] != business["contract"]
                        or snapshot.get("billing") != business.get("billing")
                        or snapshot.get("identity") != business.get("identity")
                        or snapshot.get("operational") != business.get("operational")):
                    snapshot["registry"], snapshot["contract"] = business["registry"], business["contract"]
                    snapshot["billing"] = business.get("billing")
                    snapshot["identity"] = business.get("identity")
                    snapshot["operational"] = business.get("operational")
                    snapshot["revision"] += 1

            assessments = [json.loads(row["result"]) for row in db.execute(
                "SELECT result FROM assessments WHERE inspection_id=? ORDER BY rowid", (inspection_id,))]
            sufficient = [a for a in assessments if self._sufficient(a)]
            classes = {a["visual_classification"] for a in sufficient}
            response = snapshot.get("operator_response")

            def decide(final_classification, disposition, reasons, message, *, next_actions,
                       state="DECIDED", outcome=None, decisive=None):
                snapshot["decision"] = {
                    "decision_id": new_id("dec"), "inspection_revision": snapshot["revision"],
                    "final_classification": final_classification, "disposition": disposition,
                    "exception_type": ("REGISTRY_MISMATCH" if disposition == "REGISTRY_MISMATCH" else
                                       "VISUAL_CONFLICT" if "VISUAL_CONFLICT" in reasons else
                                       "HUMAN_AI_DISAGREEMENT" if "HUMAN_AI_DISAGREEMENT" in reasons else
                                       "INSUFFICIENT_EVIDENCE" if "INSUFFICIENT_EVIDENCE" in reasons else
                                       "UNKNOWN_ASSET" if disposition == "UNKNOWN_ASSET" else None),
                    "assessment_ids": snapshot["assessment_ids"], "decisive_assessment_id": decisive,
                    "reason_codes": reasons, "registry_revision": (snapshot["registry"] or {}).get("revision"),
                    "contract_revision": (snapshot["contract"] or {}).get("revision"), "created_at": timestamp,
                }
                snapshot.update(state=state, allowed_next_actions=next_actions, outcome=outcome, message=message)

            if snapshot.get("workflow_version") == "field-reconciliation-v2":
                if snapshot["identity_status"] != "FOUND":
                    decide("UNRESOLVED", "UNKNOWN_ASSET", ["UNKNOWN_ASSET"],
                           "The pole number could not be linked to a known luminaire.",
                           next_actions=["PREPARE_ACTION"])
                    snapshot["decision"]["reconciliation_status"] = "UNKNOWN"
                elif len(classes) > 1:
                    decide("UNRESOLVED", "MANUAL_REVIEW", ["VISUAL_CONFLICT"],
                           "The photos conflict with each other. The utility record cannot be reconciled automatically.",
                           next_actions=["PREPARE_ACTION"])
                    snapshot["decision"]["reconciliation_status"] = "INCONCLUSIVE"
                elif not sufficient:
                    if len(assessments) >= 3:
                        decide("UNRESOLVED", "MANUAL_REVIEW", ["INSUFFICIENT_EVIDENCE"],
                               "The three photos do not verify the asset. Manual review is required.",
                               next_actions=["PREPARE_ACTION"])
                        snapshot["decision"]["reconciliation_status"] = "INCONCLUSIVE"
                    else:
                        snapshot.update(
                            state="NEEDS_EVIDENCE",
                            allowed_next_actions=["UPLOAD_EVIDENCE"],
                            message="The evidence does not yet verify the asset. Upload another photo from a safe position.",
                        )
                else:
                    decisive = sufficient[-1]
                    final_class = decisive["visual_classification"]
                    current_observation = snapshot.get("current_observation") or snapshot.get("original_observation") or {}
                    reported_class = current_observation.get("classification")
                    if reported_class not in {None, "UNSURE", final_class}:
                        decide(
                            "UNRESOLVED",
                            "MANUAL_REVIEW",
                            ["HUMAN_AI_DISAGREEMENT"],
                            "The field observation and visual evidence conflict. Review is required.",
                            next_actions=["PREPARE_ACTION"],
                            decisive=decisive["assessment_id"],
                        )
                        snapshot["decision"]["reconciliation_status"] = "INCONCLUSIVE"
                    else:
                        registry = snapshot["registry"]
                        registry_class = registry["classification"]
                        registered_power = registry.get("registered_power_w")
                        power_assessments = [item for item in assessments if self._power_sufficient(item)]
                        power_assessment = power_assessments[-1] if power_assessments else None
                        observed_power = power_assessment.get("observed_power_w") if power_assessment else None
                        power_delta = (
                            round(observed_power - registered_power, 2)
                            if observed_power is not None and registered_power is not None
                            else None
                        )
                        power_mismatch = power_delta is not None and abs(power_delta) >= 1
                        technology_mismatch = registry_class != final_class
                        billing = snapshot.get("billing") or {}
                        daily_minutes = billing.get("daily_minutes")
                        energy_delta = (
                            round(
                                power_delta * daily_minutes * billing.get("days_in_cycle", 0) / 60_000,
                                2,
                            )
                            if power_delta is not None and daily_minutes and billing.get("days_in_cycle")
                            else None
                        )
                        reconciliation_status = (
                            "DIVERGENT" if technology_mismatch or power_mismatch
                            else "CONFIRMED" if power_delta is not None
                            else "COMPATIBLE"
                        )
                        operational = snapshot.get("operational") or {}
                        completed_at = operational.get("completed_at")
                        intervention_age_days = None
                        if completed_at:
                            try:
                                intervention_age_days = (
                                    datetime.fromtimestamp(self.clock(), timezone.utc).date()
                                    - datetime.fromisoformat(completed_at).date()
                                ).days
                            except ValueError:
                                intervention_age_days = None
                        common = {
                            "reconciliation_status": reconciliation_status,
                            "registered_classification": registry_class,
                            "registered_subtype": registry.get("registered_subtype"),
                            "registered_power_w": registered_power,
                            "field_classification": final_class,
                            "observed_power_w": observed_power,
                            "power_evidence": power_assessment.get("power_evidence") if power_assessment else None,
                            "power_delta_w": power_delta,
                            "estimated_cycle_energy_delta_kwh": energy_delta,
                            "billing_assumption": billing or None,
                            "intervention_completed_at": completed_at,
                            "intervention_age_days": intervention_age_days,
                            "registry_sync_status": operational.get("registry_sync_status"),
                            "technology_status": "MISMATCH" if technology_mismatch else "MATCH",
                            "power_status": (
                                "MISMATCH" if power_mismatch
                                else "MATCH" if power_delta is not None
                                else "UNVERIFIED"
                            ),
                        }

                        if reconciliation_status == "DIVERGENT":
                            reasons = []
                            if technology_mismatch:
                                reasons.append("TECHNOLOGY_MISMATCH")
                            if power_mismatch:
                                reasons.append("POWER_MISMATCH")
                            if observed_power is None:
                                reasons.append("POWER_UNVERIFIED")
                            field_description = f"{final_class} · {observed_power:g} W" if observed_power is not None else f"{final_class} · power not verified"
                            registry_description = f"{registry.get('registered_subtype') or registry_class} · {registered_power:g} W" if registered_power is not None else f"{registry.get('registered_subtype') or registry_class} · power not registered"
                            message = f"Field and system of record do not match. Utility record: {registry_description}. Field: {field_description}."
                            decide(
                                final_class,
                                "REGISTRY_MISMATCH",
                                reasons,
                                message,
                                next_actions=["PREPARE_ACTION"],
                                decisive=decisive["assessment_id"],
                                outcome="REGISTRY_MISMATCH_FLAGGED",
                            )
                            snapshot["decision"].update(common)
                            snapshot["decision"]["downstream_action"] = "REGISTRY_REVIEW"
                        elif final_class == "LED":
                            message = (
                                f"Utility record confirmed in the field: LED · {observed_power:g} W."
                                if reconciliation_status == "CONFIRMED"
                                else "The installed technology is compatible with the utility record. Power was not verified from the photo."
                            )
                            decide(
                                "LED",
                                "REGISTRY_MATCH",
                                ["REGISTRY_CONFIRMED" if reconciliation_status == "CONFIRMED" else "REGISTRY_COMPATIBLE"],
                                message,
                                next_actions=["START_NEXT_INSPECTION"],
                                state="COMPLETED",
                                outcome="REGISTRY_CONFIRMED" if reconciliation_status == "CONFIRMED" else "REGISTRY_COMPATIBLE",
                                decisive=decisive["assessment_id"],
                            )
                            snapshot["decision"].update(common)
                        else:
                            finder = getattr(self.business, "find_existing_order", None)
                            existing_order = finder(snapshot["fixture_id"]) if finder else None
                            if existing_order:
                                snapshot["existing_order"] = existing_order
                                decide(
                                    "LEGACY",
                                    "REGISTRY_MATCH",
                                    ["REGISTRY_CONFIRMED", "EXISTING_ORDER_FOUND"],
                                    "Utility record confirmed in the field. A modernization request already exists; no duplicate was created.",
                                    next_actions=["START_NEXT_INSPECTION"],
                                    state="COMPLETED",
                                    outcome="REGISTRY_CONFIRMED_EXISTING_ACTION",
                                    decisive=decisive["assessment_id"],
                                )
                                snapshot["decision"].update(common)
                            elif snapshot["contract"]["status"] == "ACTIVE" and snapshot["contract"]["legacy_replacement_eligible"]:
                                decide(
                                    "LEGACY",
                                    "REGISTRY_MATCH",
                                    ["REGISTRY_CONFIRMED", "CONTRACT_ELIGIBLE"],
                                    "Utility record confirmed in the field. The contract-based replacement is a downstream action after reconciliation.",
                                    next_actions=["PREPARE_ACTION"],
                                    decisive=decisive["assessment_id"],
                                )
                                snapshot["decision"].update(common)
                                snapshot["decision"]["downstream_action"] = "REPLACEMENT"
                            else:
                                decide(
                                    "LEGACY",
                                    "REGISTRY_MATCH",
                                    ["REGISTRY_CONFIRMED", "CONTRACT_NOT_ELIGIBLE"],
                                    "Utility record confirmed in the field. No contract action is available.",
                                    next_actions=["START_NEXT_INSPECTION"],
                                    state="COMPLETED",
                                    outcome="REGISTRY_CONFIRMED",
                                    decisive=decisive["assessment_id"],
                                )
                                snapshot["decision"].update(common)
            elif snapshot["identity_status"] != "FOUND":
                decide("UNRESOLVED", "UNKNOWN_ASSET", ["UNKNOWN_ASSET"],
                       "The pole number could not be linked to a known luminaire.",
                       next_actions=["PREPARE_ACTION"])
            elif len(classes) > 1:
                decide("UNRESOLVED", "MANUAL_REVIEW", ["VISUAL_CONFLICT"],
                       "The photos contain conflicting strong visual classifications. The inspection requires review.",
                       next_actions=["PREPARE_ACTION"])
            elif not sufficient:
                if response and response["kind"] == "DECLINE_RETAKE":
                    decide("UNRESOLVED", "MANUAL_REVIEW", ["INSUFFICIENT_EVIDENCE"],
                           "The technician will not submit another photo. I routed the inspection to review.",
                           next_actions=["PREPARE_ACTION"])
                elif len(assessments) >= 3:
                    decide("UNRESOLVED", "MANUAL_REVIEW", ["INSUFFICIENT_EVIDENCE"],
                           "The three photos do not support a reliable classification. I routed the inspection to review.",
                           next_actions=["PREPARE_ACTION"])
                else:
                    snapshot.update(state="NEEDS_EVIDENCE", allowed_next_actions=["UPLOAD_EVIDENCE"],
                                    message="The evidence is still insufficient. Upload another photo from a safe position.")
            elif not response:
                snapshot.update(state="AWAITING_OPERATOR_RESPONSE", allowed_next_actions=["RESPOND_TO_ASSESSMENT"],
                                message="The photo supports a classification. Does the technician endorse that reading?")
            elif response["kind"] in {"UNSURE", "DECLINE_RETAKE"}:
                decide("UNRESOLVED", "MANUAL_REVIEW", ["HUMAN_AI_DISAGREEMENT"],
                       "The classification was not endorsed. I routed the inspection to review.",
                       next_actions=["PREPARE_ACTION"])
            elif response["kind"] == "MAINTAIN_ORIGINAL" and snapshot["current_observation"]["classification"] not in classes:
                decide("UNRESOLVED", "MANUAL_REVIEW", ["HUMAN_AI_DISAGREEMENT"],
                       "The technician observation conflicts with the visual evidence. I routed the inspection to review.",
                       next_actions=["PREPARE_ACTION"])
            else:
                decisive = next(iter(sufficient))
                final_class = decisive["visual_classification"]
                registry_class = snapshot["registry"]["classification"]
                if registry_class != final_class:
                    decide(final_class, "REGISTRY_MISMATCH", ["REGISTRY_MISMATCH"],
                           f"The photo supports {final_class}, but the utility record says {registry_class}. Review is required.",
                           next_actions=["PREPARE_ACTION"], decisive=decisive["assessment_id"], outcome="REGISTRY_MISMATCH_FLAGGED")
                elif final_class == "LED":
                    decide("LED", "NO_ACTION", ["LED_CONFIRMED"],
                           "LED confirmed and compatible with the utility record. No request is required.",
                           next_actions=["START_NEXT_INSPECTION"], state="COMPLETED",
                           outcome="NO_ACTION_LED_CONFIRMED", decisive=decisive["assessment_id"])
                elif final_class == "LEGACY":
                    # Existing-order lookup happens only after the visual and
                    # registry classes agree. A seeded reference is returned
                    # as evidence of an already-open action; no new proposal
                    # or external write is created.
                    finder = getattr(self.business, "find_existing_order", None)
                    existing_order = finder(snapshot["fixture_id"]) if finder else None
                    if existing_order:
                        snapshot["existing_order"] = existing_order
                        decide("LEGACY", "EXISTING_ORDER", ["LEGACY_CONFIRMED", "EXISTING_ORDER_FOUND"],
                               "An open request already exists for this luminaire. No duplicate was created.",
                               next_actions=["START_NEXT_INSPECTION"], state="COMPLETED",
                               outcome="EXISTING_WORK_ORDER_FOUND", decisive=decisive["assessment_id"])
                    elif snapshot["contract"]["status"] != "ACTIVE" or not snapshot["contract"]["legacy_replacement_eligible"]:
                        decide("LEGACY", "NOT_ELIGIBLE", ["CONTRACT_NOT_ELIGIBLE"],
                               "The luminaire is legacy, but it is not eligible under the current contract.",
                               next_actions=["START_NEXT_INSPECTION"], state="COMPLETED",
                               outcome="NOT_CONTRACT_ELIGIBLE", decisive=decisive["assessment_id"])
                    else:
                        decide("LEGACY", "REPLACEMENT", ["LEGACY_CONFIRMED", "CONTRACT_ELIGIBLE"],
                               "The legacy luminaire is eligible for replacement. I can prepare the request.",
                               next_actions=["PREPARE_ACTION"], decisive=decisive["assessment_id"])
            self._save(db, snapshot)
            self.store.event(db, session["id"], inspection_id, "INSPECTION_RECONCILED", timestamp,
                             {"revision": snapshot["revision"], "state": snapshot["state"],
                              "decision_id": (snapshot["decision"] or {}).get("decision_id")})
            return self._enrich(db, snapshot)

    def _proposal_dict(self, row):
        payload = json.loads(row["payload"])
        return {
            "proposal_id": row["id"], "inspection_id": row["inspection_id"],
            "inspection_revision": payload["inspection_revision"], "decision_id": payload["decision_id"],
            "action_type": payload["action_type"], "exception_type": payload.get("exception_type"),
            "effect_summary": payload["effect_summary"], "payload_hash": payload["payload_hash"],
            "expires_at": now_iso(row["expires_at"]), "status": row["status"],
        }

    def _operation_dict(self, row):
        return {
            "operation_id": row["id"], "proposal_id": row["proposal_id"],
            "inspection_id": row["inspection_id"], "action_type": row["action_type"],
            "status": row["status"],
            "external_reference": json.loads(row["external_reference"]) if row["external_reference"] else None,
            "message": ("Request created in the external system." if row["status"] == "SUCCEEDED" else
                        "Request is processing." if row["status"] in {"QUEUED", "DISPATCHING"} else
                        "The external result is not yet confirmed." if row["status"] == "OUTCOME_UNKNOWN" else
                        "The external request failed before confirmation."),
            "last_error_code": row["last_error_code"],
            "next_poll_after_ms": 2000 if row["status"] in {"QUEUED", "DISPATCHING", "OUTCOME_UNKNOWN"} else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def prepare_action(self, session, inspection_id, request):
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            snapshot = self._inspection(db, session, inspection_id)
            self._revision(snapshot, request.expected_revision)
            existing = db.execute("SELECT * FROM proposals WHERE inspection_id=? AND request_id=?",
                                  (inspection_id, request.request_id)).fetchone()
            if existing:
                if existing["intent"] != intent_hash(request):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "This key was already used with different content.")
                return self._proposal_dict(existing)
            active = db.execute("SELECT * FROM proposals WHERE inspection_id=? AND status='ACTIVE'",
                                (inspection_id,)).fetchone()
            if active:
                if active["expires_at"] <= self.clock():
                    db.execute("UPDATE proposals SET status='EXPIRED' WHERE id=?", (active["id"],))
                else:
                    raise DomainError("ACTIVE_PROPOSAL", "A proposal is already awaiting confirmation.")
            if snapshot["state"] != "DECIDED" or not snapshot.get("decision"):
                raise DomainError("DECISION_REQUIRED", "Reconcile the inspection before preparing an action.")
            decision = snapshot["decision"]
            if (decision["disposition"] == "REPLACEMENT"
                    or decision.get("downstream_action") == "REPLACEMENT"):
                action_type, exception_type = "CREATE_REPLACEMENT", None
                effect_summary = (
                    f"Create a contract replacement request for pole {snapshot['pole_id']}; "
                    "the utility record has already been confirmed in the field."
                )
            elif decision["disposition"] in {"REGISTRY_MISMATCH", "MANUAL_REVIEW", "UNKNOWN_ASSET"}:
                action_type = (
                    "CREATE_REGISTRY_REVIEW"
                    if decision["disposition"] == "REGISTRY_MISMATCH"
                    and snapshot.get("workflow_version") == "field-reconciliation-v2"
                    else "CREATE_EXCEPTION"
                )
                exception_type = decision.get("exception_type") or "MANUAL_REVIEW"
                if action_type == "CREATE_REGISTRY_REVIEW":
                    registered = decision.get("registered_power_w")
                    observed = decision.get("observed_power_w")
                    power_detail = (
                        f" Registered load: {registered:g} W; verified load: {observed:g} W."
                        if registered is not None and observed is not None
                        else " Installed power is not yet verified."
                    )
                    effect_summary = (
                        f"Record a registry discrepancy for pole {snapshot['pole_id']}."
                        + power_detail
                    )
                else:
                    effect_summary = f"Create a {exception_type} review case for inspection {inspection_id}."
            else:
                raise DomainError("NO_ACTION_AVAILABLE", "The current state does not allow an external action.")
            body = {
                "inspection_id": inspection_id, "inspection_revision": snapshot["revision"],
                "decision_id": decision["decision_id"], "action_type": action_type,
                "exception_type": exception_type, "effect_summary": effect_summary,
                "fixture_id": snapshot["fixture_id"], "pole_id": snapshot["pole_id"],
                "contract_id": (snapshot.get("contract") or {}).get("contract_id"),
                "reconciliation_status": decision.get("reconciliation_status"),
                "registered_classification": decision.get("registered_classification"),
                "field_classification": decision.get("field_classification"),
                "registered_power_w": decision.get("registered_power_w"),
                "observed_power_w": decision.get("observed_power_w"),
                "power_delta_w": decision.get("power_delta_w"),
                "estimated_cycle_energy_delta_kwh": decision.get("estimated_cycle_energy_delta_kwh"),
            }
            body["payload_hash"] = digest(json.dumps(body, sort_keys=True, ensure_ascii=False))
            proposal_id = new_id("prp")
            expires_at = self.clock() + 120
            db.execute("INSERT INTO proposals(id,inspection_id,request_id,intent,payload,status,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (proposal_id, inspection_id, request.request_id, intent_hash(request), json.dumps(body),
                        "ACTIVE", expires_at, timestamp))
            snapshot.update(state="AWAITING_CONFIRMATION", active_proposal_id=proposal_id,
                            allowed_next_actions=["CONFIRM_ACTION"],
                            message=effect_summary + " Do you confirm sending it?")
            self._save(db, snapshot)
            self.store.event(db, session["id"], inspection_id, "ACTION_PROPOSAL_PREPARED", timestamp,
                             {"proposal_id": proposal_id, "action_type": action_type, "revision": snapshot["revision"]})
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            return self._proposal_dict(row)

    def _proposal_for_session(self, db, session, proposal_id):
        row = db.execute(
            "SELECT p.* FROM proposals p JOIN inspections i ON i.id=p.inspection_id WHERE p.id=? AND i.session_id=?",
            (proposal_id, session["id"]),
        ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Proposal not found.", 404)
        return row

    def confirm_action(self, session, proposal_id, request):
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            proposal = self._proposal_for_session(db, session, proposal_id)
            snapshot = self._inspection(db, session, proposal["inspection_id"])
            existing_operation = db.execute("SELECT * FROM operations WHERE proposal_id=?", (proposal_id,)).fetchone()
            if existing_operation and proposal["status"] == "CONFIRMED":
                return {"proposal_id": proposal_id, "decision": "CONFIRM", "inspection": self._enrich(db, snapshot),
                        "operation": self._operation_dict(existing_operation), "replayed": True}
            if proposal["status"] == "DECLINED":
                return {"proposal_id": proposal_id, "decision": "DECLINE", "inspection": self._enrich(db, snapshot),
                        "operation": None, "replayed": True}
            if proposal["status"] != "ACTIVE":
                raise DomainError("PROPOSAL_NOT_ACTIVE", "The proposal is no longer available.")
            if proposal["expires_at"] <= self.clock():
                db.execute("UPDATE proposals SET status='EXPIRED' WHERE id=?", (proposal_id,))
                raise DomainError("PROPOSAL_EXPIRED", "The proposal expired; prepare a new proposal.")
            self._revision(snapshot, request.expected_revision)
            if snapshot["state"] != "AWAITING_CONFIRMATION" or snapshot["active_proposal_id"] != proposal_id:
                raise DomainError("PROPOSAL_INVALIDATED", "The inspection changed; prepare a new proposal.")
            if request.decision == "DECLINE":
                db.execute("UPDATE proposals SET status='DECLINED' WHERE id=?", (proposal_id,))
                snapshot.update(state="COMPLETED", active_proposal_id=None, outcome="ACTION_DECLINED",
                                allowed_next_actions=["START_NEXT_INSPECTION"], message="The action was declined; no external record was created.")
                self._save(db, snapshot)
                self.store.event(db, session["id"], snapshot["inspection_id"], "ACTION_DECLINED", timestamp,
                                 {"proposal_id": proposal_id, "operator_utterance": request.operator_utterance})
                return {"proposal_id": proposal_id, "decision": "DECLINE", "inspection": self._enrich(db, snapshot),
                        "operation": None, "replayed": False}
            payload = json.loads(proposal["payload"])
            # Local uniqueness reservation. A competing session receives only a
            # generic conflict and never learns its identifiers.
            active_ops = db.execute(
                "SELECT o.*,p.payload FROM operations o JOIN proposals p ON p.id=o.proposal_id "
                "WHERE o.status IN ('QUEUED','DISPATCHING','OUTCOME_UNKNOWN','SUCCEEDED')"
            ).fetchall()
            for other in active_ops:
                other_payload = json.loads(other["payload"])
                same_business = (other_payload.get("fixture_id") == payload.get("fixture_id")
                                 and other_payload.get("contract_id") == payload.get("contract_id")
                                 and other_payload.get("action_type") == payload.get("action_type"))
                if same_business and other["id"] != (existing_operation["id"] if existing_operation else None):
                    raise DomainError("OPERATION_IN_PROGRESS", "A request is already in progress for this fixture.")
            operation_id = new_id("op")
            db.execute("UPDATE proposals SET status='CONFIRMED' WHERE id=?", (proposal_id,))
            db.execute("INSERT INTO operations(id,proposal_id,inspection_id,action_type,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (operation_id, proposal_id, snapshot["inspection_id"], payload["action_type"],
                        payload["payload_hash"], "QUEUED", timestamp, timestamp))
            snapshot.update(state="ACTION_PENDING", active_operation_id=operation_id, active_proposal_id=None,
                            allowed_next_actions=["QUERY_OPERATION"], message="Request confirmed and queued.")
            self._save(db, snapshot)
            self.store.event(db, session["id"], snapshot["inspection_id"], "ACTION_CONFIRMED", timestamp,
                             {"proposal_id": proposal_id, "operation_id": operation_id})
        self._dispatch(operation_id)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            snapshot = self._inspection(db, session, proposal["inspection_id"])
            return {"proposal_id": proposal_id, "decision": "CONFIRM", "inspection": self._enrich(db, snapshot),
                    "operation": self._operation_dict(row), "replayed": False}

    def _dispatch(self, operation_id):
        """Persist DISPATCHING before the network call; never retry an unknown outcome."""
        if self.external is None or not getattr(self.external, "configured", False):
            return
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not row or row["status"] != "QUEUED":
                return
            proposal = db.execute("SELECT payload FROM proposals WHERE id=?", (row["proposal_id"],)).fetchone()
            db.execute("UPDATE operations SET status='DISPATCHING',attempts=attempts+1,updated_at=? WHERE id=?",
                       (timestamp, operation_id))
            payload = json.loads(proposal["payload"])
        try:
            external_reference = self.external.dispatch(operation_id, payload)
        except ExternalNotConfigured:
            return
        except ExternalOutcomeUnknown:
            self._finish_operation(operation_id, "OUTCOME_UNKNOWN", "EXTERNAL_OUTCOME_UNKNOWN", None)
            return
        except ExternalDefiniteFailure as exc:
            self._finish_operation(operation_id, "FAILED_DEFINITE", exc.code, None)
            return
        self._finish_operation(operation_id, "SUCCEEDED", None, external_reference)

    def _finish_operation(self, operation_id, status, error_code, external_reference):
        timestamp = now_iso(self.clock())
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not row:
                return
            db.execute("UPDATE operations SET status=?,external_reference=?,last_error_code=?,updated_at=? WHERE id=?",
                       (status, json.dumps(external_reference) if external_reference else None, error_code, timestamp, operation_id))
            snapshot_row = db.execute("SELECT snapshot FROM inspections WHERE id=?", (row["inspection_id"],)).fetchone()
            if not snapshot_row:
                return
            snapshot = json.loads(snapshot_row["snapshot"])
            if status == "SUCCEEDED":
                snapshot.update(state="COMPLETED", outcome=(
                                    "REPLACEMENT_ORDER_CREATED"
                                    if row["action_type"] == "CREATE_REPLACEMENT"
                                    else "REGISTRY_REVIEW_CREATED"
                                    if row["action_type"] == "CREATE_REGISTRY_REVIEW"
                                    else "MANUAL_REVIEW_REQUIRED"
                                ),
                                allowed_next_actions=["START_NEXT_INSPECTION"], message="The external record was confirmed.")
            elif status == "OUTCOME_UNKNOWN":
                snapshot.update(state="ACTION_UNKNOWN", allowed_next_actions=["QUERY_OPERATION"],
                                message="The external record creation is not confirmed; the operation identifier was queried.")
            elif status == "FAILED_DEFINITE":
                snapshot.update(state="DECIDED", active_operation_id=None, allowed_next_actions=["PREPARE_ACTION"],
                                message="The external create definitively failed; a new proposal is required.")
            self._save(db, snapshot)

    def get_operation_status(self, session, operation_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT o.* FROM operations o JOIN inspections i ON i.id=o.inspection_id WHERE o.id=? AND i.session_id=?",
                (operation_id, session["id"]),
            ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Operation not found.", 404)
        if row["status"] == "OUTCOME_UNKNOWN" and self.external is not None and getattr(self.external, "configured", False):
            try:
                reference = self.external.lookup(operation_id)
            except ExternalIntegrityError:
                self._set_operation_error(operation_id, "MULTIPLE_EXTERNAL_MATCHES")
            except ExternalOutcomeUnknown:
                pass
            except ExternalDefiniteFailure as exc:
                self._set_operation_error(operation_id, exc.code)
            else:
                if reference:
                    self._finish_operation(operation_id, "SUCCEEDED", None, reference)
            with self.store.connect() as db:
                row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        return self._operation_dict(row)

    def _set_operation_error(self, operation_id, error_code):
        with self.store.transaction() as db:
            db.execute("UPDATE operations SET last_error_code=?,updated_at=? WHERE id=?",
                       (error_code, now_iso(self.clock()), operation_id))

    def _enrich(self, db, snapshot):
        # G0 extensions: permit a browser reload/tool state read to recover photo IDs
        # and committed assessments. None of these fields is an adapter input.
        # Inspections exhausted before visual-v4 were stored with no next action.
        # Expose a safe transient migration so the agent can reconcile them on read.
        if (snapshot.get("state") == "NEEDS_EVIDENCE"
                and snapshot.get("remaining_image_attempts") == 0
                and not snapshot.get("decision")
                and not snapshot.get("allowed_next_actions")):
            snapshot["allowed_next_actions"] = ["RECONCILE"]
            snapshot["message"] = "All three photos were inconclusive. I will prepare a manual-review route; no external request has been created."
            snapshot["recovery_hint"] = "RECONCILE_EXHAUSTED_EVIDENCE"
        snapshot["evidence"] = [json.loads(r[0]) for r in db.execute("SELECT receipt FROM evidence WHERE inspection_id=? ORDER BY rowid", (snapshot["inspection_id"],))]
        snapshot["assessments"] = [json.loads(r[0]) for r in db.execute("SELECT result FROM assessments WHERE inspection_id=? ORDER BY rowid", (snapshot["inspection_id"],))]
        snapshot["events"] = [{"kind": r[0], "created_at": r[1], "detail": json.loads(r[2])} for r in db.execute(
            "SELECT kind,created_at,detail FROM events WHERE inspection_id=? ORDER BY id", (snapshot["inspection_id"],))]
        conversation_id = snapshot.get("conversation_id")
        conversation = db.execute(
            "SELECT * FROM conversation_analyses WHERE conversation_id=?", (conversation_id,)
        ).fetchone() if conversation_id else None
        snapshot["conversation_analysis"] = ({
            "conversation_id": conversation["conversation_id"],
            "agent_id": conversation["agent_id"],
            "agent_name": conversation["agent_name"],
            "version_id": conversation["version_id"],
            "branch_id": conversation["branch_id"],
            "status": conversation["status"],
            "event_timestamp": conversation["event_timestamp"],
            "duration_seconds": conversation["duration_seconds"],
            "transcript": json.loads(conversation["transcript"]),
            "analysis": json.loads(conversation["analysis"]),
            "metadata": json.loads(conversation["metadata"]),
            "received_at": conversation["received_at"],
        } if conversation else None)
        return snapshot

    def receive_post_call(self, raw_body: bytes, signature_header: str | None):
        secret = self.settings.elevenlabs_webhook_secret
        if not secret:
            raise DomainError(
                "WEBHOOK_NOT_CONFIGURED",
                "The post-call webhook secret is not configured.",
                503,
            )
        if len(raw_body) > 2 * 1024 * 1024:
            raise DomainError("PAYLOAD_TOO_LARGE", "Webhook payload exceeds the limit.", 413)
        try:
            signature_parts = dict(
                part.split("=", 1) for part in (signature_header or "").split(",") if "=" in part
            )
            timestamp_text = signature_parts["t"]
            supplied = "v0=" + signature_parts["v0"]
            timestamp = int(timestamp_text)
        except (KeyError, TypeError, ValueError):
            raise DomainError("INVALID_WEBHOOK_SIGNATURE", "Invalid webhook signature.", 401)
        if timestamp * 1000 < int(self.clock() * 1000) - 30 * 60 * 1000:
            raise DomainError("INVALID_WEBHOOK_SIGNATURE", "Expired webhook signature.", 401)
        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            raise DomainError("INVALID_WEBHOOK_PAYLOAD", "Invalid webhook payload.", 400)
        expected = "v0=" + hmac.new(
            secret.encode("utf-8"),
            f"{timestamp_text}.{body_text}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(expected, supplied):
            raise DomainError("INVALID_WEBHOOK_SIGNATURE", "Invalid webhook signature.", 401)
        try:
            event = json.loads(body_text)
        except json.JSONDecodeError:
            raise DomainError("INVALID_WEBHOOK_PAYLOAD", "Invalid webhook payload.", 400)
        if event.get("type") != "post_call_transcription":
            return {"status": "ignored", "event_type": event.get("type")}
        data = event.get("data")
        if not isinstance(data, dict):
            raise DomainError("INVALID_WEBHOOK_PAYLOAD", "Post-call data is missing.", 400)
        conversation_id = data.get("conversation_id")
        agent_id = data.get("agent_id")
        if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 160:
            raise DomainError("INVALID_WEBHOOK_PAYLOAD", "Invalid Conversation ID.", 400)
        if not isinstance(agent_id, str) or not agent_id:
            raise DomainError("INVALID_WEBHOOK_PAYLOAD", "Invalid Agent ID.", 400)
        if self.settings.agent_id and agent_id != self.settings.agent_id:
            raise DomainError("WRONG_WEBHOOK_AGENT", "The event belongs to another agent.", 403)
        transcript = data.get("transcript") if isinstance(data.get("transcript"), list) else []
        analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        received_at = now_iso(self.clock())
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO conversation_analyses(
                       conversation_id,agent_id,agent_name,version_id,branch_id,status,
                       event_timestamp,duration_seconds,transcript,analysis,metadata,received_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       agent_id=excluded.agent_id,agent_name=excluded.agent_name,
                       version_id=excluded.version_id,branch_id=excluded.branch_id,
                       status=excluded.status,event_timestamp=excluded.event_timestamp,
                       duration_seconds=excluded.duration_seconds,transcript=excluded.transcript,
                       analysis=excluded.analysis,metadata=excluded.metadata,received_at=excluded.received_at""",
                (
                    conversation_id,
                    agent_id,
                    data.get("agent_name"),
                    data.get("version_id"),
                    data.get("branch_id"),
                    str(data.get("status") or "unknown"),
                    event.get("event_timestamp"),
                    metadata.get("call_duration_secs"),
                    json.dumps(transcript, ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    received_at,
                ),
            )
            session = db.execute(
                "SELECT id FROM sessions WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            inspection = db.execute(
                "SELECT id FROM inspections WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                (session["id"],),
            ).fetchone() if session else None
            if session:
                self.store.event(
                    db,
                    session["id"],
                    inspection["id"] if inspection else None,
                    "POST_CALL_ANALYSIS_RECEIVED",
                    received_at,
                    {
                        "conversation_id": conversation_id,
                        "agent_id": agent_id,
                        "version_id": data.get("version_id"),
                        "call_successful": analysis.get("call_successful"),
                        "evaluation_count": len(analysis.get("evaluation_criteria_results") or {}),
                    },
                )
        return {
            "status": "received",
            "conversation_id": conversation_id,
            "associated": bool(session),
        }

    def get_inspection(self, session, inspection_id):
        with self.store.connect() as db:
            return self._enrich(db, self._inspection(db, session, inspection_id))

    def get_session(self, session, session_id):
        self._own_session(session, session_id)
        with self.store.connect() as db:
            row = db.execute("SELECT snapshot FROM inspections WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
            return {"session_id": session_id, "conversation_id": session["conversation_id"], "expires_at": now_iso(session["expires_at"]),
                    "active_inspection": self._enrich(db, json.loads(row[0])) if row else None}
