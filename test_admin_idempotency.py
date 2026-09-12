import tempfile
from pathlib import Path
from admin_idempotency import IdempotencyStore


def test_persistent_replay_record():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-idem-")
    try:
        path = Path(directory.name) / "admin.sqlite3"
        first = IdempotencyStore(path)
        first.put("key", "200 OK", {"status": "approved"})
        second = IdempotencyStore(path)
        assert second.get("key") == ("200 OK", {"status": "approved"})
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_persistent_replay_record()
    print("PASS test_persistent_replay_record")
