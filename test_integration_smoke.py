import tempfile
from integration_smoke import smoke_initialize


def test_smoke_initializes_all_nonfinancial_stores():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-smoke-")
    try:
        result = smoke_initialize(directory.name)
        assert set(result["stores"]) == {"mint", "tasks", "broadcast", "credibility", "admin_api"}
        assert result["scopes_isolated"]
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_smoke_initializes_all_nonfinancial_stores()
    print("PASS test_smoke_initializes_all_nonfinancial_stores")
