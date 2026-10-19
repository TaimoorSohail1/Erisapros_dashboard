from app.models import NormalizedExtractionField, NormalizedExtractionResult
from app.services.extractor import remove_inapplicable_experience_rated_fields


def result():
    return NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="3b. Amount of Commissions", value="3638.32"),
        NormalizedExtractionField(field_name="9c(1)(A). Commissions", value="3638.32"),
        NormalizedExtractionField(field_name="10a. Total premiums or subscription charges paid to carrier", value="33126.32"),
    ])


def test_unnumbered_nonexperience_heading_does_not_accept_part_i_commission_as_retention():
    pages = [(2, "PART III\nNON-EXPERIENCE RATED CONTRACT\na) TOTAL PREMIUM PAID TO CARRIER: $33,126.32")]
    cleaned = remove_inapplicable_experience_rated_fields(result(), pages)
    assert [f.field_name for f in cleaned.fields] == [
        "3b. Amount of Commissions", "10a. Total premiums or subscription charges paid to carrier",
    ]


def test_positive_experience_section_is_not_erased_by_a_later_nonexperience_heading():
    pages = [(2, "9 Experience-rated contracts\nClaims paid $120.00\n10 Nonexperience-rated contracts\nTotal premiums $33,126.32")]
    cleaned = remove_inapplicable_experience_rated_fields(result(), pages)
    assert any(f.field_name.startswith("9c") for f in cleaned.fields)
