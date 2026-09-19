"""Tests for the AVC (奥维云网) public listing adapter and CJK length guard."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import generate_feed  # noqa: E402

AVC_SAMPLE = {
    "code": 200,
    "msg": "操作成功",
    "data": [
        {
            "id": "1009883982378041344",
            "title": "【价格快讯】9月TV面板&整机价格快报（上旬版）",
            "type": "outer",
            "coverUrl": "https://example.invalid/c.png",
            "content": None,
            "createTime": "2026-09-11 11:13:08",
            "author": "显示研究组",
            "showCnt": 4270,
            "realCnt": 27,
            "infoType": "info",
            "top": "0",
            "moduleName": "yunying",
        },
        {
            "id": "1009825813127757824",
            "title": "2026年8月家电市场简析（新零售）",
            "type": "outer",
            "createTime": "2026-09-18 14:07:15",
            "author": "数据研究组",
            "moduleName": "yunying",
        },
        {
            # Missing id: must be skipped rather than emitted with a broken URL.
            "title": "没有 id 的条目",
            "createTime": "2026-09-18 14:07:15",
        },
        {
            # Missing title: must be skipped.
            "id": "999",
            "title": "   ",
            "createTime": "2026-09-18 14:07:15",
        },
    ],
}

AVC_SRC = {
    "id": "avc_consumer",
    "name": "奥维云网（AVC）",
    "type": "avc_listing",
    "api_url": "https://example.invalid/avc",
    "article_url_template": "https://www.avc-mr.com/article/detail?id={id}",
    "term": 30,
    "domain": "consumer_electronics",
    "region": "china",
    "evidence_class": "market_data",
    "source_layer": "market_data",
}


def avc_response(payload):
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class AvcListingAdapterTests(unittest.TestCase):
    def _fetch(self, payload, since=None):
        since = since or datetime(2026, 9, 1, tzinfo=timezone.utc)
        with mock.patch.object(generate_feed.httpx, "get", return_value=avc_response(payload)):
            return generate_feed.fetch_avc_listing(AVC_SRC, since)

    def test_parses_public_cards_and_builds_detail_urls(self):
        items = self._fetch(AVC_SAMPLE)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["id"], "1009883982378041344")
        self.assertEqual(first["source"], "avc_consumer")
        self.assertEqual(
            first["url"],
            "https://www.avc-mr.com/article/detail?id=1009883982378041344",
        )
        self.assertEqual(first["published"], "2026-09-11T11:13:08+00:00")

    def test_carries_source_metadata_for_digest_lenses(self):
        items = self._fetch(AVC_SAMPLE)
        for item in items:
            self.assertEqual(item["region"], "china")
            self.assertEqual(item["evidence_class"], "market_data")
            self.assertEqual(item["source_layer"], "market_data")

    def test_skips_entries_without_id_or_title(self):
        items = self._fetch(AVC_SAMPLE)
        titles = {item["title"] for item in items}
        self.assertNotIn("没有 id 的条目", titles)
        self.assertEqual(len(items), 2)

    def test_respects_lookback_window(self):
        items = self._fetch(AVC_SAMPLE, since=datetime(2026, 9, 15, tzinfo=timezone.utc))
        self.assertEqual([item["id"] for item in items], ["1009825813127757824"])

    def test_raises_on_non_200_api_code(self):
        with self.assertRaises(ValueError):
            self._fetch({"code": 401, "msg": "未授权", "data": []})

    def test_parse_listing_datetime_accepts_common_portal_formats(self):
        parse = generate_feed.parse_listing_datetime
        self.assertEqual(parse("2026-09-18 17:58:24").hour, 17)
        self.assertEqual(parse("2026-09-18 17:58").minute, 58)
        self.assertEqual(parse("2026-09-18").day, 18)
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("not a date"))


class CjkLengthGuardTests(unittest.TestCase):
    """The min-length guard must not systematically reject Chinese headlines."""

    def setUp(self):
        path = ROOT_DIR / "config" / "filter-terms.consumer-electronics.json"
        self.content_filter = json.loads(path.read_text("utf-8"))

    def test_chinese_headline_is_not_rejected_for_being_short(self):
        headline = "Mini LED背光电视出货量创新高，面板厂商加速扩产"
        self.assertLess(len(headline), 30)
        self.assertTrue(
            generate_feed.is_relevant_content(headline, self.content_filter)
        )

    def test_cjk_characters_weigh_more_than_latin(self):
        cjk = generate_feed.effective_text_length("电视面板")
        latin = generate_feed.effective_text_length("tv")
        self.assertEqual(cjk, 12)
        self.assertGreater(cjk, latin)

    def test_bare_category_word_still_rejected(self):
        for bare in ("面板", "电视", "Apple"):
            self.assertFalse(
                generate_feed.is_relevant_content(bare, self.content_filter),
                msg=f"{bare!r} should not be admitted on its own",
            )

    def test_tv_supply_and_trend_headlines_are_admitted(self):
        headlines = [
            "IFA 2026电视技术趋势解析：Mini‑LED分化、OLED演进与AI变革",
            "2026年8月中国电视市场：海信TCL创维份额变化",
            "电视面板价格连续三月上涨，整机厂商成本承压",
            "OLED电视技术路线演进与AI画质芯片解析",
        ]
        for headline in headlines:
            self.assertTrue(
                generate_feed.is_relevant_content(headline, self.content_filter),
                msg=headline,
            )

    def test_offtopic_chinese_noise_still_rejected(self):
        noise = [
            "公司召开2026年第一次临时股东大会决议公告",
            "关于回购股份进展的公告",
            "董事会审计委员会会议决议",
        ]
        for item in noise:
            self.assertFalse(
                generate_feed.is_relevant_content(item, self.content_filter),
                msg=item,
            )


if __name__ == "__main__":
    unittest.main()
