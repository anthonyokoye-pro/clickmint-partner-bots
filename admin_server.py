"""Windows-friendly Admin Mini App WSGI launcher."""
from __future__ import annotations

import argparse
from pathlib import Path
from wsgiref.simple_server import make_server

import config
from admin_api import AdminReadAPI
from admin_http import AdminWSGI
from admin_idempotency import IdempotencyStore
from admin_service import build_admin_app
from audience import AudienceDirectory
from broadcast_queue import BroadcastQueue
from enforcement import EnforcementStore
from ad_campaigns import AdCampaignStore
from channel_registry import ChannelRegistry
from verification_store import VerificationStore
from telegram_verification import BotCredentialStore, TelegramVerificationService
from destination_state import DestinationStateMachine
from enforcement_gate import EnforcementGate
from onboarding_api import OnboardingAPI
from platform_store import SharedKV
from governance import RoleRegistry
from mint_ledger import TransactionalMintLedger
from performance_snapshots import PerformanceSnapshotRepository
from store import JsonStore
from task_marketplace import TaskMarketplace


class _LedgerFacade:
    """Compatibility facade for the Admin API's transactional ledger boundary."""
    def __init__(self, ledger):
        self.tx = ledger
        # Legacy audience selection is intentionally empty until the migration
        # from JSON member records to the authoritative ledger is complete.
        self.ledger = {}


def build_runtime_app(*, dev: bool = False):
    shared_store = JsonStore(config.REWARD_STORE_PATH)
    roles = RoleRegistry(SharedKV(config.PLATFORM_DB_PATH, legacy=shared_store), config.OWNER_USER_ID)
    # Destinations + credentials live in the SQLite verification DB shared by both bots.
    verification_store = VerificationStore(config.VERIFICATION_DB_PATH, legacy=shared_store)
    channels = ChannelRegistry(verification_store)
    marketplace = TaskMarketplace(config.TASK_DB_PATH)
    snapshots = PerformanceSnapshotRepository(config.CREDIBILITY_DB_PATH)
    broadcasts = BroadcastQueue(config.BROADCAST_DB_PATH)
    ledger = _LedgerFacade(TransactionalMintLedger(config.MINT_DB_PATH))
    enforcement = EnforcementStore(config.ENFORCEMENT_DB_PATH)
    ads = AdCampaignStore(config.ADS_DB_PATH, enabled=config.ADS_ENABLED)
    api = AdminReadAPI(
        bot_token=config.ADMIN_BOT_TOKEN,
        owner_id=config.OWNER_USER_ID,
        roles=roles,
        channels=channels,
        marketplace=marketplace,
        snapshots=snapshots,
        broadcasts=broadcasts,
        ledger=ledger,
        audience=AudienceDirectory(shared_store),
        enforcement=enforcement,
        ads=ads,
    )
    credentials = BotCredentialStore(verification_store)
    onboarding = OnboardingAPI(
        # Members open the onboarding page from the REWARD bot, so its token signs initData.
        platform_bot_token=config.REWARD_BOT_TOKEN,
        credentials=credentials,
        verification=TelegramVerificationService(credentials),
        channels=channels,
        destination_states=DestinationStateMachine(channels),
        enforcement_gate=EnforcementGate(enforcement, owner_user_id=config.OWNER_USER_ID),
        audit=lambda actor, action, reason: ledger.tx.add_audit_event(
            actor_type="member", actor_id=str(actor), action=action,
            object_type="bot_credential", object_id=str(actor), reason=reason),
    )
    app = build_admin_app(
        api,
        root=Path(__file__).parent,
        bot_token=config.ADMIN_BOT_TOKEN,
        owner_id=str(config.OWNER_USER_ID),
        allowed_origins=config.ADMIN_ALLOWED_ORIGINS,
        idempotency_path=config.ADMIN_API_DB_PATH,
        onboarding=onboarding,
    )
    if dev:
        # Explicit local-only mode: production retains the HTTPS allowlist.
        app.allowed_origins = None
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ClickMint Admin Mini App")
    parser.add_argument("--host", default=config.ADMIN_BIND_HOST)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dev", action="store_true",
                        help="allow local HTTP origins; never use in production")
    args = parser.parse_args()
    app = build_runtime_app(dev=args.dev)
    with make_server(args.host, args.port, app) as server:
        print(f"ClickMint Admin listening on http://{args.host}:{args.port}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
