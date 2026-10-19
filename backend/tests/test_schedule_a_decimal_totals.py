from app.services.extractor import sum_money_values


def test_parenthesized_negative_adjustment_is_included_in_the_total():
    assert sum_money_values("($5.57)", "1.94", "10.35") == "6.72"


def test_currency_totals_do_not_lose_cents_to_binary_float_precision():
    assert sum_money_values("9,007,199,254,740,991.00", "0.01") == "9,007,199,254,740,991.01"
