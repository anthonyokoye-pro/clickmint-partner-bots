"""User-facing currency copy for the Reward / Mint Post Exchange bot.

Internal ledger keys remain stable for backwards-compatible data migration. Only
presentation uses the new currency name and icon.
"""
MINT_ICON = "🪙"
MINT_NAME = "MINT"
MINT_UNIT = f"{MINT_ICON} {MINT_NAME}"


def amount(value: int) -> str:
    """Consistent, readable amount: ``🪙 1 MINT`` / ``🪙 2 MINT``."""
    return f"{MINT_ICON} {int(value):,} {MINT_NAME}"


def amount_short(value: int) -> str:
    return f"{MINT_ICON} {int(value):,}"


def balance_line(balance: int, earned: int, spent: int) -> str:
    return (f"{MINT_ICON} <b>MINT WALLET</b>\n"
            f"Available: {amount(balance)}\n"
            f"Earned: {amount(earned)}\n"
            f"Spent: {amount(spent)}")
