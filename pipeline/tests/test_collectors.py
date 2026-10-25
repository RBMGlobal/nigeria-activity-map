"""Checks the small hosted-port guards without changing collection algorithms."""
import importlib.util
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ucdp = load("update_events_map")
gdelt = load("update_gdelt_layer")


class CollectorTests(unittest.TestCase):
    def test_ucdp_all_pages_and_count(self):
        pages = [{"Result": [{"id": 1}], "TotalPages": 2, "TotalCount": 2},
                 {"Result": [{"id": 2}], "TotalPages": 2, "TotalCount": 2}]
        with patch.object(ucdp, "api_get", side_effect=pages) as fetch:
            self.assertEqual(ucdp.fetch_all("26.1", "synthetic", "2024-10-01"), [{"id": 1}, {"id": 2}])
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("page=0", fetch.call_args_list[0].args[0])
        self.assertIn("page=1", fetch.call_args_list[1].args[0])
        self.assertIn("StartDate=2024-10-01", fetch.call_args_list[1].args[0])

    def test_ucdp_rejects_empty_intermediate_page(self):
        pages = [{"Result": [{"id": 1}], "TotalPages": 3}, {"Result": [], "TotalPages": 3}]
        with patch.object(ucdp, "api_get", side_effect=pages):
            with self.assertRaisesRegex(ValueError, "ended early"):
                ucdp.fetch_all("26.1", "synthetic")

    def test_ucdp_rejects_changing_page_totals(self):
        pages = [{"Result": [{"id": 1}], "TotalPages": 2}, {"Result": [{"id": 2}], "TotalPages": 3}]
        with patch.object(ucdp, "api_get", side_effect=pages):
            with self.assertRaisesRegex(ValueError, "changed during collection"):
                ucdp.fetch_all("26.1", "synthetic")

    def test_ucdp_rejects_wrong_record_count(self):
        with patch.object(ucdp, "api_get", return_value={"Result": [{"id": 1}], "TotalPages": 1, "TotalCount": 2}):
            with self.assertRaisesRegex(ValueError, "record count"):
                ucdp.fetch_all("26.1", "synthetic")

    def test_ucdp_rejects_malformed_response(self):
        for response in ({"error": "rate limited"}, {"Result": [], "TotalPages": "2"},
                         {"Result": [{"id": 1}], "TotalPages": 0}, {"Result": [], "TotalPages": -1}):
            with self.subTest(response=response), patch.object(ucdp, "api_get", return_value=response):
                with self.assertRaises(ValueError):
                    ucdp.fetch_all("26.1", "synthetic")

    def test_ucdp_allows_explicitly_empty_dataset_version(self):
        with patch.object(ucdp, "api_get", return_value={"Result": [], "TotalPages": 0, "TotalCount": 0}):
            self.assertEqual(ucdp.fetch_all("26.0.10", "synthetic"), [])

    def test_embedded_json_retains_values_and_cannot_end_script(self):
        event = [{"h": '</script><img src=x onerror="evil()">&\u2028\u2029'}]
        for collector in (ucdp, gdelt):
            with self.subTest(collector=collector.__name__):
                encoded = collector.embedded_json(event)
                self.assertEqual(json.loads(encoded), event)
                self.assertNotIn("<", encoded)
                self.assertNotIn(">", encoded)
                self.assertNotIn("&", encoded)
                self.assertNotIn("\u2028", encoded)
                self.assertNotIn("\u2029", encoded)

    def test_gdelt_dedup_and_filter_constants_unchanged(self):
        self.assertEqual(gdelt.URL_TPL, "https://data.gdeltproject.org/events/{day}.export.CSV.zip")
        self.assertEqual(gdelt.ROOTS, {"14", "18", "19", "20"})
        self.assertEqual(gdelt.GEO_TYPES, {"2", "3", "4", "5"})
        self.assertEqual((gdelt.OVERLAP_DAYS, gdelt.DEDUP_KM, gdelt.MIN_ARTICLES), (7, 15.0, 1))
        first = {"id": "1", "d": "2026-10-24", "la": 8.0, "lo": 8.0, "r": 19,
                 "u": "https://example.test/report", "n": 1, "x": 1}
        second = dict(first, id="2", n=3)
        result = gdelt.collapse([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["n"], result[0]["x"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
