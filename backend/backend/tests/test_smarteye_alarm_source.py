import os
import unittest
from unittest.mock import MagicMock, patch




class LedgerAlarmSourceTests(unittest.TestCase):
    def test_mysql_query_is_bounded_and_connection_is_closed(self):
        from integrations.smarteye.api import ledger_alarm_source as source
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        rows = [{"device_id": "device", "alarm_type_id": 119, "alarm_time": "2026-10-14 00:00:05"}]
        cursor.fetchall.return_value = rows
        with patch.dict(os.environ, {"SMARTEYE_LEDGER_ALARM_DATABASES": '{"sx":{"host":"db.test","port":30306,"user":"reader","password":"test","database":"alarms"}}'}), patch.object(source.pymysql, "connect", return_value=connection):
            result = source.query_alarm_candidates({"plat_id": "sx"}, "2026-10-13 20:00:00", "2026-10-14 00:00:00", ["119", "113"])
        sql, params = cursor.execute.call_args.args
        self.assertIn("alarm_time >= %s", sql)
        self.assertIn("alarm_time < %s", sql)
        self.assertIn("alarm_type_id IN (%s, %s)", sql)
        self.assertEqual(params, ("2026-10-13 20:00:00", "2026-10-14 00:00:00", "119", "113"))
        self.assertEqual(result, rows)
        connection.close.assert_called_once()

    def test_missing_platform_configuration_never_connects(self):
        from integrations.smarteye.api import ledger_alarm_source as source
        with patch.dict(os.environ, {"SMARTEYE_LEDGER_ALARM_DATABASES": "{}"}), patch.object(source.pymysql, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "未配置.*sx"):
                source.query_alarm_candidates({"plat_id": "sx"}, "2026-10-13 20:00:00", "2026-10-14 00:00:00", ["119"])
        connect.assert_not_called()

    def test_query_failure_closes_connection(self):
        from integrations.smarteye.api import ledger_alarm_source as source
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("timeout")
        with patch.dict(os.environ, {"SMARTEYE_LEDGER_ALARM_DATABASES": '{"sx":{"host":"db.test"}}'}), patch.object(source.pymysql, "connect", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                source.query_alarm_candidates({"plat_id": "sx"}, "2026-10-13 20:00:00", "2026-10-14 00:00:00", ["119"])
        connection.close.assert_called_once()

    def test_invalid_time_range_is_rejected_before_connecting(self):
        from integrations.smarteye.api import ledger_alarm_source as source
        with patch.object(source.pymysql, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "时间"):
                source.query_alarm_candidates({"plat_id": "sx"}, "2026-10-14 01:00:00", "2026-10-14 00:00:00", ["119"])
        connect.assert_not_called()
