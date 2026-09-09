import tempfile
from pathlib import Path

from audience import AudienceDirectory, AudienceQuery
from store import JsonStore


def test_reward_audience_resolves_unique_user_ids_from_store():
    with tempfile.TemporaryDirectory() as directory:
        store = JsonStore(str(Path(directory) / "state.json"))
        store["ledger"] = {
            "@one": {"user_id": 10, "status": "ACTIVE"},
            "@duplicate": {"user_id": 10, "status": "ACTIVE"},
            "@two": {"user_id": 20, "status": "RESTRICTED"},
            "@missing": {"status": "ACTIVE"},
        }
        store.sync()
        audience = AudienceDirectory(store)
        assert audience.recipient_ids() == ["10", "20"]
        assert audience.recipient_ids(AudienceQuery(status="ACTIVE")) == ["10"]


def test_non_reward_scope_is_not_silently_mixed():
    with tempfile.TemporaryDirectory() as directory:
        store = JsonStore(str(Path(directory) / "state.json"))
        store["ledger"] = {"@one": {"user_id": 10}}
        store.sync()
        assert AudienceDirectory(store).recipient_ids(AudienceQuery(scope="partnership")) == []


if __name__ == "__main__":
    test_reward_audience_resolves_unique_user_ids_from_store()
    test_non_reward_scope_is_not_silently_mixed()
    print("PASS audience tests")
