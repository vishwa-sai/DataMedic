"""Sample files + the treatment plan used to produce each expected output."""
CASES = {
    "messy_customers__default": ("messy_customers.csv", {}),
    "messy_customers__drop_rows_lowercase_emails": (
        "messy_customers.csv",
        {"missing": {"strategy": "drop_rows", "columns": [0, 1, 2, 3, 4, 5, 6]},
         "emails": {"lowercase": True}, "drop_empty_columns": True}),
    "messy_customers__fill_unknown_collapse": (
        "messy_customers.csv",
        {"missing": {"strategy": "fill_text", "text": "Unknown"}, "collapse_spaces": True,
         "placeholders_as_missing": True, "drop_empty_columns": True}),
    "dates_and_numbers__day_first": (
        "dates_and_numbers.csv", {"dates": {"normalize": True, "order": "dmy"}}),
    "dates_and_numbers__ambiguous_skipped": ("dates_and_numbers.csv", {}),
}
