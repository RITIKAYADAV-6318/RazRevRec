"""Payment-failure taxonomy and allowed recovery actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCohort(StrEnum):
    TEMPORARY_BANK_FAILURE = "temporary_bank_failure"
    PROCESSOR_TIMEOUT = "processor_timeout"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    THREE_DS_AUTH_FAILURE = "3ds_auth_failure"
    EXPIRED_CARD = "expired_card"
    INVALID_CARD = "invalid_card"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    RISK_FRAUD_DECLINE = "risk_fraud_decline"


class RecoveryAction(StrEnum):
    SMART_RETRY = "smart_retry"
    WAIT_AND_RETRY = "wait_and_retry"
    PAYMENT_METHOD_UPDATE = "payment_method_update"
    CUSTOMER_REMINDER = "customer_reminder"
    ESCALATE = "escalate"
    STOP = "stop"


@dataclass(frozen=True)
class CohortProfile:
    base_recovery_probability: float
    retryable: bool
    default_action: RecoveryAction
    retry_wait_minutes: int | None


COHORTS: dict[FailureCohort, CohortProfile] = {
    FailureCohort.TEMPORARY_BANK_FAILURE: CohortProfile(0.78, True, RecoveryAction.SMART_RETRY, 20),
    FailureCohort.PROCESSOR_TIMEOUT: CohortProfile(0.72, True, RecoveryAction.SMART_RETRY, 10),
    FailureCohort.INSUFFICIENT_FUNDS: CohortProfile(0.35, True, RecoveryAction.WAIT_AND_RETRY, 24 * 60),
    FailureCohort.THREE_DS_AUTH_FAILURE: CohortProfile(0.60, True, RecoveryAction.CUSTOMER_REMINDER, 5),
    FailureCohort.EXPIRED_CARD: CohortProfile(0.55, False, RecoveryAction.PAYMENT_METHOD_UPDATE, None),
    FailureCohort.INVALID_CARD: CohortProfile(0.30, False, RecoveryAction.PAYMENT_METHOD_UPDATE, None),
    FailureCohort.CHECKOUT_ABANDONED: CohortProfile(0.25, False, RecoveryAction.CUSTOMER_REMINDER, 180),
    FailureCohort.RISK_FRAUD_DECLINE: CohortProfile(0.05, False, RecoveryAction.STOP, None),
}


CONTACT_ACTIONS = frozenset({RecoveryAction.PAYMENT_METHOD_UPDATE, RecoveryAction.CUSTOMER_REMINDER})
RETRY_ACTIONS = frozenset({RecoveryAction.SMART_RETRY, RecoveryAction.WAIT_AND_RETRY})


ACTION_COSTS: dict[RecoveryAction, float] = {
    RecoveryAction.SMART_RETRY: 2.0,
    RecoveryAction.WAIT_AND_RETRY: 2.5,
    RecoveryAction.PAYMENT_METHOD_UPDATE: 8.0,
    RecoveryAction.CUSTOMER_REMINDER: 5.0,
    RecoveryAction.ESCALATE: 35.0,
    RecoveryAction.STOP: 0.0,
}

