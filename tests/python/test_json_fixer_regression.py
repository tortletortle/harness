from utils.json_fixer import JSONFixer


def test_fix_json_ignores_braces_inside_string_when_closing_truncated_object():
    data, error = JSONFixer.fix_json('{"message": "use { safely",')

    assert error is None
    assert data == {"message": "use { safely"}


def test_fix_json_closes_nested_structures_in_lifo_order():
    data, error = JSONFixer.fix_json('{"items": [{"value": "[literal]"}')

    assert error is None
    assert data == {"items": [{"value": "[literal]"}]}
