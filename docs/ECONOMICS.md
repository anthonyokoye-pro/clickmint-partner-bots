# CLICKMINT Economy Foundation

This document describes the first implemented economic foundation. It does **not** enable deposits, Boost purchases, external withdrawals, or payment processing.

## Purpose

Before the Boost/deposit/withdrawal system is enabled, CLICKMINT must prove that the marketplace can generate positive contribution margin while funding channel rewards, refunds, fraud losses, operating costs, and reserves.

The implementation is in `economics.py` and is deliberately independent of Telegram and payment providers.

## Accounting rules

For a realized payment period:

```text
operating surplus = gross revenue
                 - payment costs
                 - channel rewards
                 - refund reserve
                 - fraud reserve
                 - operating costs
                 - tax/compliance reserve
                 - fixed costs
```

Owner-distributable surplus is:

```text
max(0, operating surplus - required reserve)
```

Unspent Boost must not be treated as immediate profit. It represents an outstanding utility obligation until it is used, expires under clearly disclosed terms, or is otherwise resolved under the approved policy.

## Current safety gates

`validate_launch_economics()` rejects a launch plan when:

- the planned period is not operating-surplus positive;
- the contribution margin is below the configured target; or
- the required reserve would consume all distributable surplus.

The default recommended contribution-margin target is 20%, but this is a policy target, not a promise or final commercial decision.

## Stars

`stars_settlement_value()` accepts an explicit, configurable value per Star. The default reference value is `0.013`, based on Telegram's current Bot Platform documentation, but the value must be treated as changeable and subject to holds, refunds, regional availability, and Telegram settlement rules.

The calculation does not claim that a user's local purchase price equals the platform's realized value.

## What is intentionally not implemented

- Payment collection.
- Deposit creation.
- Boost issuance.
- Boost expiry.
- Boost-to-reward conversion.
- External withdrawals.
- Custody of money or crypto.
- Automatic owner payouts.
- Telegram Stars or TON transfers.

Those features remain later stages and require separate approval, payment-provider research, reserve policy, compliance review, and an auditable multi-currency ledger.

## Example

For an illustrative $10 realized unit:

```text
gross revenue:          $10.00
payment cost:           -$1.00
channel reward:         -$4.00
refund reserve:         -$0.75
fraud reserve:          -$0.50
operations:             -$1.00
tax reserve:            -$0.75
platform surplus:        $2.00
```

This is only a modeling example. Production values must come from the selected payment rail, actual reward policy, actual operating costs, and approved reserves.
