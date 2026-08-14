"""Built-in package resource tests."""

from pangi.builtins import load_builtin_catalog


def test_builtin_catalog_is_packaged_separately_from_user_data() -> None:
    assert load_builtin_catalog() == {"schema_version": 1, "capability_packs": []}

