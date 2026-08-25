"""Mapping file carries the target access-connector id for SC creation."""
from uc_sync.location_mapping import parse_location_mappings
from uc_sync.mapping import MappingResolver


def test_target_access_connector_id_flows_through():
    rows = [{
        "source_location": "abfss://c@src.dfs.core.windows.net/x",
        "target_location": "abfss://c@tgt.dfs.core.windows.net/x",
        "target_external_location": "el", "target_credential": "cred",
        "target_access_connector_id": "/subscriptions/TGT/connectors/ac",
    }]
    mappings = [m.to_dict() for m in parse_location_mappings(rows)]
    r = MappingResolver({"location_mappings": mappings})
    assert r.target_access_connector_id() == "/subscriptions/TGT/connectors/ac"
    assert r.rewrite_location("abfss://c@src.dfs.core.windows.net/x/t") == (
        "abfss://c@tgt.dfs.core.windows.net/x/t")


def test_per_location_connector_resolves_distinct_connectors():
    """Per-catalog setups map each source storage account to its OWN target
    connector; the resolver must pick the row matching the given location, not
    the first row (regression: all credentials got the first/gov connector)."""
    rows = [
        {
            "source_location": "abfss://data@gov.dfs.core.windows.net",
            "target_location": "abfss://data@tgtgov.dfs.core.windows.net",
            "target_access_connector_id": "/subscriptions/TGT/connectors/gov",
        },
        {
            "source_location": "abfss://data@fin.dfs.core.windows.net",
            "target_location": "abfss://data@tgtfin.dfs.core.windows.net",
            "target_access_connector_id": "/subscriptions/TGT/connectors/fin",
        },
        {
            "source_location": "abfss://data@sal.dfs.core.windows.net",
            "target_location": "abfss://data@tgtsal.dfs.core.windows.net",
            "target_access_connector_id": "/subscriptions/TGT/connectors/sal",
        },
    ]
    mappings = [m.to_dict() for m in parse_location_mappings(rows)]
    r = MappingResolver({"location_mappings": mappings})
    # Each source account resolves to its own connector (incl. a child path).
    assert r.target_access_connector_id_for_location(
        "abfss://data@fin.dfs.core.windows.net"
    ) == "/subscriptions/TGT/connectors/fin"
    assert r.target_access_connector_id_for_location(
        "abfss://data@sal.dfs.core.windows.net/orders"
    ) == "/subscriptions/TGT/connectors/sal"
    # The context-free fallback still returns the first row (used only when the
    # credential's source location is unknown).
    assert r.target_access_connector_id() == "/subscriptions/TGT/connectors/gov"
    # An unmapped location has no connector.
    assert r.target_access_connector_id_for_location(
        "abfss://data@other.dfs.core.windows.net"
    ) is None


def test_minimal_row_without_target_names_parses():
    # Names are never mapped, so target_external_location / target_credential are
    # optional; only the path rewrite (+ connector id) is required.
    rows = [{
        "source_location": "abfss://c@src.dfs.core.windows.net/x",
        "target_location": "abfss://c@tgt.dfs.core.windows.net/x",
        "target_access_connector_id": "/subscriptions/TGT/connectors/ac",
    }]
    mappings = [m.to_dict() for m in parse_location_mappings(rows)]
    r = MappingResolver({"location_mappings": mappings})
    assert r.target_access_connector_id() == "/subscriptions/TGT/connectors/ac"
    assert r.rewrite_location("abfss://c@src.dfs.core.windows.net/x/t") == (
        "abfss://c@tgt.dfs.core.windows.net/x/t")
