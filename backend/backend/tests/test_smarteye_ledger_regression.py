import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


from integrations.smarteye.api.ledger_image_downloader import (
    _download_progress_state,
    _iter_ledger_zip_files,
    _record_matches_alarm,
    _small_model_label_name,
    iter_ledger_record_pages,
    parse_analyse_res,
)


class LedgerImageDownloaderTests(unittest.TestCase):
    def test_ledger_pages_are_yielded_without_accumulating_all_records(self):
        calls = []

        def fake_post(base_url, path, payload, token_info):
            current = payload["current"]
            calls.append(current)
            return {
                "code": 0,
                "data": {
                    "records": [{"analyseId": f"record-{current}"}],
                    "total": "200",
                },
            }

        with patch("integrations.smarteye.api.ledger_image_downloader._post_with_token", fake_post):
            pages = list(
                iter_ledger_record_pages(
                    "http://example.test", {}, "device-1",
                    "2026-09-09 00:00:00", "2026-09-09 04:00:00",
                    max_pages=2,
                )
            )

        self.assertEqual(calls, [1, 2])
        self.assertEqual([[row["analyseId"] for row in page] for page in pages], [
            ["record-1"], ["record-2"],
        ])

    def test_download_progress_reports_remaining_tasks(self):
        state = _download_progress_state(completed=30, total=100, success=28, failed=2)

        self.assertEqual(state["download_pending"], 70)
        self.assertEqual(state["progress"], 75)
        self.assertIn("图片下载 30/100", state["message"])
        self.assertIn("剩余 70", state["message"])

    def test_small_model_record_matches_algorithm_label_from_alarm_type(self):
        parsed = parse_analyse_res(
            '无,{},{"26_start":{"bbox":[{"label":"26_start","confidence":0.62,'
            '"ltx":1186,"lty":294,"width":229,"height":242}]}}'
        )

        self.assertIsNotNone(parsed)
        self.assertTrue(
            _record_matches_alarm(
                parsed,
                {
                    "labels": {"26_start"},
                    "normalized_name": "烟雾",
                    "is_large_model": False,
                },
            )
        )

    def test_large_model_record_matches_detection_recognition_name_variants(self):
        parsed = {
            "model_type": "大模型",
            "model_name": "火情检测-大模型（你是一名消防员）",
            "labels": [],
            "bboxes": [],
        }

        self.assertTrue(
            _record_matches_alarm(
                parsed,
                {
                    "labels": set(),
                    "normalized_name": "火情",
                    "is_large_model": True,
                },
            )
        )

    def test_small_model_rule_rejects_same_name_large_model(self):
        parsed = {
            "model_type": "大模型",
            "model_name": "烟雾检测-大模型",
            "labels": [],
            "bboxes": [],
        }

        self.assertFalse(
            _record_matches_alarm(
                parsed,
                {
                    "labels": {"26_smoke"},
                    "normalized_name": "烟雾",
                    "is_large_model": False,
                },
            )
        )

    def test_large_model_rule_rejects_matching_small_model_label(self):
        parsed = {
            "model_type": "小模型",
            "model_name": None,
            "labels": ["26_smoke"],
            "bboxes": [{}],
        }

        self.assertFalse(
            _record_matches_alarm(
                parsed,
                {
                    "labels": {"26_smoke"},
                    "normalized_name": "烟雾",
                    "is_large_model": True,
                },
            )
        )

    def test_lishui_mq_small_model_matches_alarm_type_id(self):
        parsed = parse_analyse_res(
            '[MQ][[]],[{"alarmTypeId":26,"bbox":[{"label":"26_smoke",'
            '"confidence":0.52,"ltx":1,"lty":2,"width":3,"height":4}]}]'
        )

        self.assertEqual(parsed["model_type"], "小模型")
        self.assertTrue(_record_matches_alarm(parsed, {
            "labels": {"26_smoke"}, "alarm_code": "26",
            "normalized_name": "烟雾", "is_large_model": False,
        }))

    def test_lishui_small_model_directory_uses_alarm_name(self):
        self.assertEqual(_small_model_label_name(
            {"labels": ["26"]},
            {"labels": {"26_smoke"}, "safe_alarm": "烟雾检测"},
        ), "烟雾检测")

    def test_lishui_mq_large_model_uses_label_not_raw_response_as_name(self):
        parsed = parse_analyse_res(
            '[MQ][[{"label":"烟雾检测-大模型","confidence":1.0,"ltx":0,'
            '"lty":0,"width":1,"height":1}]],'
            '[{"alarmTypeId":26,"bbox":[]}]'
        )

        self.assertEqual(parsed["model_type"], "大模型")
        self.assertEqual(parsed["model_name"], "烟雾检测-大模型")
        self.assertTrue(_record_matches_alarm(parsed, {
            "labels": set(), "normalized_name": "烟雾", "is_large_model": True,
        }))

    def test_update_response_deduplicates_identical_bboxes(self):
        parsed = parse_analyse_res(
            '[UPDATE]{"message":"succesful","result":["无"]},'
            '{"26_smoke":{"bbox":[{"label":"26_smoke","confidence":0.52,'
            '"ltx":1966,"lty":0,"width":594,"height":959}]}},'
            '{"26_smoke":{"bbox":[{"label":"26_smoke","confidence":0.52,'
            '"ltx":1966,"lty":0,"width":594,"height":959}]}}'
        )

        self.assertEqual(parsed["labels"], ["26_smoke"])
        self.assertEqual(len(parsed["bboxes"]), 1)

    def test_zip_file_iterator_excludes_duplicate_image_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            kept = task_dir / "烟雾检测" / "kept.jpg"
            duplicate = task_dir / "重复图片" / "duplicate.jpg"
            kept.parent.mkdir(parents=True)
            duplicate.parent.mkdir(parents=True)
            kept.write_bytes(b"kept")
            duplicate.write_bytes(b"duplicate")

            files = [path.relative_to(task_dir).as_posix()
                     for path in _iter_ledger_zip_files(task_dir)]

        self.assertEqual(files, ["烟雾检测/kept.jpg"])


if __name__ == "__main__":
    unittest.main()
