"""Tests for the Sheets MCP server.

The Sheets API client is always mocked - these tests never hit the network or
need a real config.toml / service-account key. src/sheets_server.py defers all
of that into _init(), called only from __main__, precisely so import and these
tests don't require either.
"""
import asyncio

import pytest

from src import sheets_server
from src.sheets_server import _read_tab, read_networth_overview, read_portfolio_overview


@pytest.fixture(autouse=True)
def mock_tabs(monkeypatch):
    monkeypatch.setattr(sheets_server, "_TABS", {"portfolio_overview_tab": "Portfolio Development", "networth_overview_tab": "Dashboard"})
    monkeypatch.setattr(sheets_server, "_SPREADSHEET_ID", "test-spreadsheet-id")


def mock_values(monkeypatch, rows: list[list[str]]):
    """Replace `_SHEETS_VALUES` with a stub whose .get(...).execute() returns `rows`."""
    stub = type("StubValues", (), {
        "get": lambda self, spreadsheetId, range: type("StubRequest", (), {"execute": lambda self: {"values": rows}})()
    })()
    monkeypatch.setattr(sheets_server, "_SHEETS_VALUES", stub)


class TestServerRegistersTools:
    def test_lists_both_tools_by_name(self):
        tools = asyncio.run(sheets_server.server.list_tools())

        assert {t.name for t in tools} == {"read_portfolio_overview", "read_networth_overview"}


class TestReadTab:
    def test_renders_rows_as_markdown_pipe_table(self, monkeypatch):
        mock_values(monkeypatch, [["Date", "Value"], ["2026-01-01", "100"], ["2026-02-01", "110"]])

        result = _read_tab("Portfolio Development")

        assert result == (
            "| Date | Value |\n"
            "| --- | --- |\n"
            "| 2026-01-01 | 100 |\n"
            "| 2026-02-01 | 110 |"
        )

    def test_empty_tab_returns_message_not_empty_table(self, monkeypatch):
        mock_values(monkeypatch, [])

        result = _read_tab("Dashboard")

        assert result == "'Dashboard' is empty."

    def test_pads_short_rows_to_header_width(self, monkeypatch):
        mock_values(monkeypatch, [["Date", "Value", "Note"], ["2026-01-01", "100"]])

        result = _read_tab("Portfolio Development")

        assert "| 2026-01-01 | 100 |  |" in result


class TestToolsReadTheConfiguredTab:
    def test_read_portfolio_overview_reads_portfolio_development_tab(self, monkeypatch):
        mock_values(monkeypatch, [["Date", "Value"], ["2026-01-01", "100"]])

        result = read_portfolio_overview()

        assert "2026-01-01" in result

    def test_read_networth_overview_reads_dashboard_tab(self, monkeypatch):
        mock_values(monkeypatch, [["Account", "Balance"], ["Broker", "50000"]])

        result = read_networth_overview()

        assert "Broker" in result
