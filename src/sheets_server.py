"""Sheets MCP server: read-only access to the portfolio spreadsheet over stdio.

Auth is a service account (TODO.md Phase 2) - no browser consent, no refresh
tokens, no expiry handling. Both tools return their tab whole: the spreadsheet
is already summary-level, so there is nothing to filter client-side.

Structure - no class, no context manager, unlike the client side (mcp_client.py):
  - `server` (below) is one module-level MCPServer instance.
  - Each function decorated `@server.tool()` or `@server.prompt()` becomes
    visible to a connected client's list_tools()/list_prompts(); everything
    else (_init, _load_config, _read_tab, ...) is a private helper, invisible
    to the client.
  - `if __name__ == "__main__":` calls `_init()` once, then
    `asyncio.run(server.run_stdio_async())`, which blocks for the process's
    entire life: read a request from stdin, dispatch it to the matching
    decorated function, write the response to stdout, repeat. There's no
    __aenter__/__aexit__ pair here because the server doesn't initiate a
    connection - it just runs until stdin closes, which happens when the
    client's __aexit__ closes its end of the pipe.
"""
import asyncio
import tomllib
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from mcp.server import MCPServer

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

server = MCPServer("portfolio-sheets")

# Populated by _init(), not at import time - see its docstring for why.
_SHEETS_VALUES = None
_SPREADSHEET_ID = None
_TABS = None


def _load_config() -> dict:
    """
    Read the [sheets] table from config.toml (gitignored; see config.example.toml
    for the template and TODO.md Phase 2 for the service-account setup it assumes).
    :return: the parsed [sheets] table - credentials_path, spreadsheet_id, tabs.
    """
    if not CONFIG_PATH.is_file():
        raise SystemExit(f"{CONFIG_PATH} not found - copy config.example.toml to config.toml and fill it in.")
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)["sheets"]


def _build_values_resource(credentials_path: Path):
    """
    Authenticate with the service account and return the Sheets API values()
    resource - the only surface these read-only tools need.
    :param credentials_path: absolute path to the service-account JSON key.
    """
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path), scopes=[READONLY_SCOPE]
    )
    return build("sheets", "v4", credentials=credentials).spreadsheets().values()


def _init() -> None:
    """
    Load config and authenticate, populating the module-level Sheets API handle.
    Kept out of module scope - and called only from `__main__` - so importing
    this module (e.g. to test it, or for a clone with no config.toml yet) never
    touches the filesystem, credentials, or the network.
    """
    global _SHEETS_VALUES, _SPREADSHEET_ID, _TABS  # pylint: disable=global-statement
    config = _load_config()
    _SHEETS_VALUES = _build_values_resource(CONFIG_PATH.parent / config["credentials_path"])
    _SPREADSHEET_ID = config["spreadsheet_id"]
    _TABS = config["tabs"]


def _read_tab(tab_name: str) -> str:
    """
    Fetch a tab's full used range and render it as a markdown pipe table - the
    same format `context.py` renders docx tables in, since it's what the model
    parses reliably.
    :param tab_name: sheet tab name exactly as it appears in the spreadsheet.
    :return: markdown table, or a message noting the tab has no data.
    """
    result = _SHEETS_VALUES.get(spreadsheetId=_SPREADSHEET_ID, range=tab_name).execute()
    rows = result.get("values", [])
    if not rows:
        return f"'{tab_name}' is empty."

    header, *body = rows
    width = len(header)
    body = [row + [""] * (width - len(row)) for row in body]

    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


@server.tool()
def read_portfolio_overview() -> str:
    """Read the portfolio development tab: current family's portfolio overview."""
    return _read_tab(_TABS["portfolio_overview_tab"])


@server.tool()
def read_networth_overview() -> str:
    """Read the net-worth tracking tab."""
    return _read_tab(_TABS["networth_overview_tab"])


@server.prompt()
def portfolio() -> str:
    """Portfolio-only review: value, composition, and unrealized gain - no career planning."""
    return (
        "Give me a portfolio-only review: current total portfolio value, its composition "
        "(allocation across holdings), and unrealized gain, using the portfolio and net-worth "
        "tools. Stick to the portfolio - don't bring in career planning unless I ask for it."
    )


if __name__ == "__main__":
    _init()
    asyncio.run(server.run_stdio_async())
