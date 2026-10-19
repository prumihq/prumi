"""Docs that list the code stay complete: the README names every MCP tool and CLI command, and
the runtime README every member of `window.prumi`. Prose can't be checked this way; lists can.

Each check is also run on a copy of its file with one item removed, so a failure is known to
name the item and the file to add it to."""

import argparse
import asyncio
import re
from pathlib import Path

import pytest
from mcp import Client

ROOT = Path(__file__).parents[3]
README = ROOT / "README.md"
RUNTIME_README = ROOT / "packages" / "prumi-runtime" / "README.md"


def code_spans(markdown: str) -> set[str]:
    """The text of every inline code span, fenced blocks left out."""
    prose = re.sub(r"^```.*?^```", "", markdown, flags=re.S | re.M)
    return set(re.findall(r"`([^`\n]+)`", prose))


def unnamed(kind: str, path: Path, names: list[str], named, how: str) -> str:
    """The failure message for the names `named(name)` doesn't find, or "" if none."""
    missing = [name for name in names if not named(name)]
    if not missing:
        return ""
    where = path.relative_to(ROOT).as_posix()
    listed = ", ".join(f"`{name}`" for name in missing)
    kind, them = (kind + "s", "them") if len(missing) > 1 else (kind, "it")
    return f"{where} doesn't name the {kind} {listed}: add {them} to {where}, {how}."


# What each file must name, and how it may name it


@pytest.fixture
def mcp_tools(demo_services) -> list[str]:
    from prumi.mcp import create_mcp

    async def listed():
        async with Client(create_mcp(demo_services)) as mcp:
            return [tool.name for tool in (await mcp.list_tools()).tools]

    return asyncio.run(listed())


def cli_commands() -> list[str]:
    from prumi.cli import _parser

    [commands] = [a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return list(commands.choices)


def tools_message(markdown: str, tools: list[str]) -> str:
    spans = code_spans(markdown)
    return unnamed("MCP tool", README, tools, lambda tool: tool in spans, "in backticks")


def commands_message(markdown: str, commands: list[str]) -> str:
    spans = code_spans(markdown)
    return unnamed(
        "CLI command",
        README,
        commands,
        lambda command: command in spans or re.search(rf"\bprumi {command}\b", markdown),
        "in backticks or as `prumi <command>`",
    )


def members_message(markdown: str, members: list[str]) -> str:
    spans = code_spans(markdown)

    def named(member: str) -> bool:
        call = f"prumi.{member}("
        return bool({member, f"prumi.{member}"} & spans) or any(s.startswith(call) for s in spans)

    return unnamed("window.prumi member", RUNTIME_README, members, named, "in backticks")


def without(markdown: str, name: str) -> str:
    """`markdown` with every mention of `name` removed, in any of the forms above."""
    for pattern in (rf"`(prumi\.)?{name}(\([^`]*\))?`", rf"\bprumi {name}\b"):
        markdown = re.sub(pattern, "", markdown)
    return markdown


# The checks


def test_the_readme_names_every_mcp_tool(mcp_tools):
    assert "query" in mcp_tools
    text = README.read_text(encoding="utf-8")
    assert not tools_message(text, mcp_tools), tools_message(text, mcp_tools)
    for tool in mcp_tools:
        message = tools_message(without(text, tool), mcp_tools)
        assert f"MCP tool `{tool}`: add it to README.md" in message, (tool, message)


def test_the_readme_names_every_cli_command():
    commands = cli_commands()
    assert "certify" in commands
    text = README.read_text(encoding="utf-8")
    assert not commands_message(text, commands), commands_message(text, commands)
    for command in commands:
        message = commands_message(without(text, command), commands)
        assert f"CLI command `{command}`: add it to README.md" in message, (command, message)


def test_the_runtime_readme_names_every_window_prumi_member(runtime_members):
    assert "format" in runtime_members
    text = RUNTIME_README.read_text(encoding="utf-8")
    assert not members_message(text, runtime_members), members_message(text, runtime_members)
    for member in runtime_members:
        message = members_message(without(text, member), runtime_members)
        expected = f"member `{member}`: add it to packages/prumi-runtime/README.md"
        assert expected in message, (member, message)
