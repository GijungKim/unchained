"""Whoop HRS collector tests: HRM decoding per the Bluetooth spec (no radio)."""

import pytest

from unchained.collectors.whoop_hrs import decode_hrm


def test_uint8_hr_with_rr_and_contact():
    # flags 0x16: 8-bit HR, contact supported+detected, RR present
    hr, rr, contact = decode_hrm(bytes([0x16, 72, 0x33, 0x03, 0x4B, 0x03]))
    assert hr == 72
    assert rr == pytest.approx([819 / 1024 * 1000, 843 / 1024 * 1000], abs=0.1)
    assert contact is True


def test_uint16_hr_skips_energy():
    # flags 0x19: 16-bit HR, energy expended present, RR present
    hr, rr, contact = decode_hrm(bytes([0x19, 0x2C, 0x01, 0xE8, 0x03, 0x00, 0x04]))
    assert hr == 300
    assert rr == [1000.0]
    assert contact is None


def test_bare_hr_without_rr():
    assert decode_hrm(bytes([0x00, 65])) == (65, [], None)


def test_contact_lost_reports_false():
    # flags 0x04: bit2 supported, bit1 detected-clear
    _, _, contact = decode_hrm(bytes([0x04, 70]))
    assert contact is False


def test_empty_packet_rejected():
    with pytest.raises(ValueError):
        decode_hrm(b"")
