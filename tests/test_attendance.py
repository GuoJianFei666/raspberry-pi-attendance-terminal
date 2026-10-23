import concurrent.futures
import csv
import http.client
import io
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from attendance.__main__ import configuration
from attendance.counting import CountWindow, Detection, select_people, validate_polygon
from attendance.server import AppServer
from attendance.storage import ConflictError, Store, parse_roster
from attendance.vision import LiveState, VisionWorker, YoloDetector

ROSTER = "student_id,name\nEX001,示例甲\nEX002,示例乙\n"
POLYGON = validate_polygon([[0.1, 0.3], [0.9, 0.3], [0.9, 0.95], [0.1, 0.95]])


def payload(roster=ROSTER):
    return {"title": "功能检查课", "class_name": "示例班", "teacher": "示例教师",
            "scheduled_at": "2026-10-23T14:00:00+08:00", "roster_csv": roster}


def confirmation(revision=1):
    return {"revision": revision, "statuses": {"EX001": "present", "EX002": "late"}, "note": "示例数据"}


class CountingTests(unittest.TestCase):
    def test_teacher_nonperson_and_low_confidence_are_excluded(self):
        rows = [Detection(30, 30, 50, 80, 0.9, 0), Detection(40, 0, 60, 20, 0.9, 0),
                Detection(30, 30, 50, 80, 0.99, 1), Detection(30, 30, 50, 80, 0.2, 0)]
        self.assertEqual(select_people(rows, 100, 100, POLYGON), [rows[0]])

    def test_boundary_is_counted_and_empty_frame_is_zero(self):
        box = Detection(0, 10, 20, 30, 0.45, 0)
        self.assertEqual(len(select_people([box], 100, 100, POLYGON)), 1)
        self.assertEqual(select_people([], 100, 100, POLYGON), [])

    def test_invalid_detector_output_is_rejected(self):
        for box in [Detection(0, 1, float("nan"), 5, 0.9, 0), Detection(9, 2, 1, 8, 0.9, 0),
                    Detection(1, 2, 110, 8, 0.9, 0), Detection(1, 2, 10, 8, 2, 0)]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                select_people([box], 100, 100, POLYGON)

    def test_polygon_validation(self):
        invalid = [[], [[0, 0], [1, 1], [0, 0]], [[0, 0], [0.5, 0.5], [1, 1]],
                   [[0, 0], [1, 0], [0.3, 0.3], [1, 1], [0, 1]],
                   [[0, 0], [2, 0], [0, 1]], [[0, 0], [1, float("nan")], [0, 1]]]
        for polygon in invalid:
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                validate_polygon(polygon)

    def test_window_filters_one_frame_outlier_and_expires_old_values(self):
        window = CountWindow(3)
        self.assertEqual([window.add(n) for n in (5, 5, 20, 6, 6)], [5, 5, 5, 6, 6])
        window.clear()
        self.assertEqual(window.add(0), 0)
        with self.assertRaises(ValueError):
            window.add(True)

    def test_unknown_config_and_invalid_class_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for value in ({"clas_ids": [0]}, {"class_ids": [{}]}, {"confidence": 2}, {"max_age": -1}):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    configuration(path)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "records.sqlite3")
        self.session = self.store.create(payload())
        self.sid = self.session["id"]

    def test_csv_preserves_identifiers_and_handles_bom(self):
        rows = parse_roster("\ufeffstudent_id,name\n0001,示例甲\n")
        self.assertEqual(rows[0]["student_id"], "0001")

    def test_bad_csv_rejected(self):
        for value in ("student_id,name\nA,甲\nA,乙\n", "id,name\nA,甲\n", "student_id,name\nA,甲,多列", "student_id,name\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_roster(value)

    def test_draft_survives_restart_and_contains_pending_students(self):
        reopened = Store(self.store.path).get(self.sid)
        self.assertEqual(reopened["records"], self.session["records"])
        self.assertTrue(all(r["status"] == "pending" for r in reopened["records"]))

    def test_partial_confirmation_rolls_back(self):
        bad = confirmation()
        bad["statuses"].pop("EX002")
        with self.assertRaises(ValueError):
            self.store.confirm(self.sid, bad, {})
        self.assertEqual(self.store.get(self.sid)["state"], "draft")
        self.assertTrue(all(r["status"] == "pending" for r in self.store.get(self.sid)["records"]))

    def test_pending_and_extra_student_rejected(self):
        for statuses in ({"EX001": "present", "EX002": "pending"},
                         {"EX001": "present", "EX002": "absent", "EX003": "late"}):
            with self.subTest(statuses=statuses), self.assertRaises(ValueError):
                self.store.confirm(self.sid, {"revision": 1, "statuses": statuses}, {})

    def test_confirm_records_observation_and_prevents_overwrite(self):
        observation = {"count": 2, "observed_at": "2026-10-23T06:00:00Z", "source": "test-fixture"}
        result = self.store.confirm(self.sid, confirmation(), observation)
        self.assertEqual((result["state"], result["revision"], result["observed_count"]), ("confirmed", 2, 2))
        with self.assertRaises(ConflictError):
            self.store.confirm(self.sid, confirmation(2), observation)

    def test_stale_revision_and_duplicate_submit_conflict(self):
        with self.assertRaises(ConflictError):
            self.store.confirm(self.sid, confirmation(9), {})
        def submit():
            try:
                self.store.confirm(self.sid, confirmation(), {})
                return "saved"
            except ConflictError:
                return "conflict"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: submit(), range(2)))
        self.assertCountEqual(outcomes, ["saved", "conflict"])

    def test_sql_text_is_data_and_csv_formula_is_escaped(self):
        item = payload("student_id,name\nEX003,=1+1\n")
        item["title"] = "'; DROP TABLE sessions;--"
        session = self.store.create(item)
        self.assertEqual(len(self.store.list("DROP TABLE")), 1)
        rows = list(csv.reader(io.StringIO(self.store.export(session["id"]).lstrip("\ufeff"))))
        self.assertEqual(rows[1][5], "'=1+1")
        self.assertEqual(len(self.store.list()), 2)

    def test_time_zone_is_required(self):
        item = payload()
        item["scheduled_at"] = "2026-10-23T14:00:00"
        with self.assertRaises(ValueError):
            self.store.create(item)


class LiveTests(unittest.TestCase):
    def test_missing_stale_and_failed_are_not_zero_people(self):
        state = LiveState(2)
        self.assertIsNone(state.snapshot()["count"])
        with patch("attendance.vision.time.monotonic", return_value=100):
            state.update(0, 0, 1, "test-fixture", b"example")
            self.assertEqual(state.snapshot()["count"], 0)
        with patch("attendance.vision.time.monotonic", return_value=103):
            self.assertEqual(state.snapshot()["state"], "stale")
            self.assertIsNone(state.snapshot()["count"])
            self.assertIsNone(state.frame())
        state.fail(RuntimeError("test failure"))
        self.assertEqual(state.snapshot()["state"], "error")
        self.assertIsNone(state.frame())

    def test_missing_model_is_not_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            YoloDetector(Path(tmp) / "absent.pt", [0], 0.45, 640)

    def test_worker_reports_failed_adapter(self):
        state = LiveState()
        worker = VisionWorker(state, "missing.pt", "picamera2", configuration())
        with patch.dict("sys.modules", {"cv2": object()}), self.assertLogs("attendance.vision", logging.ERROR):
            worker.run()
        self.assertEqual(state.snapshot()["state"], "error")
        self.assertIsNone(state.snapshot()["count"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.live = LiveState()
        cls.server = AppServer(("127.0.0.1", 0), Store(Path(cls.tmp.name) / "api.sqlite3"), cls.live)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(2)
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
            conn.request(method, path, body, {"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            result = response.read()
            return response.status, dict(response.headers), result
        finally:
            conn.close()

    def test_full_create_read_confirm_export_flow(self):
        status, _, body = self.request("POST", "/api/sessions", payload())
        self.assertEqual(status, 201)
        session = json.loads(body)
        sid = session["id"]
        status, _, _ = self.request("POST", f"/api/sessions/{sid}/confirm", confirmation())
        self.assertEqual(status, 200)
        status, _, body = self.request("GET", f"/api/sessions/{sid}")
        self.assertEqual(json.loads(body)["state"], "confirmed")
        self.assertIsNone(json.loads(body)["observed_count"])
        status, headers, body = self.request("GET", f"/api/sessions/{sid}/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn("示例甲", body.decode("utf-8"))
        status, _, _ = self.request("POST", f"/api/sessions/{sid}/confirm", confirmation())
        self.assertEqual(status, 409)

    def test_static_assets_and_no_traversal(self):
        for path in ("/", "/app.js", "/style.css"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("GET", "/../attendance/storage.py")[0], 404)

    def test_unknown_course_and_no_frame(self):
        self.assertEqual(self.request("GET", "/api/sessions/" + "0" * 32)[0], 404)
        self.assertEqual(self.request("GET", "/api/frame.jpg")[0], 404)

    def test_bad_payload_content_type_host_and_origin(self):
        for value in ([], {"revision": 1}, None):
            status, _, _ = self.request("POST", "/api/sessions", value)
            self.assertIn(status, (400, 413))
        self.assertEqual(self.request("POST", "/api/sessions", payload(), {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/sessions", payload(), {"Origin": "https://external.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/live", headers={"Host": "external.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
