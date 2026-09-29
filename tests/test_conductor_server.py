import json
import urllib.error
import urllib.request

from modules.conductor_server import ConductorHTTPServer


class FakeService:
    def __init__(self, bundle_path):
        self.loaders = {}
        self.bundle_path = bundle_path
        self.completed_units = []

    def register_loader(self, loader_id, details):
        self.loaders[loader_id] = details

    def update_loader(self, loader_id, details):
        self.loaders[loader_id].update(details)

    def assignment_for_loader(self, loader_id):
        return None

    def heartbeat(self, loader_id):
        return None

    def bundle_for_loader(self, loader_id):
        return self.bundle_path

    def complete_print_unit(self, loader_id, order_id, unit_index):
        self.completed_units.append((loader_id, order_id, unit_index))


def _request(url, token=None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(request, timeout=2)


def test_http_server_authenticates_registers_loader_and_streams_bundle(tmp_path):
    bundle_path = tmp_path / "job.zip"
    bundle_path.write_bytes(b"zip-content")
    service = FakeService(bundle_path)
    server = ConductorHTTPServer(service, "127.0.0.1", 0, "shared-token")
    server.start()
    host, port = server.address
    root = f"http://{host}:{port}/api/v1"
    try:
        try:
            _request(f"{root}/health")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Unauthenticated request unexpectedly succeeded")

        response = _request(
            f"{root}/loaders/register",
            token="shared-token",
            payload={"loader_id": "loader-1", "status": "unavailable"},
        )
        assert json.loads(response.read())["status"] == "registered"
        assert service.loaders["loader-1"]["status"] == "unavailable"

        response = _request(
            f"{root}/loaders/loader-1/assignment/bundle", token="shared-token"
        )
        assert response.headers["Content-Type"] == "application/zip"
        assert response.read() == b"zip-content"

        response = _request(
            f"{root}/loaders/loader-1/assignments/order-1/unit-complete",
            token="shared-token",
            payload={"unit_index": 3},
        )
        assert json.loads(response.read())["status"] == "unit_completed"
        assert service.completed_units == [("loader-1", "order-1", 3)]
    finally:
        server.stop()