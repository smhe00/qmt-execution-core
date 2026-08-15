from types import SimpleNamespace

from qmt_execution_core.miniqmt.callbacks import QmtCallbackBridge, QmtBrokerDisconnected, QmtOrderObserved


def test_callbacks_only_emit_immutable_observations():
    events = []
    bridge = QmtCallbackBridge(events.append)
    bridge.on_stock_order(SimpleNamespace(
        order_id=1,
        stock_code="510300.SH",
        order_status=50,
        order_volume=100,
        traded_volume=0,
        order_remark="r1",
    ))
    bridge.on_disconnected()
    assert isinstance(events[0], QmtOrderObserved)
    assert isinstance(events[1], QmtBrokerDisconnected)
