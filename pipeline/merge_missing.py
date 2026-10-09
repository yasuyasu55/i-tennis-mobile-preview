"""Small merge safeguard for optional fields that are unread in a successful run."""

OPTIONAL_FIELDS = (
    "deadline_text", "eligibility_note", "fee_text", "contact_text",
    "entry_text", "entry_url", "guideline_url", "reserve_text", "reserve_periods",
)


def preserve_unread_optional_fields(previous, candidate):
    """Keep known optional values when this run yields no value for that field.

    Non-empty candidate values always win. Empty values on a first-seen event
    remain empty. Retained values are flagged in notes so they are not mistaken
    for fields freshly read during this run.
    """
    merged = dict(candidate)
    retained = []
    for field in OPTIONAL_FIELDS:
        old_value = previous.get(field)
        new_value = merged.get(field)
        new_empty = new_value is None or new_value == "" or new_value == []
        old_present = old_value is not None and old_value != "" and old_value != []
        if new_empty and old_present:
            merged[field] = old_value
            retained.append(field)
    if retained:
        notes = list(merged.get("notes") or [])
        marker = "一部の項目は今回未取得のため前回値を保持"
        if marker not in notes:
            notes.append(marker)
        merged["notes"] = notes
    return merged
