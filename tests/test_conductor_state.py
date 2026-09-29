import threading

from modules.conductor_state import ConductorState


def test_atomic_assignment_gives_order_to_only_one_concurrent_loader(tmp_path):
    state = ConductorState(tmp_path / "conductor_state.json")
    state.replace_queued_orders([{"id": "order-1", "tags": [], "name": "#1001"}])
    loader_ids = [f"loader-{index}" for index in range(12)]
    for loader_id in loader_ids:
        state.register_loader(loader_id, {"status": "ready"})

    barrier = threading.Barrier(len(loader_ids))
    assignments = []
    results_lock = threading.Lock()

    def assign(loader_id):
        barrier.wait()
        assignment = state.atomic_assign_order(loader_id)
        if assignment is not None:
            with results_lock:
                assignments.append(assignment)

    threads = [threading.Thread(target=assign, args=(loader_id,)) for loader_id in loader_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(assignments) == 1
    assert assignments[0]["status"] == "processing"
    assert state.queued_orders == {}
    assert list(state.active_assignments) == ["order-1"]


def test_state_reload_preserves_assignments_and_loader_state(tmp_path):
    path = tmp_path / "conductor_state.json"
    state = ConductorState(path)
    state.replace_queued_orders([{"id": "order-1", "tags": []}])
    state.register_loader("loader-a", {"status": "ready"})
    state.atomic_assign_order("loader-a")

    restored = ConductorState(path)

    assert restored.queued_orders == {}
    assert restored.active_assignments["order-1"]["loader_id"] == "loader-a"
    assert restored.active_assignments["order-1"]["status"] == "processing"
    assert restored.registered_loaders["loader-a"]["status"] == "busy"


def test_completed_orders_retry_until_absent_from_unfulfilled_snapshot(tmp_path):
    state = ConductorState(tmp_path / "conductor_state.json")
    state.replace_queued_orders([{"id": "order-1", "tags": []}])
    state.register_loader("loader-a", {"status": "ready"})
    state.atomic_assign_order("loader-a")
    state.complete_assignment("order-1", "unavailable")

    pending = state.reconcile_completed_orders({"order-1"})
    assert [item["order_id"] for item in pending] == ["order-1"]

    assert state.reconcile_completed_orders(set()) == []
    assert state.completed_orders == {}


def test_stale_busy_loader_becomes_interrupted_without_reassignment(tmp_path):
    state = ConductorState(tmp_path / "conductor_state.json")
    state.replace_queued_orders([
        {"id": "order-1", "tags": []},
        {"id": "order-2", "tags": []},
    ])
    state.register_loader("loader-a", {"status": "ready", "last_seen": 0})
    state.atomic_assign_order("loader-a")
    with state.lock:
        state.active_assignments["order-1"]["assigned_at"] = 0

    assert state.mark_stale_loaders(30) == 1
    assert state.registered_loaders["loader-a"]["status"] == "interrupted"
    assert state.active_assignments["order-1"]["status"] == "interrupted"

    state.register_loader("loader-a", {"status": "ready", "last_seen": 9999999999})
    assert state.atomic_assign_order("loader-a") is None
    assert list(state.queued_orders) == ["order-2"]


def test_acknowledged_units_survive_conductor_restart(tmp_path):
    path = tmp_path / "conductor_state.json"
    state = ConductorState(path)
    state.replace_queued_orders([{"id": "order-1", "tags": []}])
    state.register_loader("loader-a", {"status": "ready"})
    state.atomic_assign_order("loader-a")
    state.cache_assignment("order-1", {
        "order_id": "order-1",
        "print_units": [{"index": 0}, {"index": 1}],
    }, str(tmp_path / "order.zip"))
    state.acknowledge_print_unit("loader-a", "order-1", 0)

    restored = ConductorState(path)

    assert restored.active_assignments["order-1"]["completed_units"] == [0]