from backend.beacon import (
    BeaconListener,
    BeaconPublisher,
    decode_beacon_message,
    encode_beacon_message,
    goal_from_path,
    publish_snapshot,
)


class FakePublisher:
    def __init__(self, fail_ids: set[str]):
        self.fail_ids = fail_ids
        self.published: list[tuple] = []

    def publish(self, amr_id, x, y, goal):
        if amr_id in self.fail_ids:
            raise OSError("no route to host")
        self.published.append((amr_id, x, y, goal))


def test_publish_snapshot_sends_a_beacon_per_amr():
    publisher = FakePublisher(fail_ids=set())
    snapshot = [
        {"id": "amr-1", "position": {"x": 1.0, "y": 2.0}, "path": []},
        {"id": "amr-2", "position": {"x": 3.0, "y": 4.0}, "path": ["n3"]},
    ]

    publish_snapshot(publisher, snapshot)

    assert publisher.published == [
        ("amr-1", 1.0, 2.0, None),
        ("amr-2", 3.0, 4.0, "n3"),
    ]


def test_publish_snapshot_continues_after_one_amr_send_fails():
    publisher = FakePublisher(fail_ids={"amr-1"})
    snapshot = [
        {"id": "amr-1", "position": {"x": 1.0, "y": 2.0}, "path": []},
        {"id": "amr-2", "position": {"x": 3.0, "y": 4.0}, "path": ["n3"]},
    ]

    publish_snapshot(publisher, snapshot)

    assert publisher.published == [("amr-2", 3.0, 4.0, "n3")]


def test_publish_snapshot_warns_only_once_per_amr_on_repeated_failure(capsys):
    publisher = FakePublisher(fail_ids={"amr-1"})
    snapshot = [{"id": "amr-1", "position": {"x": 1.0, "y": 2.0}, "path": []}]
    warned: set[str] = set()

    for _ in range(5):
        publish_snapshot(publisher, snapshot, warned)

    stderr_lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert len(stderr_lines) == 1
    assert "amr-1" in stderr_lines[0]


def test_goal_from_path_returns_last_node():
    assert goal_from_path(["n2", "n3"]) == "n3"


def test_goal_from_path_returns_none_when_idle():
    assert goal_from_path([]) is None


def test_encode_decode_roundtrip_with_goal():
    payload = encode_beacon_message("amr-1", 1.5, 2.5, "n3")

    assert decode_beacon_message(payload) == {
        "amr_id": "amr-1",
        "x": 1.5,
        "y": 2.5,
        "goal": "n3",
    }


def test_encode_decode_roundtrip_without_goal():
    payload = encode_beacon_message("amr-1", 0.0, 0.0, None)

    assert decode_beacon_message(payload) == {
        "amr_id": "amr-1",
        "x": 0.0,
        "y": 0.0,
        "goal": None,
    }


def test_publisher_socket_has_a_send_timeout():
    # A blocking sendto() with no timeout can freeze the whole asyncio event
    # loop forever if the OS ever stalls a broadcast send (observed on real
    # networks, e.g. macOS's Local Network privacy controls) — Python signal
    # handling can't interrupt a thread stuck in a blocking C syscall, so
    # this isn't just slow, it makes Ctrl+C/SIGTERM stop working too. A
    # socket timeout bounds the call so a stall becomes a fast OSError
    # instead, which publish_snapshot already catches and logs.
    publisher = BeaconPublisher(port=0)
    try:
        timeout = publisher._sock.gettimeout()
        assert timeout is not None
        assert 0 < timeout <= 5
    finally:
        publisher.close()


def test_publisher_and_listener_roundtrip_over_loopback():
    listener = BeaconListener(port=0)
    publisher = BeaconPublisher(port=listener.port, broadcast_address="127.0.0.1")
    try:
        publisher.publish("amr-1", 1.5, 2.5, "n3")
        message = listener.receive(timeout=1.0)

        assert message == {"amr_id": "amr-1", "x": 1.5, "y": 2.5, "goal": "n3"}
    finally:
        publisher.close()
        listener.close()
