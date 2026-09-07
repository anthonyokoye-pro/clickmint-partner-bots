from pathlib import Path
from admin_deploy import admin_preflight


def test_preflight_rejects_bad_origin_and_accepts_repo_artifacts():
    root = Path(__file__).parent
    errors = admin_preflight(root, bot_token="short", owner_id="nope", allowed_origins=["http://localhost"])
    assert len(errors) >= 3
    errors = admin_preflight(root, bot_token="x" * 40, owner_id="123", allowed_origins=["https://admin.example"])
    assert errors == []


if __name__ == "__main__":
    test_preflight_rejects_bad_origin_and_accepts_repo_artifacts()
    print("PASS test_preflight_rejects_bad_origin_and_accepts_repo_artifacts")
