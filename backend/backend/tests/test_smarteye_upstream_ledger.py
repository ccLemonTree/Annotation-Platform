import tempfile
import unittest
import ssl
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch


from integrations.smarteye.api.ledger_image_downloader import parse_analyse_res, _record_matches_alarm
from integrations.smarteye.api import ledger_image_downloader as downloader


class AlarmDrivenLedgerTests(unittest.TestCase):
    def setUp(self):
        from integrations.smarteye.api import ledger_watch_downloader as watch
        for item in (patch.object(downloader, 'ledger_image_tasks', {}),
                     patch.object(downloader, 'tracked_run', side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
                     patch.object(watch, 'get_runtime', return_value=Mock(get=Mock(return_value={}))),
                     patch.object(watch, '_persist_state')):
            item.start(); self.addCleanup(item.stop)

    platform = {"plat_id": "sx", "platform_name": "绍兴", "base_url": "http://example.test"}
    alarm_types = {"烟雾检测-大模型": "119", "火情检测-大模型": "113"}

    def run_platform(self, candidates, record_type="119", error=None, parsed=False, records=None, download=True):
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            source = stack.enter_context(patch.object(
                downloader, "query_alarm_candidates", create=True,
                return_value=candidates, side_effect=error,
            ))
            login = stack.enter_context(patch.object(downloader, "login_platform", return_value={"success": True}))
            devices = stack.enter_context(patch.object(downloader, "query_devices_by_alarm_type", return_value=[]))
            stack.enter_context(patch.object(downloader, "get_cached_devices", create=True, return_value=[{"device_id": "all-device"}]))
            stack.enter_context(patch.object(downloader, "_build_alarm_filter", side_effect=lambda name, code, *args: {
                "alarm_code": code, "safe_alarm": name, "is_large_model": "大模型" in name,
                "labels": set(), "normalized_name": name,
            }))
            records = records if records is not None else [
                {"analyseId": "image-1", "deviceId": "alarm-device", "captureTime": "2026-10-14 00:00:05", "backPath": "/original.jpg"}
            ]
            page = stack.enter_context(patch.object(downloader, "query_ledger_record_page", return_value=(records, len(records))))
            stack.enter_context(patch.object(downloader, "parse_analyse_res", return_value={
                "model_type": "大模型", "model_name": "烟雾检测-大模型",
                "alarm_type_id": record_type,
            } if parsed is False else parsed))
            image = stack.enter_context(patch.object(downloader, "_download_image", return_value=download if not callable(download) else None,
                                                    side_effect=download if callable(download) else None))
            result = downloader.process_single_platform(
                self.platform, self.alarm_types, "2026-10-13 20:00:00", "2026-10-14 00:01:00", Path(temp_dir),
                small_model_mode="full",
            )
        return result, source, login, devices, page, image

    def test_only_alarm_devices_and_fifteen_second_windows_are_queried(self):
        rows = [{"id": n, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"} for n in (1, 2)]
        result, source, _, devices, page, image = self.run_platform(rows)
        self.assertEqual(page.call_args_list[0].args[2:5], (
            "alarm-device", "2026-10-13 23:59:50", "2026-10-14 00:00:20",
        ))
        self.assertEqual(page.call_count, 1)
        devices.assert_not_called()
        self.assertEqual(source.call_args.args[1:3], ("2026-10-13 20:00:00", "2026-10-14 00:01:00"))
        self.assertEqual(result["device_count"], 1)
        self.assertEqual(result["downloaded_count"], 2)
        self.assertEqual(result["downloaded_alarm_count"], 2)
        self.assertEqual(image.call_args_list[0].args[1], image.call_args_list[1].args[1])
        self.assertTrue(image.call_args_list[0].args[1].endswith("alarm-device_2026-10-14_000005_full.jpg"))
        self.assertEqual(image.call_args.args[0], "http://example.test/original.jpg")

    def test_overlapping_alarm_windows_save_one_original_per_alarm(self):
        rows = [{"id": n, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, n), "alarm_type_id": "119"} for n in (1, 2)]
        result, _, _, _, page, image = self.run_platform(rows)
        self.assertEqual(page.call_count, 2)
        self.assertEqual(image.call_count, 2)
        self.assertEqual(result["downloaded_count"], 2)

    def test_database_alarm_type_is_authoritative(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "113"}]
        result, _, _, _, _, image = self.run_platform(rows)
        self.assertEqual(image.call_count, 1)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertIn("火情检测-大模型", image.call_args.args[1])

    def test_original_is_saved_even_when_ledger_analysis_is_unparseable(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"}]
        result, _, _, _, _, image = self.run_platform(rows, parsed=None)
        self.assertEqual(result["downloaded_alarm_count"], 1)
        self.assertEqual(image.call_count, 1)

    def test_nearest_original_is_selected_within_the_alarm_window(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"}]
        records = [{"deviceId": "alarm-device", "captureTime": f"2026-10-14 00:00:{second:02}", "backPath": path}
                   for second, path in [(18, "/far.jpg"), (6, "/nearest.jpg"), (4, "")]]
        result, _, _, _, _, image = self.run_platform(rows, records=records)
        self.assertEqual(image.call_args.args[0], "http://example.test/nearest.jpg")
        self.assertEqual(result["downloaded_alarm_count"], 1)

    def test_missing_ledger_records_are_counted_against_database_alarms(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"}]
        result, _, _, _, _, image = self.run_platform(rows, records=[])
        self.assertEqual(result["unmatched_alarm_count"], 1)
        self.assertEqual(result["alarm_count"], result["downloaded_alarm_count"] + result["unmatched_alarm_count"] + result["failed_alarm_count"])
        image.assert_not_called()

    def test_equal_distance_prefers_original_back_path_over_capture_path(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"}]
        records = [
            {"deviceId": "alarm-device", "captureTime": "2026-10-14 00:00:04", "capturePath": "/expired_capture.jpg"},
            {"deviceId": "alarm-device", "captureTime": "2026-10-14 00:00:06", "backPath": "/original.jpg"},
        ]
        _, _, _, _, _, image = self.run_platform(rows, records=records)
        self.assertEqual(image.call_args.args[0], "http://example.test/original.jpg")

    def test_small_model_database_alarm_is_saved_despite_negative_verify(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "110"}]
        with patch.dict(self.alarm_types, {"烟雾识别": "110"}, clear=True):
            result, _, _, _, _, image = self.run_platform(rows, parsed=None)
        self.assertEqual(result["downloaded_alarm_count"], 1)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertIn("小模型", image.call_args.args[1])

    def test_failed_download_is_counted_without_silent_loss(self):
        rows = [{"id": 1, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"}]
        result, _, _, _, _, _ = self.run_platform(rows, download=False)
        self.assertEqual(result["failed_alarm_count"], 1)
        self.assertEqual(result["downloaded_alarm_count"], 0)
        self.assertEqual(result["status"], "失败")

    def test_same_filename_downloads_are_serialized(self):
        rows = [{"id": n, "device_id": "alarm-device", "alarm_time": datetime(2026, 10, 14, 0, 0, 5), "alarm_type_id": "119"} for n in range(8)]
        active = peak = 0
        lock = threading.Lock()
        def download(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.005)
            with lock:
                active -= 1
            return True
        result, _, _, _, _, image = self.run_platform(rows, download=download)
        self.assertEqual(result["downloaded_count"], 8)
        self.assertEqual(image.call_count, 8)
        self.assertEqual(peak, 1)

    def test_no_alarms_is_success_without_platform_requests(self):
        result, _, login, devices, page, _ = self.run_platform([])
        self.assertEqual(result["status"], "成功")
        login.assert_not_called()
        devices.assert_not_called()
        page.assert_not_called()

    def test_database_failure_is_reported_without_full_scan(self):
        result, _, _, devices, page, _ = self.run_platform([], error=ValueError("未配置预警数据库"))
        self.assertEqual(result["status"], "失败")
        self.assertIn("未配置预警数据库", result["msg"])
        devices.assert_not_called()
        page.assert_not_called()

    def test_scheduler_uses_configured_interval_as_alarm_window(self):
        sched = downloader.SchedulerInstance("alarm-window-test")
        sched.interval_minutes = 240
        sched._stop_event = Mock()
        sched._stop_event.is_set.side_effect = [False, True]
        with patch.object(downloader, "run_ledger_image_download_task") as run:
            downloader._scheduler_loop(sched)
        start, end = run.call_args.args[3:5]
        self.assertIsNotNone(start)
        self.assertEqual((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 4 * 3600)

    def test_download_duration_does_not_extend_scheduler_interval(self):
        sched = downloader.SchedulerInstance("alarm-cadence-test")
        sched.interval_minutes = 240
        sched._stop_event = Mock()
        sched._stop_event.is_set.side_effect = [False, True]
        with patch.object(downloader, "run_ledger_image_download_task"), patch.object(downloader.time, "monotonic", side_effect=[10, 25]):
            downloader._scheduler_loop(sched)
        sched._stop_event.wait.assert_called_once_with(timeout=4 * 3600 - 15)

    def test_watch_uses_poll_interval_and_shared_alarm_downloader(self):
        from integrations.smarteye.api import ledger_watch_downloader as watch
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(watch, "process_single_platform", create=True, return_value={
            "status": "成功", "downloaded_count": 2, "cropped_count": 1, "msg": "ok",
        }) as process:
            count = watch._poll_platform("job", self.platform, {"poll_interval_seconds": 120}, self.alarm_types, Path(temp_dir))
        self.assertIsNotNone(process.call_args)
        start, end = process.call_args.args[2:4]
        self.assertEqual((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 120)
        self.assertEqual(count, 3)

    def test_download_duration_does_not_extend_watch_interval(self):
        from integrations.smarteye.api import ledger_watch_downloader as watch
        job = {"job_id": "watch-cadence-test", "poll_interval_seconds": 120}
        event = Mock()
        event.is_set.side_effect = [False, False, True]
        watch._init_job_state(job["job_id"], job)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(watch._stop_events, {job["job_id"]: event}))
            stack.enter_context(patch.object(watch, "get_watch_jobs", return_value=[job]))
            stack.enter_context(patch.object(watch, "get_smart_search_platforms", return_value=[self.platform]))
            stack.enter_context(patch.object(watch, "get_combined_platform_mapping", return_value={"绍兴": {"alarmTypes": self.alarm_types}}))
            stack.enter_context(patch.object(watch, "_poll_platform", return_value=1))
            stack.enter_context(patch.object(watch.time, "monotonic", side_effect=[10, 25]))
            watch._watch_loop(job["job_id"])
        event.wait.assert_called_once_with(105)
        watch.watch_jobs_state.pop(job["job_id"])

    def test_ledger_query_failure_is_not_reported_as_empty_success(self):
        with patch.object(downloader, "_post_with_token", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "台账"):
                downloader.query_ledger_record_page("http://example.test", {}, "device", "start", "end", 1)

    def test_task_reports_missing_database_as_failure(self):
        task_id = "missing-db-test"
        downloader.ledger_image_tasks[task_id] = {"status": "pending"}
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(patch.object(downloader, "DOWNLOAD_PATH", temp_dir))
            stack.enter_context(patch.object(downloader, "get_smart_search_platforms", return_value=[self.platform]))
            stack.enter_context(patch.object(downloader, "get_combined_platform_mapping", return_value={"绍兴": {"alarmTypes": self.alarm_types}}))
            stack.enter_context(patch.object(downloader, "process_single_platform", return_value={"status": "失败", "msg": "未配置预警数据库"}))
            downloader.run_ledger_image_download_task(task_id)
        self.assertEqual(downloader.ledger_image_tasks[task_id]["status"], "failed")
        self.assertIn("未配置预警数据库", downloader.ledger_image_tasks.pop(task_id)["message"])

    def test_twenty_thousand_alarms_are_processed_without_device_enumeration(self):
        rows = [{"id": n, "device_id": f"device-{n // 14400}",
                 "alarm_time": datetime(2026, 10, 14) + timedelta(seconds=n % 14400),
                 "alarm_type_id": "119"} for n in range(20000)]
        with patch.object(downloader, "LEDGER_DEVICE_CONCURRENCY", 5):
            result, _, _, devices, page, _ = self.run_platform(rows)
        self.assertEqual(result["alarm_count"], 20000)
        self.assertEqual(result["device_count"], 2)
        self.assertEqual(page.call_count, 20000)
        devices.assert_not_called()


class LedgerImageDownloaderTests(unittest.TestCase):
    def test_http_sessions_reuse_ssl_context_without_reloading_certificates(self):
        with ExitStack() as stack:
            sessions = [stack.enter_context(downloader._make_no_proxy_session()) for _ in range(3)]
            contexts = [session.adapters["https://"].poolmanager.connection_pool_kw["ssl_context"] for session in sessions]
            self.assertTrue(all(context is contexts[0] for context in contexts))
            self.assertEqual(contexts[0].verify_mode, ssl.CERT_NONE)
            self.assertFalse(contexts[0].check_hostname)
            self.assertTrue(all(not session.trust_env for session in sessions))

    def test_shaoxing_plain_bbox_json_is_a_small_model_record(self):
        parsed = parse_analyse_res('{"chushai-smoke":{"bbox":[{"label":"chushai-smoke","ltx":1,"lty":2,"width":3,"height":4}]}}')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["model_type"], "小模型")
        self.assertEqual(parsed["labels"], ["chushai-smoke"])

    def test_shaoxing_verify_keeps_only_positive_large_model_labels(self):
        parsed = parse_analyse_res(
            '{"chushai-smoke":{"bbox":[{"label":"chushai-smoke"}]},"chushai-fire":{"bbox":[{"label":"chushai-fire"}]}}'
            '[VERIFY][{"result":[{"label":"chushai-smoke","isAlarm":true,"desc":"含有 } 括号"},{"label":"chushai-fire","isAlarm":false}]}]'
            '[CONFIDENCE]0.9'
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["model_type"], "大模型")
        self.assertEqual(parsed["labels"], ["chushai-smoke"])
        self.assertEqual(parsed["bboxes"], [{"label": "chushai-smoke"}])
        self.assertTrue(_record_matches_alarm(parsed, {
            "is_large_model": True, "labels": {"chushai-smoke"}, "alarm_code": "119",
        }))
        self.assertFalse(_record_matches_alarm(parsed, {
            "is_large_model": False, "labels": {"chushai-smoke"}, "alarm_code": "110",
        }))

    def test_shaoxing_negative_or_invalid_verify_result_is_skipped(self):
        prefix = '{"chushai-smoke":{"bbox":[{"label":"chushai-smoke"}]}}[VERIFY]'
        self.assertIsNone(parse_analyse_res(prefix + '[{"result":[{"label":"chushai-smoke","isAlarm":false}]}]'))
        self.assertIsNone(parse_analyse_res(prefix + 'not-json'))
