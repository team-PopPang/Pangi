"""Stable public import surface checks."""

import pangi


def test_root_public_api_is_explicit_and_minimal() -> None:
    assert pangi.__all__ == (
        "AttachmentRef",
        "PangiConfig",
        "PangiRuntime",
        "Principal",
        "RunEvent",
        "RunRequest",
        "__version__",
    )

    namespace: dict[str, object] = {}
    exec("from pangi import *", namespace)

    assert {name for name in namespace if name != "__builtins__"} == set(pangi.__all__)
