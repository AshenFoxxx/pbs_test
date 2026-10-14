import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.db import connect
from app.main import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = str(Path(self.tmp.name) / "app.db")
        os.environ["SEED_DIR"] = str(Path(__file__).resolve().parents[1] / "seed")
        self.seed = Path(__file__).resolve().parents[1] / "seed"

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, client: TestClient):
        files = {}
        for field, path in zip(("prep", "weld", "docs"), sorted(self.seed.glob("*.xlsx"))):
            files[field] = (
                path.name,
                path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        return client.post("/upload", files=files, follow_redirects=False)

    def test_pages_report_correction_and_restart_without_seed(self):
        with TestClient(app) as client:
            empty = client.get("/upload")
            self.assertIn("Загрузить в систему", empty.text)
            self.assertNotIn("72,5%", empty.text)
            blocked = client.get("/", follow_redirects=False)
            self.assertEqual(blocked.status_code, 303)

            loaded = self.upload(client)
            self.assertEqual(loaded.status_code, 303)
            self.assertIn("msg=loaded", loaded.headers["location"])
            repeated = self.upload(client)
            self.assertIn("msg=already", repeated.headers["location"])

            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("72,5%", page.text)
            self.assertIn("90,6%", page.text)
            self.assertIn("69,2%", page.text)
            self.assertIn("LOT-03, LOT-04, LOT-05, LOT-07", page.text)
            self.assertIn("4 из 9 записей в итоге", page.text)

            control = client.get("/", params={"date_from": "2026-10-05", "date_to": "2026-10-06"})
            self.assertIn("95,0%", control.text)
            self.assertIn("190 из 200", control.text)
            self.assertNotIn("1200", control.text)

            prep = client.get("/prep")
            self.assertIn("факт больше плана в два раза", prep.text)
            weld = client.get("/weld")
            self.assertIn("недостоверно", weld.text)
            docs = client.get("/docs")
            self.assertIn("Мария Лебедева", docs.text)
            self.assertNotIn("LOT-01", docs.text)

            with connect() as connection:
                row = connection.execute("SELECT * FROM prep WHERE record_id = 'P-003'").fetchone()
            saved = client.post(
                f"/prep/{row['id']}",
                data={
                    "record_id": "P-003",
                    "date": "2026-10-07",
                    "batch_id": "LOT-03",
                    "employee": "Иван Морозов",
                    "plan_qty": "100",
                    "fact_qty": "100",
                },
                follow_redirects=False,
            )
            self.assertEqual(saved.status_code, 303)

            updated = client.get("/")
            self.assertIn("78,0%", updated.text)
            self.assertIn("86,7%", updated.text)
            self.assertIn("69,2%", updated.text)
            self.assertIn("390 из 500", updated.text)
            self.assertNotIn("72,5%", updated.text)
            self.assertIn("по партиям: LOT-04, LOT-05, LOT-07", updated.text)

            report = client.get("/report")
            self.assertEqual(report.status_code, 200)
            workbook = load_workbook(BytesIO(report.content))
            self.assertEqual(workbook["Итоги"]["C5"].value, 390)
            self.assertEqual(workbook["Итоги"]["D5"].value, 500)
            self.assertEqual(workbook["Итоги"]["C6"].value, 390)
            self.assertEqual(workbook["Итоги"]["D6"].value, 450)
            self.assertEqual(workbook["Итоги"]["C7"].value, 18)
            self.assertEqual(workbook["Итоги"]["D7"].value, 26)

        empty = Path(self.tmp.name) / "empty-seed"
        empty.mkdir()
        os.environ["SEED_DIR"] = str(empty)
        with TestClient(app) as again:
            restarted = again.get("/")
            self.assertIn("78,0%", restarted.text)
            self.assertIn("69,2%", restarted.text)
        with connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM prep").fetchone()[0]
            fact = connection.execute("SELECT fact_qty FROM prep WHERE record_id = 'P-003'").fetchone()[0]
        self.assertEqual(count, 9)
        self.assertEqual(fact, 100)

    def test_deleting_a_duplicate_row(self):
        with TestClient(app) as client:
            self.assertEqual(self.upload(client).status_code, 303)
            with connect() as connection:
                copies = connection.execute(
                    "SELECT id FROM prep WHERE record_id = 'P-005' ORDER BY id"
                ).fetchall()
            self.assertEqual(len(copies), 2)
            deleted = client.post(f"/prep/{copies[0]['id']}/delete", follow_redirects=False)
            self.assertEqual(deleted.status_code, 303)
            page = client.get("/")
            self.assertIn("74,0%", page.text)
            self.assertIn("88,1%", page.text)
            self.assertIn("69,2%", page.text)
            self.assertIn("по партиям: LOT-03, LOT-04, LOT-07", page.text)

    def test_swapped_files_are_rejected(self):
        paths = sorted(self.seed.glob("*.xlsx"))
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        files = {
            "prep": (paths[1].name, paths[1].read_bytes(), mime),
            "weld": (paths[0].name, paths[0].read_bytes(), mime),
            "docs": (paths[2].name, paths[2].read_bytes(), mime),
        }
        with TestClient(app) as client:
            response = client.post("/upload", files=files, follow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertIn("02_Потребность_сварки.xlsx</strong> в поле «Заготовка»", response.text)
            self.assertIn("Не хватает: employee, plan_qty, fact_qty", response.text)
            self.assertIn("Лишние: need_qty", response.text)
            self.assertIn("01_Заготовка.xlsx</strong> в поле «Потребность сварки»", response.text)
            self.assertIn("Не хватает: need_qty", response.text)
            self.assertNotIn("03_Документация.xlsx</strong> в поле", response.text)
            home = client.get("/", follow_redirects=False)
            self.assertEqual(home.status_code, 303)
            with connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM prep").fetchone()[0], 0)

    def test_clear_requires_both_checks_and_then_removes_everything(self):
        with TestClient(app) as client:
            self.upload(client)
            denied = client.post("/upload/clear", data={"phrase": "УДАЛИТЬ"}, follow_redirects=False)
            self.assertIn("msg=wipe_denied", denied.headers["location"])
            wrong = client.post(
                "/upload/clear",
                data={"confirm": "yes", "phrase": "удалить"},
                follow_redirects=False,
            )
            self.assertIn("msg=wipe_denied", wrong.headers["location"])
            with connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM prep").fetchone()[0], 9)

            wiped = client.post(
                "/upload/clear",
                data={"confirm": "yes", "phrase": "УДАЛИТЬ"},
                follow_redirects=False,
            )
            self.assertIn("msg=wiped", wiped.headers["location"])
            page = client.get("/upload")
            self.assertIn("Загрузить в систему", page.text)
            home = client.get("/", follow_redirects=False)
            self.assertEqual(home.status_code, 303)
