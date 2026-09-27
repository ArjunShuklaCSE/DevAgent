import pytest

from wallet.account import Account, InsufficientFundsError


def test_overdraft_raises_insufficient_funds():
    account = Account(10)
    with pytest.raises(InsufficientFundsError):
        account.withdraw(11)
    assert account.balance == 10


def test_withdraw_from_empty_account():
    with pytest.raises(InsufficientFundsError):
        Account().withdraw(1)
