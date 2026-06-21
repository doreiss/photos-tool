"""Static guards on packaging/photos-tool.spec.

The .app is never built in CI (slow, arm64-only), but the spec's ``info_plist`` is a plain
dict literal, so we parse it with ``ast`` and assert the load-bearing keys here, on every
push, on Linux. The absence of ``NSAppleEventsUsageDescription`` is what made tccd silently
refuse the Apple Events request for days with no prompt and nothing red — this is the cheap
guard that would have caught it.
"""

from __future__ import annotations

import ast
import pathlib

SPEC = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "photos-tool.spec"
BUNDLE_ID = "com.dominicreiss.photos-tool"


def _info_plist() -> dict[str, ast.AST]:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "info_plist":
            assert isinstance(node.value, ast.Dict)
            out: dict[str, ast.AST] = {}
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(key, ast.Constant):
                    out[str(key.value)] = value
            return out
    raise AssertionError("info_plist= not found in the spec")


def _const(node: ast.AST) -> object:
    # Plist values are string literals (parenthesised implicit concatenation folds to one
    # Constant) or ast.Constant(True); version keys are an ast.Name (_VERSION) -> None, for
    # which presence alone is asserted.
    return node.value if isinstance(node, ast.Constant) else None


def test_info_plist_declares_required_usage_descriptions():
    plist = _info_plist()
    # Each must be present AND non-empty — an empty string also fails the TCC prompt.
    for key in (
        "NSAppleEventsUsageDescription",  # the bug: tccd silently refuses without it
        "NSPhotoLibraryUsageDescription",
        "NSPhotoLibraryAddUsageDescription",
    ):
        assert key in plist, f"spec info_plist missing {key}"
        value = _const(plist[key])
        assert isinstance(value, str) and value.strip(), f"{key} is empty"


def test_info_plist_is_a_menubar_agent():
    plist = _info_plist()
    assert "LSUIElement" in plist
    assert _const(plist["LSUIElement"]) is True


def test_bundle_identifier_is_stable():
    # The bundle id IS the TCC identity all Photos/Automation/FDA grants key to; a rename
    # silently orphans every prior grant on the family Mac.
    assert f'bundle_identifier="{BUNDLE_ID}"' in SPEC.read_text(encoding="utf-8")
