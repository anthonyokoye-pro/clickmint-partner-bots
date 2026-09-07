import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from webapp_auth import InitDataError, validate_init_data


def signed(token, values):
    encoded = urlencode(values)
    data = dict(values)
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return encoded + "&hash=" + digest


def test_valid_init_data():
    raw = signed("token", {
        "auth_date": str(int(time.time())),
        "query_id": "q1",
        "user": json.dumps({"id": 42, "username": "owner"}, separators=(",", ":")),
    })
    identity = validate_init_data(raw, "token")
    assert identity.user_id == 42
    assert identity.username == "owner"


def test_tampering_and_expiry_fail_closed():
    raw = signed("token", {"auth_date": "100", "user": json.dumps({"id": 42})})
    try:
        validate_init_data(raw, "token", now=1000, max_age=10)
    except InitDataError:
        pass
    else:
        raise AssertionError("expired initData accepted")
    try:
        validate_init_data(raw.replace("100", "101"), "token", now=100, max_age=10)
    except InitDataError:
        pass
    else:
        raise AssertionError("tampered initData accepted")


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("PASS", name)
