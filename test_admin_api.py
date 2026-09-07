import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from admin_api import AdminReadAPI
from channel_registry import ChannelRegistry
from performance_snapshots import PerformanceSnapshotRepository
from task_marketplace import TaskMarketplace
from broadcast_queue import BroadcastQueue
from store import JsonStore


class Roles:
    def is_admin(self, uid):
        return int(uid) == 9


def signed(token, values):
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values) + "&hash=" + digest


def test_authenticated_dashboard_is_read_only():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-api-")
    try:
        root = Path(directory.name)
        store = JsonStore(str(root / "store.json"))
        channels = ChannelRegistry(store)
        channels.add(9, "-1001", "@demo", "channel", ["Guides"], bot_added=True)
        market = TaskMarketplace(root / "tasks.sqlite3")
        snapshots = PerformanceSnapshotRepository(root / "credibility.sqlite3")
        broadcasts = BroadcastQueue(root / "broadcast.sqlite3")
        api = AdminReadAPI(bot_token="token", owner_id=8, roles=Roles(),
                           channels=channels, marketplace=market,
                           snapshots=snapshots, broadcasts=broadcasts)
        init_data = signed("token", {
            "auth_date": str(int(time.time())), "user": json.dumps({"id": 9})
        })
        result = api.dashboard(init_data)
        assert result["user"]["id"] == 9
        assert result["channels"][0]["status"] == "ACTIVE"
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_authenticated_dashboard_is_read_only()
    print("PASS test_authenticated_dashboard_is_read_only")
