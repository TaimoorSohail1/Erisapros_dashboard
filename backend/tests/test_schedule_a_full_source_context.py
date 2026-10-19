from app.services.extractor import build_xray_source_text


def test_semantic_source_can_include_values_after_the_evidence_preview_boundary():
    text = "Carrier statement introductory text. " * 50 + "\nGeneral Agent Fees: $0\n"
    chunk = {"text": text}
    assert "General Agent Fees" in build_xray_source_text(chunk, limit=None)
    assert len(build_xray_source_text(chunk)) <= 1200
