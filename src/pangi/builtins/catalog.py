"""Access the immutable catalog bundled in the distribution."""

import json
from importlib import resources
from typing import cast


def load_builtin_catalog() -> dict[str, object]:
    """Load a fresh copy of the built-in resource catalog."""

    catalog_text = (
        resources.files("pangi.builtins.resources").joinpath("catalog.json").read_text("utf-8")
    )
    catalog = json.loads(catalog_text)
    if not isinstance(catalog, dict):
        raise ValueError("built-in catalog must be a JSON object")
    return cast(dict[str, object], catalog)

