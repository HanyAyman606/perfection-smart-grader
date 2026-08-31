"""
tests/test_ip_share_card.py
------------------------------
Covers the IpShareCard auto-resync feature: polling get_local_ip() for
changes, auto-refreshing the IP label/QR code without a manual click,
and the finite (non-looping) pulse animation that flags a change
happened.

QT_QPA_PLATFORM=offscreen is required to run these (no real display) —
see other widget tests in this suite for the same requirement.
"""
import pytest
from PySide6.QtCore import QEventLoop, QTimer

from unittest.mock import patch

import admin_dashboard.widgets.ip_share_card as card_module
from admin_dashboard.widgets.ip_share_card import IpShareCard


@pytest.fixture
def card(qtbot_app):
    """poll_interval_ms is set high so the background QTimer doesn't
    interfere with tests that drive _check_for_network_change()
    manually — these tests are about behavior on a change, not about
    timing the poll interval itself."""
    with patch.object(card_module, "get_local_ip", return_value="192.168.1.10"):
        c = IpShareCard("Consolas", "Arial", poll_interval_ms=100_000)
        c.show()
    yield c


def test_initial_ip_shown_and_flash_hidden(card):
    assert card.ip_value_lbl.text() == "192.168.1.10"
    assert card.network_flash.isVisible() is False


def test_network_change_auto_updates_ip_and_qr(card):
    old_qr = card.ip_qr_lbl.pixmap()
    with patch.object(card_module, "get_local_ip", return_value="10.0.0.55"):
        card._check_for_network_change()

    assert card.ip_value_lbl.text() == "10.0.0.55"
    assert card._last_known_ip == "10.0.0.55"
    new_qr = card.ip_qr_lbl.pixmap()
    assert new_qr is not None
    # A different IP must encode to a different QR image.
    assert new_qr.toImage() != old_qr.toImage()


def test_network_change_triggers_flash(card):
    with patch.object(card_module, "get_local_ip", return_value="10.0.0.55"):
        card._check_for_network_change()
    assert card.network_flash.isVisible() is True


def test_unchanged_poll_does_not_trigger_flash(card):
    """The whole point of comparing against _last_known_ip: a poll tick
    where nothing changed must be a silent no-op — no flash, no
    unnecessary QR regeneration."""
    with patch.object(card_module, "get_local_ip", return_value="192.168.1.10"):
        card._check_for_network_change()
    assert card.network_flash.isVisible() is False


def test_manual_refresh_keeps_poller_baseline_in_sync(card):
    """If the manual refresh button catches a new IP first, the poller's
    _last_known_ip must be updated too — otherwise the next poll tick
    would see a 'change' that already happened and re-flash for
    something the admin already saw."""
    with patch.object(card_module, "get_local_ip", return_value="10.0.0.99"):
        card._refresh_ip_display()  # simulates clicking the manual ⟳ button
    assert card._last_known_ip == "10.0.0.99"

    # A poll tick right after, still seeing the same new IP, must be a no-op.
    card.network_flash.setVisible(False)
    with patch.object(card_module, "get_local_ip", return_value="10.0.0.99"):
        card._check_for_network_change()
    assert card.network_flash.isVisible() is False


def test_pulse_animation_is_finite_and_self_hides(card):
    """The core constraint carried over from PulsingDot's own history
    (widgets/common.py — deliberately de-animated to avoid an infinite
    repaint loop per instance): this animation must run a bounded
    number of loops and then go idle on its own, not run forever."""
    assert card.network_flash._animation.loopCount() == 5
    assert card.network_flash._animation.duration() == 1000  # ms per loop

    card.network_flash.pulse()
    assert card.network_flash.isVisible() is True

    # Run a REAL event loop past the total animation time (5 * 1000ms)
    # and confirm it hid itself — not inferred from the loop count alone.
    loop = QEventLoop()
    QTimer.singleShot(6000, loop.quit)
    loop.exec()

    assert card.network_flash.isVisible() is False


def test_pulse_can_be_retriggered_mid_animation(card):
    """A network that flaps twice in quick succession (e.g. Wi-Fi -> VPN
    -> different Wi-Fi within a couple seconds) must restart the flash
    cleanly rather than erroring or getting stuck."""
    card.network_flash.pulse()
    assert card.network_flash.isVisible() is True
    card.network_flash.pulse()  # re-trigger before the first pulse finished
    assert card.network_flash.isVisible() is True