"""Company-centric entity KB for ticker/product-aware retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time
from typing import Protocol
from urllib.parse import urljoin

import requests
import trafilatura
import yaml

CURRENT_RELATION_TYPES = ("has_ceo", "has_product", "has_business_line", "has_theme")


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    canonical_name: str
    website: str
    ir_url: str
    ceo_name: str | None = None
    aliases: tuple[str, ...] = ()
    ceo_aliases: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    business_lines: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    asset_type: str = "company"


@dataclass(frozen=True)
class CompanyContext:
    company_id: str
    primary_ticker: str
    canonical_name: str
    website: str
    ir_url: str
    asset_type: str
    ticker_aliases: tuple[str, ...] = ()
    company_aliases: tuple[str, ...] = ()
    ceo_names: tuple[str, ...] = ()
    product_names: tuple[str, ...] = ()
    business_lines: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()

    @property
    def all_query_names(self) -> tuple[str, ...]:
        values = (
            self.primary_ticker,
            *self.ticker_aliases,
            self.canonical_name,
            *self.company_aliases,
            *self.ceo_names,
            *self.product_names,
        )
        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join((value or "").split()).strip()
            normalized = normalize_entity_text(cleaned)
            if not cleaned or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            names.append(cleaned)
        return tuple(names)

    @property
    def indirect_query_terms(self) -> tuple[str, ...]:
        values = (
            self.primary_ticker,
            self.canonical_name,
            *self.product_names,
            *self.business_lines,
            *self.themes,
        )
        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join((value or "").split()).strip()
            normalized = normalize_entity_text(cleaned)
            if not cleaned or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            names.append(cleaned)
        return tuple(names)


@dataclass(frozen=True)
class AttributionResult:
    company_id: str | None
    is_match: bool
    match_types: tuple[str, ...] = ()
    matched_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntitySyncResult:
    ticker: str
    status: str
    company_id: str | None = None
    error: str | None = None


class CompanyProfileSource(Protocol):
    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        """Return a normalized company profile for one ticker."""


class StaticCompanyProfileSource:
    """Test-friendly in-memory source of company profiles."""

    def __init__(self, profiles: dict[str, CompanyProfile]):
        self.profiles = {normalize_ticker(key): value for key, value in profiles.items()}

    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        return self.profiles.get(normalize_ticker(ticker))


class YamlSeedCompanyProfileSource:
    """
    Seeded profile source backed by YAML.

    Profiles may include curated facts while still pointing to canonical company / IR URLs.
    When explicit CEO or products are missing, the source makes a best-effort extraction from
    fetched website text, but the curated YAML remains the safest path.
    """

    def __init__(self, seed_path: str | Path, *, timeout: int = 15):
        self.seed_path = Path(seed_path)
        self.timeout = timeout
        self._seed_rows = _load_company_profile_seeds(self.seed_path)

    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        row = self._seed_rows.get(normalize_ticker(ticker))
        if row is None:
            return None

        website = row["website"]
        ir_url = row["ir_url"]
        try:
            fetched_text = _fetch_profile_text(ir_url or website, timeout=self.timeout)
        except requests.RequestException:
            fetched_text = ""
        ceo_name = row.get("ceo_name") or _extract_ceo_name(fetched_text)
        products = tuple(row.get("products") or ())
        if not products:
            products = tuple(_extract_candidate_products(fetched_text))
        return CompanyProfile(
            ticker=normalize_ticker(row["ticker"]),
            canonical_name=row["canonical_name"],
            website=website,
            ir_url=ir_url,
            ceo_name=ceo_name,
            aliases=tuple(row.get("aliases") or ()),
            ceo_aliases=tuple(row.get("ceo_aliases") or ()),
            products=products,
            business_lines=tuple(row.get("business_lines") or ()),
            themes=tuple(row.get("themes") or ()),
            asset_type=row.get("asset_type", "company"),
        )


class AutoCompanyProfileSource:
    """Automatically resolve company profiles from public finance metadata and Wikidata."""

    YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
    WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"
    WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

    def __init__(self, *, timeout: int = 20):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self._wikidata_search_cache: dict[str, list[dict]] = {}
        self._wikidata_entity_cache: dict[str, dict] = {}
        self._wikidata_label_cache: dict[str, str | None] = {}

    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        quote = self._fetch_quote(ticker)
        if quote is None:
            return None
        quote_type = str(quote.get("quoteType") or "").upper()
        if quote_type != "EQUITY":
            return None

        canonical_name = self._clean_company_name(
            quote.get("longname") or quote.get("shortname") or normalize_ticker(ticker)
        )
        qid = self._resolve_wikidata_qid(normalize_ticker(ticker), canonical_name)
        if qid is None:
            return None

        entity = self._fetch_wikidata_entity(qid)
        website = self._select_wikidata_website(entity)
        if not website:
            return None
        try:
            website_text, website_html = self._fetch_website_bundle(website)
        except requests.RequestException:
            website_text, website_html = "", ""
        ir_url = self._discover_ir_url(website, website_html) or website

        ceo_name = self._extract_current_ceo_name(entity)
        products = self._extract_product_names(entity)
        if not products:
            products = tuple(_extract_candidate_products(website_text))

        aliases = tuple(
            alias
            for alias in {
                canonical_name,
                self._clean_company_name(quote.get("shortname") or ""),
                self._clean_company_name(quote.get("longname") or ""),
            }
            if alias and normalize_entity_text(alias) != normalize_entity_text(canonical_name)
        )

        return CompanyProfile(
            ticker=normalize_ticker(ticker),
            canonical_name=canonical_name,
            website=website,
            ir_url=ir_url,
            ceo_name=ceo_name,
            aliases=aliases,
            products=products,
            business_lines=(),
            themes=(),
            asset_type="company",
        )

    def _fetch_quote(self, ticker: str) -> dict | None:
        response = self.session.get(
            self.YAHOO_SEARCH_URL,
            params={
                "q": normalize_ticker(ticker),
                "lang": "en-US",
                "region": "US",
                "quotesCount": 10,
                "newsCount": 0,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        quotes = payload.get("quotes", [])
        normalized = normalize_ticker(ticker)
        for quote in quotes:
            if normalize_ticker(str(quote.get("symbol") or "")) == normalized:
                return quote
        return None

    def _resolve_wikidata_qid(self, ticker: str, canonical_name: str) -> str | None:
        search_terms = [canonical_name]
        stripped_name = self._strip_company_suffixes(canonical_name)
        if stripped_name and stripped_name != canonical_name:
            search_terms.append(stripped_name)

        normalized_company = normalize_entity_text(canonical_name)
        fallback_candidates: list[dict] = []
        for search_term in search_terms:
            candidates = self._search_wikidata_entities(search_term)
            if not candidates:
                continue
            for candidate in candidates:
                label = candidate.get("label", "")
                description = (candidate.get("description") or "").casefold()
                if normalize_entity_text(label) == normalized_company and self._description_looks_like_company(description):
                    entity = self._fetch_wikidata_entity(candidate["id"])
                    if self._select_wikidata_website(entity):
                        return candidate["id"]
                    fallback_candidates.append(candidate)
            for candidate in candidates:
                description = (candidate.get("description") or "").casefold()
                if self._description_looks_like_company(description):
                    entity = self._fetch_wikidata_entity(candidate["id"])
                    if self._select_wikidata_website(entity):
                        return candidate["id"]
                    fallback_candidates.append(candidate)
            fallback_candidates.extend(candidates)

        for candidate in fallback_candidates:
            if candidate.get("id"):
                return candidate["id"]
        return None

    def _fetch_wikidata_entity(self, qid: str) -> dict:
        if qid in self._wikidata_entity_cache:
            return self._wikidata_entity_cache[qid]
        payload = self._get_json_with_retries(self.WIKIDATA_ENTITY_URL.format(qid=qid))
        entity = payload["entities"][qid]
        self._wikidata_entity_cache[qid] = entity
        return entity

    def _select_wikidata_website(self, entity: dict) -> str | None:
        candidates: list[str] = []
        for statement in entity.get("claims", {}).get("P856", []):
            snak = statement.get("mainsnak", {})
            datavalue = snak.get("datavalue", {})
            website = datavalue.get("value")
            if isinstance(website, str) and website.startswith("http"):
                candidates.append(website.rstrip("/"))
        if not candidates:
            return None
        candidates.sort(key=self._website_priority)
        return candidates[0]

    def _fetch_website_bundle(self, website: str) -> tuple[str, str]:
        response = self.session.get(website, timeout=self.timeout)
        response.raise_for_status()
        html = response.text
        extracted = trafilatura.extract(html, include_links=False, include_images=False) or ""
        text = " ".join(extracted.split()) if extracted else " ".join(html.split())
        return text, html

    def _discover_ir_url(self, website: str, html: str) -> str | None:
        anchor_matches = re.findall(
            r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for href, label in anchor_matches:
            cleaned_label = re.sub(r"<[^>]+>", " ", label)
            text = " ".join(cleaned_label.split()).casefold()
            if "investor" in text or "ir" == text:
                return urljoin(website, href).rstrip("/")
        return None

    def _extract_current_ceo_name(self, entity: dict) -> str | None:
        statements = entity.get("claims", {}).get("P169", [])
        current_candidates: list[tuple[str, str | None]] = []
        for statement in statements:
            qualifiers = statement.get("qualifiers", {})
            if "P582" in qualifiers:
                continue
            target_id = self._extract_entity_id_from_statement(statement)
            if not target_id:
                continue
            start_value = _extract_qualifier_time(qualifiers.get("P580", []))
            current_candidates.append((target_id, start_value))
        if not current_candidates:
            return None
        current_candidates.sort(key=lambda item: item[1] or "", reverse=True)
        return self._fetch_wikidata_label(current_candidates[0][0])

    def _extract_product_names(self, entity: dict) -> tuple[str, ...]:
        product_ids: list[str] = []
        for pid in ("P1056", "P2283"):
            for statement in entity.get("claims", {}).get(pid, []):
                entity_id = self._extract_entity_id_from_statement(statement)
                if entity_id:
                    product_ids.append(entity_id)
        labels: list[str] = []
        for entity_id in product_ids[:6]:
            label = self._fetch_wikidata_label(entity_id)
            if not label or label in labels:
                continue
            if len(label) < 3:
                continue
            labels.append(label)
        return tuple(labels[:8])

    def _fetch_wikidata_label(self, qid: str) -> str | None:
        if qid in self._wikidata_label_cache:
            return self._wikidata_label_cache[qid]
        payload = self._get_json_with_retries(
            self.WIKIDATA_SEARCH_URL,
            params={
                "action": "wbgetentities",
                "ids": qid,
                "languages": "en",
                "format": "json",
                "props": "labels",
            },
        )
        entity = payload.get("entities", {}).get(qid, {})
        label = entity.get("labels", {}).get("en", {}).get("value")
        normalized = " ".join(label.split()) if isinstance(label, str) and label.strip() else None
        self._wikidata_label_cache[qid] = normalized
        return normalized

    def _extract_entity_id_from_statement(self, statement: dict) -> str | None:
        datavalue = statement.get("mainsnak", {}).get("datavalue", {})
        value = datavalue.get("value", {})
        entity_id = value.get("id")
        return entity_id if isinstance(entity_id, str) else None

    def _clean_company_name(self, value: str) -> str:
        cleaned = " ".join((value or "").split()).strip()
        cleaned = re.sub(r"\s+\((?:The|Class [A-Z]|Common Stock).*\)$", "", cleaned, flags=re.IGNORECASE)
        return cleaned

    def _search_wikidata_entities(self, search_term: str) -> list[dict]:
        if search_term in self._wikidata_search_cache:
            return self._wikidata_search_cache[search_term]
        payload = self._get_json_with_retries(
            self.WIKIDATA_SEARCH_URL,
            params={
                "action": "wbsearchentities",
                "search": search_term,
                "language": "en",
                "format": "json",
                "limit": 10,
            },
        )
        candidates = payload.get("search", [])
        self._wikidata_search_cache[search_term] = candidates
        return candidates

    def _description_looks_like_company(self, description: str) -> bool:
        return any(
            token in description
            for token in (
                "company",
                "corporation",
                "business",
                "manufacturer",
                "airline",
                "technology",
                "cloud computing",
                "semiconductor",
                "package delivery",
            )
        )

    def _strip_company_suffixes(self, value: str) -> str:
        stripped = value.strip()
        previous = None
        while stripped and stripped != previous:
            previous = stripped
            stripped = re.sub(
                r",?\s+(incorporated|inc|corp|corporation|company|co|ltd|limited|holdings)\.?$",
                "",
                stripped,
                flags=re.IGNORECASE,
            ).strip(" ,")
        stripped = re.sub(r"\.com$", "", stripped, flags=re.IGNORECASE).strip()
        return stripped

    def _website_priority(self, website: str) -> tuple[int, int, int, str]:
        lowered = website.casefold()
        return (
            0 if lowered.startswith("https://") else 1,
            0 if lowered.endswith(".com") or ".com/" in lowered else 1,
            0 if re.search(r"/[a-z]{2}(?:-[a-z]{2})?$", lowered) is None else 1,
            lowered,
        )

    def _get_json_with_retries(self, url: str, *, params: dict | None = None, attempts: int = 5) -> dict:
        delay = 1.0
        for attempt in range(attempts):
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt == attempts - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            sleep_seconds = float(retry_after) if retry_after and retry_after.isdigit() else delay
            time.sleep(sleep_seconds)
            delay = min(delay * 2, 16.0)
        raise RuntimeError(f"Failed to fetch JSON after retries: {url}")


class SQLiteEntityStore:
    """SQLite-backed company entity KB."""

    def __init__(self, db_path: str = "company_entities.db"):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def init_db(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                company_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                website TEXT NOT NULL,
                ir_url TEXT NOT NULL,
                primary_ticker TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'company',
                last_synced_at TEXT,
                FOREIGN KEY(company_id) REFERENCES entities(entity_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS company_tickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                normalized_ticker TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                UNIQUE(company_id, normalized_ticker),
                FOREIGN KEY(company_id) REFERENCES companies(company_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                alias_type TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                UNIQUE(entity_id, alias_type, normalized_alias),
                FOREIGN KEY(entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entity_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                to_entity_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                UNIQUE(from_entity_id, relation_type, to_entity_id, source_url, observed_at),
                FOREIGN KEY(from_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
                FOREIGN KEY(to_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_sync_runs (
                run_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                company_id TEXT,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_url TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_company_tickers_normalized_ticker ON company_tickers(normalized_ticker)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_aliases_normalized_alias ON entity_aliases(normalized_alias)"
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def begin_sync_run(self, ticker: str, *, source_type: str, source_url: str | None = None) -> str:
        if self.conn is None:
            self.init_db()
        run_id = f"sync-{normalize_ticker(ticker)}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.conn.execute(
            """
            INSERT INTO entity_sync_runs
            (run_id, ticker, company_id, status, source_type, source_url, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                normalize_ticker(ticker),
                None,
                "running",
                source_type,
                source_url,
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()
        return run_id

    def finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        company_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.conn is None:
            self.init_db()
        self.conn.execute(
            """
            UPDATE entity_sync_runs
            SET company_id = ?, status = ?, completed_at = ?, error = ?
            WHERE run_id = ?
            """,
            (
                company_id,
                status,
                datetime.now().isoformat(),
                error,
                run_id,
            ),
        )
        self.conn.commit()

    def upsert_company_profile(
        self,
        profile: CompanyProfile,
        *,
        source_type: str = "company_website",
        observed_at: datetime | None = None,
    ) -> str:
        if self.conn is None:
            self.init_db()
        observed_at = observed_at or datetime.now()
        ticker = normalize_ticker(profile.ticker)
        company_id = self._ensure_entity(
            entity_type="company",
            canonical_name=profile.canonical_name,
            preferred_id=f"company:{ticker}",
        )
        self.conn.execute(
            """
            INSERT INTO companies
            (company_id, canonical_name, website, ir_url, primary_ticker, asset_type, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                canonical_name = excluded.canonical_name,
                website = excluded.website,
                ir_url = excluded.ir_url,
                primary_ticker = excluded.primary_ticker,
                asset_type = excluded.asset_type,
                last_synced_at = excluded.last_synced_at
            """,
            (
                company_id,
                profile.canonical_name,
                profile.website,
                profile.ir_url,
                ticker,
                profile.asset_type,
                observed_at.isoformat(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO company_tickers (company_id, ticker, normalized_ticker, is_primary)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(company_id, normalized_ticker) DO UPDATE SET
                ticker = excluded.ticker,
                is_primary = excluded.is_primary
            """,
            (company_id, ticker, ticker, 1),
        )
        self._upsert_alias(company_id, ticker, "ticker")
        self._upsert_alias(company_id, profile.canonical_name, "company_name")
        for alias in profile.aliases:
            self._upsert_alias(company_id, alias, "company_name")

        self._mark_current_relations_not_current(company_id, CURRENT_RELATION_TYPES)

        if profile.ceo_name:
            person_id = self._ensure_entity(
                entity_type="person",
                canonical_name=profile.ceo_name,
                preferred_id=f"person:{normalize_entity_text(profile.ceo_name)}",
            )
            self._upsert_alias(person_id, profile.ceo_name, "ceo_name")
            for alias in profile.ceo_aliases:
                self._upsert_alias(person_id, alias, "ceo_name")
            self._upsert_relation(
                company_id,
                "has_ceo",
                person_id,
                source_url=profile.ir_url or profile.website,
                source_type=source_type,
                observed_at=observed_at,
            )

        for product in profile.products:
            product_id = self._ensure_entity(
                entity_type="product",
                canonical_name=product,
                preferred_id=f"product:{normalize_entity_text(product)}",
            )
            self._upsert_alias(product_id, product, "product_name")
            self._upsert_relation(
                company_id,
                "has_product",
                product_id,
                source_url=profile.website or profile.ir_url,
                source_type=source_type,
                observed_at=observed_at,
            )

        for business_line in profile.business_lines:
            business_line_id = self._ensure_entity(
                entity_type="business_line",
                canonical_name=business_line,
                preferred_id=f"business_line:{normalize_entity_text(business_line)}",
            )
            self._upsert_alias(business_line_id, business_line, "business_line_name")
            self._upsert_relation(
                company_id,
                "has_business_line",
                business_line_id,
                source_url=profile.website or profile.ir_url,
                source_type=source_type,
                observed_at=observed_at,
            )

        for theme in profile.themes:
            theme_id = self._ensure_entity(
                entity_type="theme",
                canonical_name=theme,
                preferred_id=f"theme:{normalize_entity_text(theme)}",
            )
            self._upsert_alias(theme_id, theme, "theme_name")
            self._upsert_relation(
                company_id,
                "has_theme",
                theme_id,
                source_url=profile.website or profile.ir_url,
                source_type=source_type,
                observed_at=observed_at,
            )

        self.conn.commit()
        return company_id

    def load_company_by_ticker(self, ticker: str) -> CompanyContext | None:
        if self.conn is None:
            self.init_db()
        normalized_ticker = normalize_ticker(ticker)
        row = self.conn.execute(
            """
            SELECT c.*
            FROM companies c
            JOIN company_tickers ct ON ct.company_id = c.company_id
            WHERE ct.normalized_ticker = ?
            ORDER BY ct.is_primary DESC, c.company_id
            LIMIT 1
            """,
            (normalized_ticker,),
        ).fetchone()
        if row is None:
            return None
        company_id = row["company_id"]
        return CompanyContext(
            company_id=company_id,
            primary_ticker=row["primary_ticker"],
            canonical_name=row["canonical_name"],
            website=row["website"],
            ir_url=row["ir_url"],
            asset_type=row["asset_type"],
            ticker_aliases=self._list_aliases(company_id, "ticker", exclude=(row["primary_ticker"],)),
            company_aliases=self._list_aliases(company_id, "company_name", exclude=(row["canonical_name"],)),
            ceo_names=self._list_related_aliases(company_id, "has_ceo", "ceo_name"),
            product_names=self._list_related_aliases(company_id, "has_product", "product_name"),
            business_lines=self._list_related_aliases(company_id, "has_business_line", "business_line_name"),
            themes=self._list_related_aliases(company_id, "has_theme", "theme_name"),
        )

    def attribute_article_to_company(self, article_text: str, company: CompanyContext) -> AttributionResult:
        haystack = normalize_search_text(article_text)
        match_types: list[str] = []
        matched_aliases: list[str] = []
        alias_groups = (
            ("ticker", (company.primary_ticker, *company.ticker_aliases)),
            ("company", (company.canonical_name, *company.company_aliases)),
            ("ceo", company.ceo_names),
            ("product", company.product_names),
        )
        for match_type, aliases in alias_groups:
            group_hits = [alias for alias in aliases if alias and alias_in_text(alias, haystack)]
            if not group_hits:
                continue
            match_types.append(match_type)
            matched_aliases.extend(group_hits)
        return AttributionResult(
            company_id=company.company_id if match_types else None,
            is_match=bool(match_types),
            match_types=tuple(match_types),
            matched_aliases=tuple(_dedupe_preserve_order(matched_aliases)),
        )

    def _ensure_entity(self, *, entity_type: str, canonical_name: str, preferred_id: str) -> str:
        cleaned_name = require_text(canonical_name, "canonical_name")
        normalized_name = normalize_entity_text(cleaned_name)
        rows = self.conn.execute(
            """
            SELECT e.entity_id, e.canonical_name, a.normalized_alias
            FROM entities e
            LEFT JOIN entity_aliases a ON a.entity_id = e.entity_id
            WHERE e.entity_type = ?
              AND (e.entity_id = ? OR a.normalized_alias = ?)
            ORDER BY e.entity_id
            """,
            (entity_type, preferred_id, normalized_name),
        ).fetchall()
        row = next(
            (
                candidate
                for candidate in rows
                if candidate["entity_id"] == preferred_id
                or normalize_entity_text(candidate["canonical_name"]) == normalized_name
                or candidate["normalized_alias"] == normalized_name
            ),
            None,
        )
        if row is not None:
            entity_id = row["entity_id"]
            self.conn.execute(
                "UPDATE entities SET canonical_name = ?, status = ? WHERE entity_id = ?",
                (cleaned_name, "active", entity_id),
            )
            return entity_id

        self.conn.execute(
            """
            INSERT INTO entities (entity_id, entity_type, canonical_name, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (preferred_id, entity_type, cleaned_name, "active", datetime.now().isoformat()),
        )
        return preferred_id

    def _upsert_alias(self, entity_id: str, alias: str, alias_type: str) -> None:
        cleaned_alias = require_text(alias, "alias")
        self.conn.execute(
            """
            INSERT INTO entity_aliases (entity_id, alias, alias_type, normalized_alias)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_id, alias_type, normalized_alias) DO UPDATE SET alias = excluded.alias
            """,
            (entity_id, cleaned_alias, alias_type, normalize_entity_text(cleaned_alias)),
        )

    def _mark_current_relations_not_current(self, from_entity_id: str, relation_types: tuple[str, ...]) -> None:
        placeholders = ",".join("?" for _ in relation_types)
        self.conn.execute(
            f"""
            UPDATE entity_relations
            SET is_current = 0
            WHERE from_entity_id = ?
              AND relation_type IN ({placeholders})
              AND is_current = 1
            """,
            (from_entity_id, *relation_types),
        )

    def _upsert_relation(
        self,
        from_entity_id: str,
        relation_type: str,
        to_entity_id: str,
        *,
        source_url: str,
        source_type: str,
        observed_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO entity_relations
            (from_entity_id, relation_type, to_entity_id, source_url, source_type, observed_at, is_current)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                from_entity_id,
                relation_type,
                to_entity_id,
                source_url,
                source_type,
                observed_at.isoformat(),
            ),
        )

    def _list_aliases(self, entity_id: str, alias_type: str, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
        excluded = {normalize_entity_text(item) for item in exclude}
        rows = self.conn.execute(
            """
            SELECT alias
            FROM entity_aliases
            WHERE entity_id = ? AND alias_type = ?
            ORDER BY id
            """,
            (entity_id, alias_type),
        ).fetchall()
        values: list[str] = []
        seen: set[str] = set()
        for row in rows:
            alias = row["alias"]
            normalized = normalize_entity_text(alias)
            if normalized in excluded or normalized in seen:
                continue
            seen.add(normalized)
            values.append(alias)
        return tuple(values)

    def _list_related_aliases(self, company_id: str, relation_type: str, alias_type: str) -> tuple[str, ...]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT a.alias
            FROM entity_relations r
            JOIN entity_aliases a ON a.entity_id = r.to_entity_id
            WHERE r.from_entity_id = ?
              AND r.relation_type = ?
              AND r.is_current = 1
              AND a.alias_type = ?
            ORDER BY a.id
            """,
            (company_id, relation_type, alias_type),
        ).fetchall()
        return tuple(row["alias"] for row in rows)


def build_direct_expanded_query(company: CompanyContext) -> str:
    return " ".join(company.all_query_names)


def build_indirect_expanded_query(company: CompanyContext) -> str:
    return " ".join(company.indirect_query_terms)


def build_expanded_query(company: CompanyContext) -> str:
    return build_direct_expanded_query(company)


def load_tickers_from_annotation_dir(annotation_dir: str | Path) -> list[str]:
    path = Path(annotation_dir)
    tickers = sorted(
        normalize_ticker(item.stem)
        for item in path.glob("*.yaml")
        if item.is_file()
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for ticker in tickers:
        if ticker in seen:
            continue
        seen.add(ticker)
        ordered.append(ticker)
    return ordered


def sync_ticker_universe(
    annotation_dir: str | Path,
    store: SQLiteEntityStore,
    profile_source: CompanyProfileSource,
    *,
    source_type: str = "company_website",
) -> list[EntitySyncResult]:
    results: list[EntitySyncResult] = []
    for ticker in load_tickers_from_annotation_dir(annotation_dir):
        run_id = store.begin_sync_run(ticker, source_type=source_type)
        try:
            profile = profile_source.fetch_profile(ticker)
            if profile is None:
                store.finish_sync_run(run_id, status="missing", error="No company profile available")
                results.append(EntitySyncResult(ticker=ticker, status="missing", error="No company profile available"))
                continue
            company_id = store.upsert_company_profile(profile, source_type=source_type)
            store.finish_sync_run(run_id, status="success", company_id=company_id)
            results.append(EntitySyncResult(ticker=ticker, status="success", company_id=company_id))
        except Exception as exc:
            store.finish_sync_run(run_id, status="failed", error=str(exc))
            results.append(EntitySyncResult(ticker=ticker, status="failed", error=str(exc)))
    return results


def save_company_profiles_seed(profiles: list[CompanyProfile], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profiles": [
            {
                "ticker": profile.ticker,
                "canonical_name": profile.canonical_name,
                "website": profile.website,
                "ir_url": profile.ir_url,
                "ceo_name": profile.ceo_name,
                "aliases": list(profile.aliases),
                "ceo_aliases": list(profile.ceo_aliases),
                "products": list(profile.products),
                "business_lines": list(profile.business_lines),
                "themes": list(profile.themes),
                "asset_type": profile.asset_type,
            }
            for profile in profiles
        ]
    }
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return output_path


def require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    return cleaned


def normalize_entity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def normalize_ticker(value: str) -> str:
    return " ".join((value or "").split()).strip().upper()


def normalize_search_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


def alias_in_text(alias: str, normalized_haystack: str) -> bool:
    normalized_alias = normalize_search_text(alias)
    if not normalized_alias:
        return False
    haystack = f" {normalized_haystack} "
    needle = f" {normalized_alias} "
    return needle in haystack


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_entity_text(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value)
    return deduped


def _load_company_profile_seeds(path: Path) -> dict[str, dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Company profile seed YAML must contain a top-level mapping")
    profiles = raw.get("profiles", [])
    if not isinstance(profiles, list):
        raise ValueError("Company profile seed YAML must contain a 'profiles' list")
    rows: dict[str, dict] = {}
    for index, item in enumerate(profiles):
        if not isinstance(item, dict):
            raise ValueError(f"profiles[{index}] must be an object")
        ticker = normalize_ticker(require_text(item.get("ticker"), f"profiles[{index}].ticker"))
        canonical_name = require_text(item.get("canonical_name"), f"profiles[{index}].canonical_name")
        website = require_text(item.get("website"), f"profiles[{index}].website")
        ir_url = require_text(item.get("ir_url"), f"profiles[{index}].ir_url")
        rows[ticker] = {
            "ticker": ticker,
            "canonical_name": canonical_name,
            "website": website,
            "ir_url": ir_url,
            "ceo_name": item.get("ceo_name"),
            "aliases": tuple(item.get("aliases") or ()),
            "ceo_aliases": tuple(item.get("ceo_aliases") or ()),
            "products": tuple(item.get("products") or ()),
            "business_lines": tuple(item.get("business_lines") or ()),
            "themes": tuple(item.get("themes") or ()),
            "asset_type": item.get("asset_type", "company"),
        }
    return rows


def _fetch_profile_text(url: str, *, timeout: int) -> str:
    if not url:
        return ""
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    extracted = trafilatura.extract(response.text, include_links=False, include_images=False)
    if extracted:
        return " ".join(extracted.split())
    return " ".join(response.text.split())


def _extract_ceo_name(text: str) -> str | None:
    patterns = (
        r"([A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){1,3})\s*,\s*(?:chief executive officer|ceo)",
        r"(?:chief executive officer|ceo)\s*[:\-]?\s*([A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){1,3})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split())
    return None


def _extract_qualifier_time(snaks: list[dict]) -> str | None:
    for snak in snaks:
        time_value = snak.get("datavalue", {}).get("value", {}).get("time")
        if isinstance(time_value, str):
            return time_value
    return None


def _extract_candidate_products(text: str) -> list[str]:
    matches = re.findall(r"([A-Z][A-Za-z0-9+\-]+(?:\s+[A-Z][A-Za-z0-9+\-]+){0,2})", text)
    blocked = {"Chief Executive Officer", "Investor Relations", "Press Release"}
    products: list[str] = []
    for match in matches:
        cleaned = " ".join(match.split()).strip()
        if cleaned in blocked or len(cleaned) < 3:
            continue
        if cleaned in products:
            continue
        products.append(cleaned)
        if len(products) >= 8:
            break
    return products
