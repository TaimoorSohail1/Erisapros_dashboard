from app.models import NormalizedExtractionField, NormalizedExtractionResult
from app.services.schedule_a_trace import capture_extraction_stage


def test_stage_evidence_does_not_change_when_later_processing_mutates_the_result():
    result = NormalizedExtractionResult(provider="Example", fields=[
        NormalizedExtractionField(field_name="1e. Persons Covered (End of Policy Year)", value="246"),
    ], raw={"request_id": "non-secret-id"})
    capture_extraction_stage(result, "provider")
    result.fields[0].value = "1246"
    capture_extraction_stage(result, "parser")
    assert result.raw["request_id"] == "non-secret-id"
    assert [s["fields"][0]["value"] for s in result.raw["extraction_stages"]] == ["246", "1246"]
    assert all("raw" not in s for s in result.raw["extraction_stages"])
