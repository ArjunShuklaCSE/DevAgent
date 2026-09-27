"""A minimal account with balance checks."""


class InsufficientFundsError(Exception):
    """Raised when a withdrawal exceeds the available balance."""


class Account:
    def __init__(self, balance: int = 0) -> None:
        if balance < 0:
            raise ValueError("initial balance cannot be negative")
        self.balance = balance

    def deposit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("deposit must be positive")
        self.balance += amount

    def withdraw(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("withdrawal must be positive")
        if amount > self.balance:
            raise ValueError("insufficient funds")
        self.balance -= amount
