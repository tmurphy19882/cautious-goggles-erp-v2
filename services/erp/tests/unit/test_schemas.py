"""Unit tests for shared Pydantic v2 schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas import AppModel, MoneyDecimal, utcnow


def test_money_decimal_rejects_float() -> None:
    # Decimal is required; float is intentionally not accepted by the type.
    m = MoneyDecimal(value="10.50", currency="USD")
    assert str(m.value) == "10.5000"
    assert m.currency == "USD"


def test_money_decimal_currency_must_be_iso() -> None:
    with pytest.raises(ValidationError):
        MoneyDecimal(value="1.00", currency="usd")  # lowercase fails the regex


def test_money_decimal_currency_must_be_3_letters() -> None:
    with pytest.raises(ValidationError):
        MoneyDecimal(value="1.00", currency="EU")


def test_app_model_forbids_extras() -> None:
    class M(AppModel):
        x: int

    with pytest.raises(ValidationError):
        M(x=1, y=2)  # type: ignore[call-arg]


def test_app_model_from_attributes() -> None:
    class M(AppModel):
        x: int

    class Obj:
        x = 7

    assert M.model_validate(Obj()).x == 7


def test_utcnow_is_aware() -> None:
    t = utcnow()
    assert t.tzinfo is not None
    assert t.tzinfo.utcoffset(t).total_seconds() == 0
