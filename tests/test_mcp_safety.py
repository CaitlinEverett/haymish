"""MCP surface safety contract: propose-only, no apply/delete/hide tools.

The MCP server's safety story is that the AI proposes and the human disposes.
These tests verify the tool registry and API contracts.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Source-level tool-name extraction: avoids the haymish.mcp_server import chain
# (which pulls in photoscript -> objc, problematic under re-import).
# ---------------------------------------------------------------------------

_MCP_SOURCE = Path(__file__).resolve().parents[1] / "haymish" / "mcp_server.py"


def _extract_tool_names_from_source() -> set[str]:
    """Parse the source AST to find every function decorated with @mcp.tool()."""
    source = _MCP_SOURCE.read_text()
    tree = ast.parse(source)

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                # @mcp.tool()
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "tool"):
                    names.add(node.name)
    return names


# The exact set of tools the MCP server should expose.
_EXPECTED_TOOLS = {
    "haymish_status",
    "haymish_find",
    "haymish_review_preview",
    "haymish_ask_plan",
    "haymish_index_refresh",
    "haymish_job_status",
    "haymish_staged_deletes",
}

# Tool names that MUST NOT exist on the MCP surface. These are full tool names
# (not substrings) representing mutating actions. haymish_staged_deletes is a
# read-only reporter (lists what's staged), not a mutation — so it's allowed.
_FORBIDDEN_TOOLS = {
    "haymish_apply", "haymish_delete", "haymish_hide", "haymish_unhide",
    "haymish_archive", "haymish_confirm", "haymish_toggle",
    "haymish_stage", "haymish_unstage", "haymish_remove",
    "haymish_confirm_deletes", "haymish_sweep_apply",
}


def test_mcp_tool_set_is_exactly_the_expected_propose_only_set():
    """The MCP surface must expose exactly the known tools — no more, no less.
    A new tool that slips in without updating this list gets caught."""
    names = _extract_tool_names_from_source()
    assert names == _EXPECTED_TOOLS, (
        f"MCP tool set changed.\n"
        f"  New:     {names - _EXPECTED_TOOLS}\n"
        f"  Missing: {_EXPECTED_TOOLS - names}"
    )


def test_no_forbidden_mutating_tool_exists():
    """No tool may be named as a direct mutation action. Read-only reporters
    like haymish_staged_deletes (a list, not an action) are fine."""
    names = _extract_tool_names_from_source()
    forbidden_present = names & _FORBIDDEN_TOOLS
    assert not forbidden_present, (
        f"MCP surface contains forbidden mutating tools: {forbidden_present}"
    )


def test_human_gate_note_is_present_in_source():
    """The _HUMAN_GATE_NOTE must be non-empty and mention the review_url concept."""
    source = _MCP_SOURCE.read_text()
    # Find the _HUMAN_GATE_NOTE assignment
    assert "_HUMAN_GATE_NOTE" in source
    match = re.search(r'_HUMAN_GATE_NOTE\s*=\s*\(\s*"(.+?)"\s*\)', source, re.DOTALL)
    assert match is not None, "_HUMAN_GATE_NOTE must be defined"
    note = match.group(1)
    assert "review_url" in note
    assert "cannot apply" in note.lower() or "cannot apply" in source


def test_source_has_no_action_verb_tool_registration():
    """No MCP tool should be named as a direct mutation action (apply, confirm, hide).
    Read-only reporters whose names include nouns like 'staged_deletes' are fine."""
    source = _MCP_SOURCE.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "tool"):
                    assert node.name not in _FORBIDDEN_TOOLS, (
                        f"MCP tool {node.name!r} is a forbidden mutation tool"
                    )


def test_mcp_find_docstring_says_read_only():
    """haymish_find's docstring must mention read-only."""
    source = _MCP_SOURCE.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "haymish_find":
            docstring = ast.get_docstring(node) or ""
            assert "read" in docstring.lower()
            assert "never modifies" in docstring.lower() or "read-only" in docstring.lower()
            break
    else:
        pytest.fail("haymish_find not found in source")


def test_mcp_review_preview_docstring_says_cannot_apply():
    """haymish_review_preview's docstring must say it cannot apply."""
    source = _MCP_SOURCE.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "haymish_review_preview":
            docstring = ast.get_docstring(node) or ""
            assert "cannot" in docstring.lower()
            break
    else:
        pytest.fail("haymish_review_preview not found in source")


def test_mcp_staged_deletes_docstring_says_read_only():
    """haymish_staged_deletes must say it's purely informational."""
    source = _MCP_SOURCE.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "haymish_staged_deletes":
            docstring = ast.get_docstring(node) or ""
            assert "read-only" in docstring.lower() or "informational" in docstring.lower()
            break
    else:
        pytest.fail("haymish_staged_deletes not found in source")


# -- _with_review_url and _wait_for_job (import-safe, no photoscript needed) ---

def test_with_review_url_attaches_note_and_url():
    """Test the URL builder and note attachment via source-level check."""
    source = _MCP_SOURCE.read_text()
    # _with_review_url must add review_url and note to the result dict
    assert 'result["review_url"]' in source
    assert 'result["note"]' in source
    # Error/timeout results should pass through untouched
    assert '"error" in result' in source or "'error' in result" in source
    assert "timed_out" in source


def test_wait_for_job_surfaces_error_state():
    """The source must handle 'error' job state and return the error."""
    source = _MCP_SOURCE.read_text()
    # _wait_for_job must check for error state
    assert '"error"' in source
    assert '"done"' in source
    assert '"timed_out"' in source
    # Must include the job id in timeout response for polling
    assert '"job"' in source
