import os
import tempfile

from event_collector.entity_kb import (
    CompanyProfile,
    SQLiteEntityStore,
    StaticCompanyProfileSource,
    alias_in_text,
    build_direct_expanded_query,
    build_expanded_query,
    build_indirect_expanded_query,
    load_tickers_from_annotation_dir,
    save_company_profiles_seed,
    sync_ticker_universe,
)


def test_entity_store_loads_company_context_and_expanded_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteEntityStore(db_path=os.path.join(tmpdir, "entities.db"))
        store.init_db()
        store.upsert_company_profile(
            CompanyProfile(
                ticker="MSFT",
                canonical_name="Microsoft",
                website="https://www.microsoft.com",
                ir_url="https://www.microsoft.com/en-us/Investor",
                ceo_name="Satya Nadella",
                aliases=("Microsoft Corp",),
                ceo_aliases=("Satya Narayana Nadella",),
                products=("Azure", "GitHub Copilot"),
                business_lines=("cloud infrastructure",),
                themes=("enterprise software",),
            )
        )

        company = store.load_company_by_ticker("msft")

        assert company is not None
        assert company.company_id == "company:MSFT"
        assert company.company_aliases == ("Microsoft Corp",)
        assert company.ceo_names == ("Satya Nadella", "Satya Narayana Nadella")
        assert company.product_names == ("Azure", "GitHub Copilot")
        assert company.business_lines == ("cloud infrastructure",)
        assert company.themes == ("enterprise software",)
        assert build_direct_expanded_query(company) == (
            "MSFT Microsoft Microsoft Corp Satya Nadella "
            "Satya Narayana Nadella Azure GitHub Copilot"
        )
        assert build_indirect_expanded_query(company) == (
            "MSFT Microsoft Azure GitHub Copilot cloud infrastructure enterprise software"
        )
        assert build_expanded_query(company) == build_direct_expanded_query(company)

        store.close()


def test_attribute_article_to_company_matches_product_and_ceo_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteEntityStore(db_path=os.path.join(tmpdir, "entities.db"))
        store.init_db()
        store.upsert_company_profile(
            CompanyProfile(
                ticker="MSFT",
                canonical_name="Microsoft",
                website="https://www.microsoft.com",
                ir_url="https://www.microsoft.com/en-us/Investor",
                ceo_name="Satya Nadella",
                products=("Azure",),
            )
        )
        company = store.load_company_by_ticker("MSFT")

        product_only = store.attribute_article_to_company(
            "Azure demand accelerated again in large enterprise accounts.",
            company,
        )
        ceo_only = store.attribute_article_to_company(
            "Satya Nadella said commercial momentum stayed healthy.",
            company,
        )
        unrelated = store.attribute_article_to_company(
            "Oil prices rose as macro conditions shifted.",
            company,
        )

        assert product_only.is_match is True
        assert product_only.match_types == ("product",)
        assert product_only.matched_aliases == ("Azure",)
        assert ceo_only.is_match is True
        assert ceo_only.match_types == ("ceo",)
        assert unrelated.is_match is False
        assert unrelated.match_types == ()

        store.close()


def test_sync_pipeline_updates_current_relations_without_duplicate_company():
    with tempfile.TemporaryDirectory() as tmpdir:
        annotation_dir = os.path.join(tmpdir, "annotations")
        os.makedirs(annotation_dir, exist_ok=True)
        with open(os.path.join(annotation_dir, "MSFT.yaml"), "w", encoding="utf-8") as handle:
            handle.write("ticker: MSFT\nannotations: []\n")

        store = SQLiteEntityStore(db_path=os.path.join(tmpdir, "entities.db"))
        store.init_db()

        first_source = StaticCompanyProfileSource(
            {
                "MSFT": CompanyProfile(
                    ticker="MSFT",
                    canonical_name="Microsoft",
                    website="https://www.microsoft.com",
                    ir_url="https://www.microsoft.com/en-us/Investor",
                    ceo_name="Satya Nadella",
                    products=("Azure",),
                    business_lines=("cloud infrastructure",),
                )
            }
        )
        second_source = StaticCompanyProfileSource(
            {
                "MSFT": CompanyProfile(
                    ticker="MSFT",
                    canonical_name="Microsoft",
                    website="https://www.microsoft.com",
                    ir_url="https://www.microsoft.com/en-us/Investor",
                    ceo_name="Amy Hood",
                    products=("Microsoft 365",),
                    business_lines=("productivity software",),
                )
            }
        )

        first = sync_ticker_universe(annotation_dir, store, first_source)
        second = sync_ticker_universe(annotation_dir, store, second_source)

        assert [item.status for item in first] == ["success"]
        assert [item.status for item in second] == ["success"]
        company = store.load_company_by_ticker("MSFT")
        assert company is not None
        assert company.company_id == "company:MSFT"
        assert company.ceo_names == ("Amy Hood",)
        assert company.product_names == ("Microsoft 365",)

        current_relations = store.conn.execute(
            """
            SELECT relation_type, is_current
            FROM entity_relations
            WHERE from_entity_id = ?
            ORDER BY id
            """,
            (company.company_id,),
        ).fetchall()
        assert [tuple(row) for row in current_relations] == [
            ("has_ceo", 0),
            ("has_product", 0),
            ("has_business_line", 0),
            ("has_ceo", 1),
            ("has_product", 1),
            ("has_business_line", 1),
        ]
        run_statuses = store.conn.execute(
            "SELECT status FROM entity_sync_runs ORDER BY started_at"
        ).fetchall()
        assert [row["status"] for row in run_statuses] == ["success", "success"]

        store.close()


def test_load_tickers_from_annotation_dir_orders_and_normalizes_file_names():
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ("msft.yaml", "AAPL.yaml", "msft.txt", "nvda.yaml"):
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as handle:
                handle.write("ticker: placeholder\nannotations: []\n")

        assert load_tickers_from_annotation_dir(tmpdir) == ["AAPL", "MSFT", "NVDA"]


def test_alias_in_text_uses_whitespace_token_matching():
    haystack = "microsoft azure demand remained durable"

    assert alias_in_text("Azure", haystack) is True
    assert alias_in_text("MS", haystack) is False


def test_save_company_profiles_seed_writes_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profiles.yaml")
        save_company_profiles_seed(
            [
                CompanyProfile(
                    ticker="MSFT",
                    canonical_name="Microsoft",
                    website="https://www.microsoft.com",
                    ir_url="https://www.microsoft.com/en-us/Investor",
                    ceo_name="Satya Nadella",
                    products=("Azure",),
                    business_lines=("cloud infrastructure",),
                )
            ],
            path,
        )

        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()

        assert "ticker: MSFT" in content
        assert "canonical_name: Microsoft" in content
        assert "- Azure" in content
        assert "business_lines:" in content
