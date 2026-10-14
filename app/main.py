from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl.utils.exceptions import InvalidFileException

from app.charts import FACT, NEED, PLAN, render_chart
from app.db import UploadRejected, clear_loaded_data, connect, database_ready, delete_row, fetch, import_sources, insert_docs, insert_prep, insert_weld, row_counts, update_docs, update_prep, update_weld
from app.formatters import format_date, format_pct, format_qty, input_qty
from app.metrics import STATUS_LABELS, annotate_docs, annotate_prep, annotate_weld, build_dashboard, period_bounds
from app.reporting import build_report

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["qty"] = format_qty
templates.env.filters["pct"] = format_pct
templates.env.filters["day"] = format_date
templates.env.filters["field"] = input_qty

MESSAGES = {
    "saved": "Запись сохранена. Итоги на сводке уже пересчитаны.",
    "deleted": "Запись удалена. Итоги на сводке уже пересчитаны.",
    "added": "Запись добавлена.",
    "bad": "Проверьте дату и числа. Дата — ГГГГ-ММ-ДД, в числе можно использовать запятую.",
    "missing": "Запись не найдена.",
    "loaded": "Файлы загружены. Дальше система работает из базы, исходные Excel можно убрать.",
    "bad_files": "Нужны три разных Excel: заготовка, сварка и документация.",
    "already": "Эти данные уже загружены. Повторно файлы не принимаются.",
    "wiped": "Все записи трёх участков удалены. Можно загрузить файлы заново.",
    "wipe_denied": "Ничего не удалено: нужна отметка и точное слово УДАЛИТЬ.",
}

CONFIRM_PHRASE = "УДАЛИТЬ"


class FormError(Exception):
    pass


app = FastAPI(title="Сводка участков", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.middleware("http")
async def require_upload(request: Request, call_next):
    path = request.url.path
    if path == "/upload" or path == "/health" or path.startswith("/static"):
        return await call_next(request)
    if not _ready():
        return RedirectResponse("/upload", status_code=303)
    return await call_next(request)


def render(request: Request, name: str, context: dict, active: str):
    with connect() as connection:
        problems = sum(1 for row in annotate_prep(fetch(connection, "prep")) if not row["reliable"])
    payload = {
        "request": request,
        "active": active,
        "prep_problems": problems,
        "loaded": _ready(),
        "msg": MESSAGES.get(request.query_params.get("msg", ""), ""),
        "status_labels": STATUS_LABELS,
        **context,
    }
    return templates.TemplateResponse(request, name, payload)


def redirect(path: str, code: str, row_id: int | None = None) -> RedirectResponse:
    url = f"{path}?msg={code}"
    if row_id is not None:
        url += f"#row-{row_id}"
    return RedirectResponse(url, status_code=303)


def dashboard_for(date_from: str | None, date_to: str | None) -> dict:
    with connect() as connection:
        prep = fetch(connection, "prep")
        weld = fetch(connection, "weld")
        docs = fetch(connection, "docs")
    start, end = period_bounds(prep, weld, docs)
    date_from = _valid_date(date_from) or start
    date_to = _valid_date(date_to) or end
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    model = build_dashboard(prep, weld, docs, date_from, date_to)
    model["prep_svg"] = render_chart(model["prep_chart"], [PLAN, FACT])
    model["weld_svg"] = render_chart(model["weld_chart"], [NEED, FACT])
    model["docs_svg"] = render_chart(model["docs_chart"], [PLAN, FACT])
    return model


@app.get("/upload")
def upload_page(request: Request):
    with connect() as connection:
        counts = row_counts(connection)
    return render(
        request,
        "upload.html",
        {"loaded": _ready(), "counts": counts, "confirm_phrase": CONFIRM_PHRASE},
        "upload",
    )


@app.post("/upload/clear")
def clear_uploads(confirm: str = Form(""), phrase: str = Form("")):
    if confirm != "yes" or phrase.strip() != CONFIRM_PHRASE:
        return redirect("/upload", "wipe_denied")
    if not _ready():
        return redirect("/upload", "wipe_denied")
    with connect() as connection:
        clear_loaded_data(connection)
    return redirect("/upload", "wiped")


@app.post("/upload")
async def upload_files(
    request: Request,
    prep: UploadFile = File(...),
    weld: UploadFile = File(...),
    docs: UploadFile = File(...),
):
    if _ready():
        return redirect("/upload", "already")
    sources = []
    for expected, item in (("prep", prep), ("weld", weld), ("docs", docs)):
        payload = await item.read()
        sources.append((expected, item.filename or "файл", BytesIO(payload)))
    try:
        with connect() as connection:
            import_sources(connection, sources)
    except UploadRejected as exc:
        with connect() as connection:
            counts = row_counts(connection)
        return render(
            request,
            "upload.html",
            {
                "loaded": False,
                "counts": counts,
                "confirm_phrase": CONFIRM_PHRASE,
                "errors": exc.errors,
            },
            "upload",
        )
    except (RuntimeError, InvalidFileException, BadZipFile, KeyError, ValueError):
        return redirect("/upload", "bad_files")
    return redirect("/", "loaded")


@app.get("/")
def director(request: Request, date_from: str | None = None, date_to: str | None = None):
    return render(request, "director.html", {"board": dashboard_for(date_from, date_to)}, "home")


@app.get("/report")
def report(date_from: str | None = None, date_to: str | None = None):
    model = dashboard_for(date_from, date_to)
    filename = f"otchet_{model['date_from']}_{model['date_to']}.xlsx"
    return Response(
        content=build_report(model),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/prep")
def prep_page(request: Request):
    with connect() as connection:
        rows = annotate_prep(fetch(connection, "prep"))
    return render(request, "prep.html", {"rows": rows}, "prep")


@app.post("/prep")
def create_prep(
    record_id: str = Form(""),
    date: str = Form(""),
    batch_id: str = Form(""),
    employee: str = Form(""),
    plan_qty: str = Form(""),
    fact_qty: str = Form(""),
):
    try:
        payload = _prep_payload(record_id, date, batch_id, employee, plan_qty, fact_qty)
    except FormError:
        return redirect("/prep", "bad")
    with connect() as connection:
        row_id = insert_prep(connection, payload)
    return redirect("/prep", "added", row_id)


@app.post("/prep/{row_id}")
def save_prep(
    row_id: int,
    record_id: str = Form(""),
    date: str = Form(""),
    batch_id: str = Form(""),
    employee: str = Form(""),
    plan_qty: str = Form(""),
    fact_qty: str = Form(""),
):
    try:
        payload = _prep_payload(record_id, date, batch_id, employee, plan_qty, fact_qty)
    except FormError:
        return redirect("/prep", "bad", row_id)
    with connect() as connection:
        saved = update_prep(connection, row_id, payload)
    return redirect("/prep", "saved" if saved else "missing", row_id if saved else None)


@app.post("/prep/{row_id}/delete")
def remove_prep(row_id: int):
    with connect() as connection:
        deleted = delete_row(connection, "prep", row_id)
    return redirect("/prep", "deleted" if deleted else "missing")


@app.get("/weld")
def weld_page(request: Request):
    with connect() as connection:
        prep = annotate_prep(fetch(connection, "prep"))
        rows = annotate_weld(fetch(connection, "weld"), prep)
    return render(request, "weld.html", {"rows": rows}, "weld")


@app.post("/weld")
def create_weld(
    record_id: str = Form(""),
    date: str = Form(""),
    batch_id: str = Form(""),
    need_qty: str = Form(""),
):
    try:
        payload = _weld_payload(record_id, date, batch_id, need_qty)
    except FormError:
        return redirect("/weld", "bad")
    with connect() as connection:
        row_id = insert_weld(connection, payload)
    return redirect("/weld", "added", row_id)


@app.post("/weld/{row_id}")
def save_weld(
    row_id: int,
    record_id: str = Form(""),
    date: str = Form(""),
    batch_id: str = Form(""),
    need_qty: str = Form(""),
):
    try:
        payload = _weld_payload(record_id, date, batch_id, need_qty)
    except FormError:
        return redirect("/weld", "bad", row_id)
    with connect() as connection:
        saved = update_weld(connection, row_id, payload)
    return redirect("/weld", "saved" if saved else "missing", row_id if saved else None)


@app.post("/weld/{row_id}/delete")
def remove_weld(row_id: int):
    with connect() as connection:
        deleted = delete_row(connection, "weld", row_id)
    return redirect("/weld", "deleted" if deleted else "missing")


@app.get("/docs")
def docs_page(request: Request):
    with connect() as connection:
        rows = annotate_docs(fetch(connection, "docs"))
    return render(request, "docs.html", {"rows": rows}, "docs")


@app.post("/docs")
def create_docs(
    record_id: str = Form(""),
    date: str = Form(""),
    employee: str = Form(""),
    plan_docs: str = Form(""),
    fact_docs: str = Form(""),
):
    try:
        payload = _docs_payload(record_id, date, employee, plan_docs, fact_docs)
    except FormError:
        return redirect("/docs", "bad")
    with connect() as connection:
        row_id = insert_docs(connection, payload)
    return redirect("/docs", "added", row_id)


@app.post("/docs/{row_id}")
def save_docs(
    row_id: int,
    record_id: str = Form(""),
    date: str = Form(""),
    employee: str = Form(""),
    plan_docs: str = Form(""),
    fact_docs: str = Form(""),
):
    try:
        payload = _docs_payload(record_id, date, employee, plan_docs, fact_docs)
    except FormError:
        return redirect("/docs", "bad", row_id)
    with connect() as connection:
        saved = update_docs(connection, row_id, payload)
    return redirect("/docs", "saved" if saved else "missing", row_id if saved else None)


@app.post("/docs/{row_id}/delete")
def remove_docs(row_id: int):
    with connect() as connection:
        deleted = delete_row(connection, "docs", row_id)
    return redirect("/docs", "deleted" if deleted else "missing")


@app.get("/health")
def health():
    return {"ok": True}


def _prep_payload(record_id: str, date: str, batch_id: str, employee: str, plan_qty: str, fact_qty: str) -> dict:
    return {
        "record_id": _required(record_id),
        "date": _date(date),
        "batch_id": _required(batch_id),
        "employee": _required(employee),
        "plan_qty": _qty(plan_qty),
        "fact_qty": _qty(fact_qty),
    }


def _weld_payload(record_id: str, date: str, batch_id: str, need_qty: str) -> dict:
    return {
        "record_id": _required(record_id),
        "date": _date(date),
        "batch_id": _required(batch_id),
        "need_qty": _qty(need_qty),
    }


def _docs_payload(record_id: str, date: str, employee: str, plan_docs: str, fact_docs: str) -> dict:
    return {
        "record_id": _required(record_id),
        "date": _date(date),
        "employee": _required(employee),
        "plan_docs": _qty(plan_docs),
        "fact_docs": _qty(fact_docs),
    }


def _required(value: str) -> str:
    text = value.strip()
    if not text:
        raise FormError
    return text


def _date(value: str) -> str:
    text = value.strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise FormError from exc
    return text


def _qty(value: str):
    text = value.strip().replace(" ", "").replace(",", ".")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise FormError from exc


def _ready() -> bool:
    with connect() as connection:
        return database_ready(connection)


def _valid_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _date(value)
    except FormError:
        return None
