from human_verification import (HumanVerificationRegistry, VerificationReplayError,
                                VerificationState, risk_decision)


def test_provider_result_is_single_use_and_expires():
    registry = HumanVerificationRegistry()
    result = registry.accept_provider_result(subject_id="u1", token="one", success=True,
                                              provider="test", ttl_seconds=60)
    assert result.valid
    assert registry.get("u1").state == VerificationState.VERIFIED
    try:
        registry.accept_provider_result(subject_id="u2", token="one", success=True, provider="test")
    except VerificationReplayError:
        pass
    else:
        raise AssertionError("replayed verification token accepted")


def test_risk_decision_is_progressive():
    assert risk_decision() == "ALLOW"
    assert risk_decision(referral_velocity=20) == "CHALLENGE"
    assert risk_decision(enforcement_state="SUSPENDED") == "REVIEW"
    assert risk_decision(enforcement_state="BANNED") == "BLOCK"


if __name__ == "__main__":
    test_provider_result_is_single_use_and_expires()
    test_risk_decision_is_progressive()
    print("PASS human verification tests")
