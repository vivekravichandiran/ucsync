"""MI storage credentials are created via REST (CREATE STORAGE CREDENTIAL is not SQL)."""
from uc_sync.package_import import PackageImportEngine


class _FakeWS:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
    def post(self, path, body):
        self.calls.append((path, body))
        if self.fail:
            raise RuntimeError(self.fail)
        return {"name": body["name"]}


def _engine(ws):
    return PackageImportEngine("/tmp/nonexistent", sql_executor=None, workspace_client=ws)


def test_storage_credential_created_via_rest():
    ws = _FakeWS()
    ddl = ["CREATE STORAGE CREDENTIAL IF NOT EXISTS `cred` WITH AZURE_MANAGED_IDENTITY "
           "(ACCESS_CONNECTOR_ID = '/subscriptions/T/connectors/ac')"]
    status, msg = _engine(ws)._create_storage_credential_via_rest(ddl)
    assert status == "SUCCESS"
    assert ws.calls[0][0] == "/api/2.1/unity-catalog/storage-credentials"
    assert ws.calls[0][1]["name"] == "cred"
    assert ws.calls[0][1]["azure_managed_identity"]["access_connector_id"].endswith("/ac")


def test_existing_credential_is_skip():
    ws = _FakeWS(fail="already exists")
    ddl = ["CREATE STORAGE CREDENTIAL `cred` WITH AZURE_MANAGED_IDENTITY (ACCESS_CONNECTOR_ID = '/x/ac')"]
    status, _ = _engine(ws)._create_storage_credential_via_rest(ddl)
    assert status == "SUCCESS"
