import tempfile
from pathlib import Path
from performance_snapshots import PerformanceSnapshotRepository


def test_snapshots_are_durable_and_explainable():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-credibility-")
    try:
        repo = PerformanceSnapshotRepository(Path(directory.name) / "credibility.sqlite3")
        first = repo.record_task_outcome(destination_id="channel-a", user_id=7,
                                         category="Guides", success=True)
        assert first["sample_size"] == 1
        assert first["tier"] in {"EMERGING", "ESTABLISHED", "PROVISIONAL"}
        repo.record_task_outcome(destination_id="channel-a", user_id=7,
                                 category="Guides", success=False)
        current = repo.current("channel-a")
        assert current["sample_size"] == 2
        assert len(repo.history("channel-a")) == 2
        explanation = repo.explain("channel-a")
        assert explanation["status"] == current["tier"]
        assert explanation["confidence"] >= 0
    finally:
        directory.cleanup()


def test_repeated_failures_temporarily_suspend_destination():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-credibility-")
    try:
        repo = PerformanceSnapshotRepository(Path(directory.name) / "credibility.sqlite3")
        for _ in range(3):
            repo.record_task_outcome(destination_id="channel-fail", user_id=7,
                                     category="Guides", success=False)
        assert repo.is_suspended("channel-fail")
        assert "failures" in repo.control("channel-fail")["reason"]
        assert repo.clear_cooldown("channel-fail", reason="admin review")
        assert not repo.is_suspended("channel-fail")
        history = repo.intervention_history("channel-fail")
        assert [item["action"] for item in history] == ["COOLDOWN_CLEARED", "COOLDOWN_SET"]
    finally:
        directory.cleanup()


def test_unknown_destination_is_provisional():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-credibility-")
    try:
        repo = PerformanceSnapshotRepository(Path(directory.name) / "credibility.sqlite3")
        explanation = repo.explain("unknown")
        assert explanation["status"] == "PROVISIONAL"
    finally:
        directory.cleanup()


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("PASS", name)
