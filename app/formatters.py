def input_qty(value) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text


def format_qty(value) -> str:
    if value is None:
        return "—"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".replace(".", ",")


def format_pct(value) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def format_date(value) -> str:
    if not value:
        return "—"
    year, month, day = str(value).split("-")
    return f"{day}.{month}.{year}"


def short_date(value) -> str:
    _year, month, day = str(value).split("-")
    return f"{day}.{month}"
