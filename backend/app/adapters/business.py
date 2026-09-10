"""Fictional Santa Aurora source used by the deterministic demo policy.

This adapter is deliberately shaped like a source boundary. It contains no
LLM input and no write capability. Replacing it with Airtable reads later must
preserve the same typed snapshots and revision semantics.
"""

from copy import deepcopy


class DemoBusinessSource:
    municipality = "Santa Aurora"
    contract_id = "IP-2026-014"

    # Fictional scenario parameter, not a claim about a real municipality.
    # Store whole minutes instead of decimal hours so values such as 11h52 can
    # never be misread as 11.52 hours. Production adapters should supply the
    # municipality's authoritative time basis and revision.
    _billing = {
        "daily_minutes": 687,
        "days_in_cycle": 30,
        "time_basis": "FICTIONAL_MUNICIPAL_PARAMETER",
        "revision": "billing-scenario-2026-08",
        "label": "Santa Aurora · fictional registry cycle",
    }

    _fixtures = {
        "F-18427-01": {
            "pole_id": "18427", "asset_id": "A-18427",
            "identity": {"address": "Rua das Palmeiras, 322", "municipality": municipality,
                         "latitude": -27.2168, "longitude": -49.6451, "gps_status": "REGISTERED"},
            "registry": {"classification": "LEGACY", "lamp_power_w": 70,
                          "auxiliary_type": "BALLAST", "auxiliary_power_w": 30,
                          "registered_power_w": 100, "registered_subtype": "HPS",
                          "last_updated_at": "2025-11-04", "revision": "register-2025-11-04"},
            "operational": {"work_order_id": "OS-2026-0618-27", "work_order_type": "LED_MODERNIZATION",
                            "work_order_status": "COMPLETED", "completed_at": "2026-06-18",
                            "registry_sync_status": "PENDING", "queue_status": "LIKELY_DIVERGENCE",
                            "next_step": "VERIFY_FIELD"},
            "contract": {"contract_id": contract_id, "modernization_cycle_id": "cycle-2026-led",
                         "status": "ACTIVE", "legacy_replacement_eligible": True,
                         "revision": "contract-2026-01"},
        },
        "F-18428-01": {
            "pole_id": "18428", "asset_id": "A-18428",
            "identity": {"address": "Avenida Central, 88", "municipality": municipality,
                         "latitude": -27.2175, "longitude": -49.6462, "gps_status": "REGISTERED"},
            "registry": {"classification": "LEGACY", "lamp_power_w": 70,
                          "auxiliary_type": "BALLAST", "auxiliary_power_w": 15,
                          "registered_power_w": 85, "registered_subtype": "HPS",
                          "last_updated_at": "2026-07-12", "revision": "register-2026-07-12"},
            "operational": {"work_order_id": None, "work_order_type": None,
                            "work_order_status": None, "completed_at": None,
                            "registry_sync_status": "CURRENT", "queue_status": "IN_FIELD",
                            "next_step": "CAPTURE_EVIDENCE"},
            "contract": {"contract_id": contract_id, "modernization_cycle_id": "cycle-2026-led",
                         "status": "ACTIVE", "legacy_replacement_eligible": True,
                         "revision": "contract-2026-01"},
        },
        "F-18429-01": {
            "pole_id": "18429", "asset_id": "A-18429",
            "identity": {"address": "Rua do Mercado, 41", "municipality": municipality,
                         "latitude": -27.2183, "longitude": -49.6446, "gps_status": "REGISTERED"},
            "registry": {"classification": "LEGACY", "lamp_power_w": 70,
                          "auxiliary_type": "BALLAST", "auxiliary_power_w": 30,
                          "registered_power_w": 100, "registered_subtype": "HPS",
                          "last_updated_at": "2026-06-30", "revision": "register-2026-06-30"},
            "operational": {"work_order_id": "OS-2026-0702-29", "work_order_type": "LED_MODERNIZATION",
                            "work_order_status": "OPEN", "completed_at": None,
                            "registry_sync_status": "CURRENT", "queue_status": "FIELD_PENDING",
                            "next_step": "VERIFY_FIELD"},
            "contract": {"contract_id": contract_id, "modernization_cycle_id": "cycle-2026-led",
                         "status": "ACTIVE", "legacy_replacement_eligible": True,
                         "revision": "contract-2026-01"},
        },
        "F-18430-01": {
            "pole_id": "18430", "asset_id": "A-18430",
            "identity": {"address": "Praça da Estação, 12", "municipality": municipality,
                         "latitude": -27.2191, "longitude": -49.6438, "gps_status": "REGISTERED"},
            "registry": {"classification": "LED", "lamp_power_w": 80,
                          "auxiliary_type": "DRIVER_INTEGRATED", "auxiliary_power_w": 0,
                          "registered_power_w": 80, "registered_subtype": "INTEGRATED_LED",
                          "last_updated_at": "2026-08-03", "revision": "register-2026-08-03"},
            "operational": {"work_order_id": "OS-2026-0520-30", "work_order_type": "LED_MODERNIZATION",
                            "work_order_status": "COMPLETED", "completed_at": "2026-05-20",
                            "registry_sync_status": "CURRENT", "queue_status": "READY_TO_CONFIRM",
                            "next_step": "RECONCILE"},
            "contract": {"contract_id": contract_id, "modernization_cycle_id": "cycle-2026-led",
                         "status": "ACTIVE", "legacy_replacement_eligible": True,
                         "revision": "contract-2026-01"},
        },
        "F-18431-01": {
            "pole_id": "18431", "asset_id": "A-18431",
            "identity": {"address": "Rua das Acácias, 510", "municipality": municipality,
                         "latitude": -27.2202, "longitude": -49.6429, "gps_status": "REGISTERED"},
            "registry": {"classification": "LED", "lamp_power_w": 120,
                          "auxiliary_type": "DRIVER_INTEGRATED", "auxiliary_power_w": 0,
                          "registered_power_w": 120, "registered_subtype": "INTEGRATED_LED",
                          "last_updated_at": "2026-04-19", "revision": "register-2026-04-19"},
            "operational": {"work_order_id": None, "work_order_type": None,
                            "work_order_status": None, "completed_at": None,
                            "registry_sync_status": "CURRENT", "queue_status": "OPERATION_EXCEPTION",
                            "next_step": "VERIFY_OPERATING_STATE"},
            "contract": {"contract_id": contract_id, "modernization_cycle_id": "cycle-2026-led",
                         "status": "ACTIVE", "legacy_replacement_eligible": True,
                         "revision": "contract-2026-01"},
        },
    }

    def __init__(self, existing_orders=None):
        # The default demo source has no external order. Tests or a later
        # read adapter may inject a real, already-seeded reference here. It is
        # intentionally not hard-coded into the fixture registry.
        self._existing_orders = deepcopy(existing_orders or {})

    def resolve(self, fixture_id):
        fixture = self._fixtures.get(fixture_id)
        if fixture is None:
            return None
        result = deepcopy(fixture)
        result["billing"] = deepcopy(self._billing)
        return result

    def find_existing_order(self, fixture_id):
        """Return a normalized external reference when one is already open."""
        reference = self._existing_orders.get(fixture_id)
        return deepcopy(reference) if reference else None

    def list_assets(self):
        """Return stable pole dossiers; inspections remain versioned events over them."""
        assets = []
        for fixture_id, fixture in self._fixtures.items():
            assets.append({
                "fixture_id": fixture_id,
                "pole_id": fixture["pole_id"],
                "asset_id": fixture["asset_id"],
                "identity": deepcopy(fixture["identity"]),
                "registry": deepcopy(fixture["registry"]),
                "operational": deepcopy(fixture["operational"]),
            })
        return assets
