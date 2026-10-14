"""Расчёты сводки.

Выполнение плана = сумма факта / сумма плана по достоверным строкам.
Это не среднее процентов по отдельным строкам.

Строка заготовки недостоверна, если:
- факт пустой;
- факт меньше нуля;
- факт больше плана в два раза (план × 2);
- план пустой или не больше нуля;
- record_id встречается больше одного раза.

Покрытие сварки считается только по партиям, где за выбранный период
есть ровно одна достоверная заготовка. Сомнительный факт не превращается
в процент покрытия.

Документация считается по своим строкам и не зависит от других участков.
Ноль в факте — введённое значение, а не пропуск.
"""

from collections import Counter, defaultdict

from app.formatters import format_date, format_qty, short_date

FACT_TO_PLAN_LIMIT = 2

RULES = [
    "Выполнение плана = сумма факта / сумма плана по достоверным строкам. Это не среднее процентов.",
    "Строка заготовки недостоверна, если факт пустой, меньше нуля или больше плана в два раза, если план не больше нуля, или если record_id повторяется.",
    "Покрытие сварки = сумма факта достоверной заготовки / сумма потребности. Партия без одной достоверной заготовки в процент не входит.",
    "Документация считается только по своим строкам. Правка заготовки или сварки её не меняет.",
    "Ноль в факте — это введённое значение. Пустая ячейка — пропуск, такая строка в итог не входит.",
]

STATUS_LABELS = {
    "ok": "в итоге",
    "bad_prep": "недостоверно",
    "no_prep": "нет заготовки",
    "bad_need": "проверьте потребность",
    "bad_row": "недостоверно",
}


def annotate_prep(rows: list[dict]) -> list[dict]:
    counts = Counter(row["record_id"] for row in rows)
    annotated = []
    for row in rows:
        issues = []
        if counts[row["record_id"]] > 1:
            issues.append("повтор record_id")
        if row["plan_qty"] is None or row["plan_qty"] <= 0:
            issues.append("план пустой или не больше нуля")
        if row["fact_qty"] is None:
            issues.append("нет факта")
        elif row["fact_qty"] < 0:
            issues.append("отрицательный факт")
        elif (
            row["plan_qty"] is not None
            and row["plan_qty"] > 0
            and row["fact_qty"] > row["plan_qty"] * FACT_TO_PLAN_LIMIT
        ):
            issues.append("факт больше плана в два раза")
        item = dict(row)
        item["issues"] = issues
        item["reliable"] = not issues
        annotated.append(item)
    return annotated


def annotate_docs(rows: list[dict]) -> list[dict]:
    counts = Counter(row["record_id"] for row in rows)
    annotated = []
    for row in rows:
        issues = []
        if counts[row["record_id"]] > 1:
            issues.append("повтор record_id")
        if row["plan_docs"] is None or row["plan_docs"] <= 0:
            issues.append("план пустой или не больше нуля")
        if row["fact_docs"] is None:
            issues.append("нет факта")
        elif row["fact_docs"] < 0:
            issues.append("отрицательный факт")
        item = dict(row)
        item["issues"] = issues
        item["reliable"] = not issues
        annotated.append(item)
    return annotated


def annotate_weld(rows: list[dict], prep_rows: list[dict], all_rows: list[dict] | None = None) -> list[dict]:
    counts = Counter(row["record_id"] for row in (all_rows or rows))
    by_batch = defaultdict(list)
    for prep in prep_rows:
        by_batch[prep["batch_id"]].append(prep)

    annotated = []
    for row in rows:
        issues = []
        if counts[row["record_id"]] > 1:
            issues.append("повтор record_id")
        if row["need_qty"] is None or row["need_qty"] <= 0:
            issues.append("потребность пустая или не больше нуля")

        item = dict(row)
        item["issues"] = issues
        item["prep_record"] = None
        item["prep_id"] = None
        item["prep_fact"] = None
        item["coverage"] = None
        matches = by_batch.get(row["batch_id"], [])
        reliable = [prep for prep in matches if prep["reliable"]]
        unreliable = [prep for prep in matches if not prep["reliable"]]
        link_issues = []

        if issues:
            item["reliable"] = False
            item["status"] = "bad_need" if any("потребность" in issue for issue in issues) else "bad_row"
        elif not matches:
            item["reliable"] = False
            item["status"] = "no_prep"
            link_issues.append("нет заготовки с этой партией")
        elif unreliable or len(reliable) != 1:
            item["reliable"] = False
            item["status"] = "bad_prep"
            for prep in matches:
                if prep["issues"]:
                    link_issues.append(f"{prep['record_id']}: {', '.join(prep['issues'])}")
            if len(reliable) != 1:
                link_issues.append("нет одной достоверной записи заготовки")
            item["prep_id"] = matches[0]["id"]
            item["prep_record"] = matches[0]["record_id"]
        else:
            prep = reliable[0]
            item["reliable"] = True
            item["status"] = "ok"
            item["prep_fact"] = prep["fact_qty"]
            item["prep_id"] = prep["id"]
            item["prep_record"] = prep["record_id"]
            item["coverage"] = prep["fact_qty"] / row["need_qty"]

        item["link_issues"] = link_issues
        annotated.append(item)
    return annotated


def period_bounds(*groups: list[dict]) -> tuple[str, str]:
    dates = [row["date"] for group in groups for row in group if row.get("date")]
    if not dates:
        today = "2026-10-05"
        return today, today
    return min(dates), max(dates)


def build_dashboard(prep: list[dict], weld: list[dict], docs: list[dict], date_from: str, date_to: str) -> dict:
    prep_all = annotate_prep(prep)
    docs_all = annotate_docs(docs)
    prep_rows = [row for row in prep_all if date_from <= row["date"] <= date_to]
    docs_period = [row for row in docs_all if date_from <= row["date"] <= date_to]
    weld_period = [row for row in weld if date_from <= row["date"] <= date_to]
    weld_rows = annotate_weld(weld_period, prep_rows, all_rows=weld)

    prep_totals = _sum_pair(prep_rows, "plan_qty", "fact_qty")
    docs_totals = _sum_pair(docs_period, "plan_docs", "fact_docs")
    weld_good = [row for row in weld_rows if row["reliable"]]
    weld_need = sum(row["need_qty"] for row in weld_good)
    weld_fact = sum(row["prep_fact"] for row in weld_good)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "rules": RULES,
        "prep_problem_total": sum(1 for row in prep_all if not row["reliable"]),
        "prep": {
            **prep_totals,
            "rows": prep_rows,
            "fraction": _fraction(prep_totals, "нет записей заготовки за период"),
            "note": _note(prep_totals, "записей"),
        },
        "weld": {
            "need": weld_need,
            "fact": weld_fact,
            "ratio": (weld_fact / weld_need) if weld_need else None,
            "used": len(weld_good),
            "total": len(weld_rows),
            "excluded": len(weld_rows) - len(weld_good),
            "rows": weld_rows,
            "blocked_batches": _unique(row["batch_id"] for row in weld_rows if row["status"] == "bad_prep"),
            "missing_batches": _unique(row["batch_id"] for row in weld_rows if row["status"] == "no_prep"),
            "fraction": _fraction(
                {"fact": weld_fact, "plan": weld_need, "used": len(weld_good), "total": len(weld_rows)},
                "нет записей сварки за период",
            ),
            "note": _note(
                {"used": len(weld_good), "total": len(weld_rows)},
                "партий",
            ),
        },
        "docs": {
            **docs_totals,
            "rows": docs_period,
            "fraction": _fraction(docs_totals, "нет записей документации за период"),
            "note": _note(docs_totals, "записей"),
        },
        "problems": _problems(prep_rows, prep_all),
        "by_date": _by_date(prep_rows, weld_rows, docs_period),
        "prep_chart": _date_chart(prep_rows, "plan_qty", "fact_qty"),
        "docs_chart": _date_chart(docs_period, "plan_docs", "fact_docs"),
        "weld_chart": _weld_chart(weld_rows),
    }


def _sum_pair(rows: list[dict], plan_key: str, fact_key: str) -> dict:
    good = [row for row in rows if row["reliable"]]
    plan = sum(row[plan_key] for row in good)
    fact = sum(row[fact_key] for row in good)
    return {
        "plan": plan,
        "fact": fact,
        "ratio": (fact / plan) if plan else None,
        "used": len(good),
        "total": len(rows),
        "excluded": len(rows) - len(good),
    }


def _fraction(totals: dict, empty_text: str) -> str:
    if totals["total"] == 0:
        return empty_text
    if totals["used"] == 0:
        return "нет достоверных строк"
    return f"{format_qty(totals['fact'])} из {format_qty(totals['plan'])}"


def _note(totals: dict, unit: str) -> str:
    return f"{totals['used']} из {totals['total']} {unit} в итоге"


def _problems(period_rows: list[dict], all_rows: list[dict]) -> list[dict]:
    problems = []
    seen = set()
    for row in period_rows:
        if row["reliable"]:
            continue
        if "повтор record_id" in row["issues"]:
            if row["record_id"] in seen:
                continue
            seen.add(row["record_id"])
            copies = [item for item in all_rows if item["record_id"] == row["record_id"]]
            facts = ", ".join(format_qty(item["fact_qty"]) for item in copies)
            extra = []
            for copy in copies:
                for issue in copy["issues"]:
                    if issue != "повтор record_id":
                        extra.append(f"строка {copy['id']}: {issue}")
            tail = f" Дополнительно: {'; '.join(extra)}." if extra else ""
            problems.append(
                {
                    "href": f"/prep#row-{copies[0]['id']}",
                    "code": row["record_id"],
                    "batch": row["batch_id"],
                    "date": format_date(row["date"]),
                    "text": (
                        f"Запись повторяется, копий: {len(copies)}. "
                        f"Все копии исключены из итога, иначе факт сложился бы несколько раз. "
                        f"Факты копий: {facts}.{tail}"
                    ),
                }
            )
            continue
        problems.append(
            {
                "href": f"/prep#row-{row['id']}",
                "code": row["record_id"],
                "batch": row["batch_id"],
                "date": format_date(row["date"]),
                "text": _problem_text(row),
            }
        )
    return problems


def _problem_text(row: dict) -> str:
    details = []
    if row["fact_qty"] is not None:
        details.append(f"факт {format_qty(row['fact_qty'])}")
    else:
        details.append("факт пустой")
    if row["plan_qty"] is not None:
        details.append(f"план {format_qty(row['plan_qty'])}")
    return f"{', '.join(row['issues'])}. {', '.join(details)}."


def _by_date(prep_rows: list[dict], weld_rows: list[dict], docs_rows: list[dict]) -> list[dict]:
    dates = sorted({row["date"] for row in prep_rows + weld_rows + docs_rows})
    table = []
    for day in dates:
        prep = _day_part([row for row in prep_rows if row["date"] == day], "plan_qty", "fact_qty")
        weld = _day_part([row for row in weld_rows if row["date"] == day], "need_qty", "prep_fact")
        docs = _day_part([row for row in docs_rows if row["date"] == day], "plan_docs", "fact_docs")
        table.append({"date": day, "label": format_date(day), "prep": prep, "weld": weld, "docs": docs})
    return table


def _day_part(rows: list[dict], plan_key: str, fact_key: str) -> dict:
    if not rows:
        return {"has": False, "plan": None, "fact": None, "excluded": 0}
    good = [row for row in rows if row["reliable"]]
    if not good:
        return {"has": True, "plan": None, "fact": None, "excluded": len(rows)}
    return {
        "has": True,
        "plan": sum(row[plan_key] for row in good),
        "fact": sum(row[fact_key] for row in good),
        "excluded": len(rows) - len(good),
    }


def _date_chart(rows: list[dict], plan_key: str, fact_key: str) -> list[dict]:
    dates = sorted({row["date"] for row in rows if row["reliable"]})
    groups = []
    for day in dates:
        same_day = [row for row in rows if row["date"] == day and row["reliable"]]
        plan = sum(row[plan_key] for row in same_day)
        fact = sum(row[fact_key] for row in same_day)
        groups.append(
            {
                "label": short_date(day),
                "bars": [
                    {"value": plan, "caption": format_qty(plan)},
                    {"value": fact, "caption": format_qty(fact)},
                ],
            }
        )
    return groups


def _weld_chart(rows: list[dict]) -> list[dict]:
    groups = []
    for row in rows:
        need_value = row["need_qty"] if row["need_qty"] is not None and row["need_qty"] > 0 else None
        if row["reliable"]:
            fact_bar = {"value": row["prep_fact"], "caption": format_qty(row["prep_fact"])}
        else:
            fact_bar = {"value": None, "caption": "н/д"}
        groups.append(
            {
                "label": row["batch_id"],
                "bars": [
                    {
                        "value": need_value,
                        "caption": format_qty(need_value) if need_value is not None else "н/д",
                    },
                    fact_bar,
                ],
            }
        )
    return groups


def _unique(values) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
