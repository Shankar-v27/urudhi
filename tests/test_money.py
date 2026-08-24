import pytest

from urudhi.ledger.money import format_inr, rupees


class TestRupees:
    def test_int(self):
        assert rupees(500) == 50_000

    def test_indian_grouped_string(self):
        assert rupees("2,50,000") == 25_000_000


class TestFormatInr:
    @pytest.mark.parametrize(
        ("paise", "expected"),
        [
            (0, "₹0.00"),
            (50, "₹0.50"),
            (100, "₹1.00"),
            (99_999, "₹999.99"),
            (100_000, "₹1,000.00"),
            (10_00_000_00, "₹10,00,000.00"),
            (1234567800, "₹1,23,45,678.00"),
            (-50_000, "-₹500.00"),
        ],
    )
    def test_indian_digit_grouping(self, paise, expected):
        assert format_inr(paise) == expected
