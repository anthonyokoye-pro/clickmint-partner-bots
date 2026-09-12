import tempfile
from pathlib import Path
from admin_service import build_admin_app


class API:
    pass


def test_startup_factory_fails_closed_and_builds_valid_app():
    root = Path(__file__).parent
    directory = tempfile.TemporaryDirectory(prefix="clickmint-admin-service-")
    try:
        try:
            build_admin_app(API(), root=root, bot_token="bad", owner_id="0",
                            allowed_origins=["http://unsafe"], idempotency_path=Path(directory.name) / "api.sqlite3")
        except RuntimeError:
            pass
        else:
            raise AssertionError("invalid startup configuration accepted")
        app = build_admin_app(API(), root=root, bot_token="x" * 40, owner_id="123",
                              allowed_origins=["https://admin.example"], idempotency_path=Path(directory.name) / "api.sqlite3")
        assert app.idempotency_store is not None
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_startup_factory_fails_closed_and_builds_valid_app()
    print("PASS test_startup_factory_fails_closed_and_builds_valid_app")
