from pathlib import Path

import pytest

from event_collector.ticker_kb import build_expanded_query, load_ticker_identities, load_ticker_list


def test_load_ticker_list_and_kb_success(tmp_path):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - msft\n  - aapl\n", encoding="utf-8")

    kb = tmp_path / "kb.yaml"
    kb.write_text(
        (
            "tickers:\n"
            "  - ticker: MSFT\n"
            "    company_name: Microsoft\n"
            "    aliases:\n"
            "      - Microsoft Corp\n"
            "  - ticker: AAPL\n"
            "    company_name: Apple\n"
        ),
        encoding="utf-8",
    )

    tickers = load_ticker_list(ticker_list)
    identities = load_ticker_identities(kb)

    assert tickers == ["MSFT", "AAPL"]
    assert identities["MSFT"].company_name == "Microsoft"
    assert identities["MSFT"].aliases == ("Microsoft Corp",)
    assert build_expanded_query(identities["MSFT"]) == "MSFT Microsoft Microsoft Corp"


def test_load_ticker_identities_rejects_theme_aliases(tmp_path):
    kb = tmp_path / "kb.yaml"
    kb.write_text(
        (
            "tickers:\n"
            "  - ticker: MSFT\n"
            "    company_name: Microsoft\n"
            "    aliases:\n"
            "      - cloud\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="product, theme, or business-line term"):
        load_ticker_identities(kb)


def test_load_ticker_list_rejects_duplicates(tmp_path):
    ticker_list = tmp_path / "tickers.yaml"
    ticker_list.write_text("tickers:\n  - MSFT\n  - msft\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate ticker"):
        load_ticker_list(ticker_list)
