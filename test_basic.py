#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Basic smoke tests for rss_scrawler.

This script mocks network requests to provide a small RSS payload,
runs `fetch_rss_feeds()` and tests `save_history`/`load_history`.
"""

import os
import sys
import tempfile

import rss_scrawler


def main():
    tmp_name = None
    orig_get = rss_scrawler.requests.get
    orig_rss_feeds = rss_scrawler.RSS_FEEDS
    orig_history = rss_scrawler.HISTORY_FILE

    try:
        rss_xml = '''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item>
<title>Test Title</title>
<link>http://example.com/article1</link>
<description>This is a test description.</description>
</item>
</channel>
</rss>'''

        class FakeResp:
            def __init__(self, content_bytes):
                self.content = content_bytes

            def raise_for_status(self):
                return None

        def fake_get(url, proxies=None, timeout=None):
            return FakeResp(rss_xml.encode('utf-8'))

        # Monkeypatch requests.get
        rss_scrawler.requests.get = fake_get

        # Small feed list
        rss_scrawler.RSS_FEEDS = [{'name': 'Test', 'url': 'http://example.com/rss'}]

        # Temp history file
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_name = tmp.name
        tmp.close()
        # Ensure file does not exist so load_history returns []
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        rss_scrawler.HISTORY_FILE = tmp_name

        raw_news, new_uids = rss_scrawler.fetch_rss_feeds()
        print('raw_news:', raw_news)
        print('new_uids:', new_uids)

        assert len(raw_news) == 1, f'expected 1 news item, got {len(raw_news)}'
        assert 'Test Title' in raw_news[0]
        assert len(new_uids) == 1

        # Test history save/load
        rss_scrawler.save_history(['uid1', 'uid2'])
        loaded = rss_scrawler.load_history()
        assert isinstance(loaded, list) and 'uid2' in loaded

        print('ALL TESTS PASSED')
    except AssertionError as e:
        print('TEST FAILED:', e)
        sys.exit(2)
    except Exception as e:
        import traceback

        traceback.print_exc()
        sys.exit(3)
    finally:
        # restore
        rss_scrawler.requests.get = orig_get
        rss_scrawler.RSS_FEEDS = orig_rss_feeds
        rss_scrawler.HISTORY_FILE = orig_history
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass


if __name__ == '__main__':
    main()
