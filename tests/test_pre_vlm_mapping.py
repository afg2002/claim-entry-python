from services.pre_vlm_mapping import map_field, map_fields, summarize_cost_saving


def test_regex_member_number_skips_vlm():
    result = map_field("No. Peserta: ABC123456", "member_number")
    assert result.method == "regex"
    assert result.value == "ABC123456"
    assert result.needs_vlm is False
    assert result.confidence == 100.0


def test_fuzzy_label_match_is_conservative():
    result = map_field("Nama Pasen : Budi Santoso", "patient_name", fuzzy_threshold=80)
    assert result.method in {"rapidfuzz", "ambiguous_fuzzy"}
    assert result.confidence >= 80


def test_unknown_field_stays_unresolved():
    result = map_field("random text", "provider_name")
    assert result.needs_vlm is True


def test_map_fields_returns_audit_shape():
    result = map_fields("No. Kartu: ABC123\nTanggal Lahir: 19/03/2002", ["member_number", "date_of_birth"])
    assert result["member_number"]["method"] == "regex"
    assert result["date_of_birth"]["method"] == "regex"


def test_cost_saving_summary():
    result = summarize_cost_saving(10, 7, estimated_vlm_cost_per_page_idr=1000)
    assert result["vlm_pages"] == 3
    assert result["skipped_vlm_pages"] == 7
    assert result["vlm_skip_rate"] == 70.0
    assert result["estimated_cost_saved_idr"] == 7000
