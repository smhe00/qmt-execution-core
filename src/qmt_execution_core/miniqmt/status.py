from __future__ import annotations

from enum import IntEnum

from ..domain import BrokerOrderStatus


class QmtOrderStatus(IntEnum):
    UNREPORTED = 48
    WAIT_REPORTING = 49
    REPORTED = 50
    REPORTED_CANCEL = 51
    PARTSUCC_CANCEL = 52
    PART_CANCEL = 53
    CANCELED = 54
    PART_SUCC = 55
    SUCCEEDED = 56
    JUNK = 57
    UNKNOWN = 255


_STATUS_MAP = {
    QmtOrderStatus.UNREPORTED: BrokerOrderStatus.ACCEPTED,
    QmtOrderStatus.WAIT_REPORTING: BrokerOrderStatus.ACCEPTED,
    QmtOrderStatus.REPORTED: BrokerOrderStatus.WORKING,
    QmtOrderStatus.REPORTED_CANCEL: BrokerOrderStatus.CANCEL_PENDING,
    QmtOrderStatus.PARTSUCC_CANCEL: BrokerOrderStatus.CANCEL_PENDING,
    QmtOrderStatus.PART_CANCEL: BrokerOrderStatus.PARTIAL_CANCELLED,
    QmtOrderStatus.CANCELED: BrokerOrderStatus.CANCELLED,
    QmtOrderStatus.PART_SUCC: BrokerOrderStatus.PARTIALLY_FILLED,
    QmtOrderStatus.SUCCEEDED: BrokerOrderStatus.FILLED,
    QmtOrderStatus.JUNK: BrokerOrderStatus.REJECTED,
    QmtOrderStatus.UNKNOWN: BrokerOrderStatus.UNKNOWN,
}


def normalize_qmt_order_status(raw_status: object) -> BrokerOrderStatus:
    """Map XtQuant raw order status to the generic broker status.

    Unknown types/values fail closed to UNKNOWN; they never become WORKING.
    """
    if type(raw_status) is not int:
        return BrokerOrderStatus.UNKNOWN
    try:
        status = QmtOrderStatus(raw_status)
    except ValueError:
        return BrokerOrderStatus.UNKNOWN
    return _STATUS_MAP[status]
