"""Tests de la validation des virements."""

from transfers.validation import Account, validate_transfer

ACCOUNT = Account(iban="MA00DEMO", balance=100000, phone="0600000000")


def test_req_012_at_threshold():
    result = validate_transfer(10000, ACCOUNT)
    assert result["status"] == "VALIDATED"


def test_small_transfer_is_validated():
    result = validate_transfer(100, ACCOUNT)
    assert result["amount"] == 100
