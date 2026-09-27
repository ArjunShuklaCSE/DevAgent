import pytest

from wallet.account import Account


def test_deposit_and_withdraw():
    account = Account(10)
    account.deposit(5)
    account.withdraw(3)
    assert account.balance == 12


def test_rejects_negative_deposit():
    with pytest.raises(ValueError):
        Account().deposit(-1)
