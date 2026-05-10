import ingest_news


def test_ingest_news_reexports_main():
    assert callable(ingest_news.main)
