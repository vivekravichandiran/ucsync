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
