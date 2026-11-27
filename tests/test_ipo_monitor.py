import os
import tempfile
import unittest
from unittest.mock import patch

from src.analysis import ipo_monitor

CASES = {"allType": [
    {"data": ["7889", "騰勢", "初上櫃"]},
    {"data": ["45403", "全球傳動三", "有擔保可轉換公司債"]},
    {"data": ["2414", "精技", "上市增資"]},
]}
DETAIL = {"allType": [{
    "allData": [{
        "type": "申購",
        "fields": ["申購期間（起迄）", "抽籤日期", "實際承銷張數", "承銷價（元）", "申購張數"],
        "data": ["2026-11-19-2026-11-23", "2026-11-25", "451", "50.88", "451"],
    }],
    "typeExplanation3": "初上櫃",
}]}


def fake_post(endpoint, body):
    if endpoint == "IpoQueryCase":
        return CASES
    if endpoint == "IpoQueryFast":
        return DETAIL if body.get("stockNo") == "7889" else {}
    raise AssertionError(endpoint)


class IpoAlertTests(unittest.TestCase):
    def test_only_new_ipo_broadcast_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ipo_alerted.json")
            with patch.object(ipo_monitor, "_post", fake_post):
                first = ipo_monitor.fetch_ipo_alerts(store_path=path)
                second = ipo_monitor.fetch_ipo_alerts(store_path=path)
            self.assertEqual(len(first), 1)
            msg = first[0]["message"]
            self.assertIn("7889", msg)
            self.assertIn("截止 2026-11-23", msg)
            self.assertIn("50.88", msg)
            self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
