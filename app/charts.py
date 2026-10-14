import math

from app.formatters import format_qty

PLAN = "#7f948c"
FACT = "#0e7c66"
NEED = "#b0893e"
MISSING = "#a33b32"


def render_chart(groups: list[dict], colors: list[str]) -> str:
    if not groups:
        return (
            '<svg class="chart" viewBox="0 0 640 96" role="img">'
            '<text x="24" y="52" fill="#6d675c" font-size="14">Нет достоверных данных за период</text>'
            "</svg>"
        )

    bar_width = 18
    inner_gap = 6
    group_pad = 28
    bars_in_group = max(len(group["bars"]) for group in groups)
    group_width = bars_in_group * bar_width + max(bars_in_group - 1, 0) * inner_gap + group_pad
    left = 46
    plot_top = 12
    plot_height = 168
    bottom = 32
    width = max(320, left + 12 + group_width * len(groups))
    height = plot_top + plot_height + bottom
    baseline = plot_top + plot_height
    usable = plot_height - 16
    ymax = _nice_max(_max_value(groups))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img">',
        f'<line x1="{left}" y1="{baseline}" x2="{width - 8}" y2="{baseline}" stroke="#e3dccb"/>',
    ]
    for step in range(4):
        tick = ymax * step / 3
        y = baseline - (tick / ymax) * usable
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - 8}" y2="{y:.1f}" stroke="#f0ebe1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#6d675c" font-size="11">{_tick(tick)}</text>'
        )

    for index, group in enumerate(groups):
        origin = left + index * group_width + group_pad / 2
        inner = bars_in_group * bar_width + max(bars_in_group - 1, 0) * inner_gap
        for bar_index, bar in enumerate(group["bars"]):
            color = colors[bar_index] if bar_index < len(colors) else FACT
            x = origin + bar_index * (bar_width + inner_gap)
            value = bar["value"]
            if value is None:
                parts.append(
                    f'<text x="{x + bar_width / 2:.1f}" y="{baseline - 8:.1f}" text-anchor="middle" '
                    f'fill="{MISSING}" font-size="11">{_xml(bar["caption"])}</text>'
                )
                continue
            bar_height = 0 if ymax == 0 else (float(value) / ymax) * usable
            y = baseline - bar_height
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width}" height="{max(bar_height, 0):.1f}" '
                f'rx="3" fill="{color}"><title>{_xml(bar["caption"])}</title></rect>'
            )
            parts.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                f'fill="#1e2430" font-size="11">{_xml(bar["caption"])}</text>'
            )
        label_x = origin + inner / 2
        parts.append(
            f'<text x="{label_x:.1f}" y="{baseline + 18}" text-anchor="middle" fill="#1e2430" font-size="11">{_xml(group["label"])}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _max_value(groups: list[dict]) -> float:
    values = [
        float(bar["value"])
        for group in groups
        for bar in group["bars"]
        if bar["value"] is not None
    ]
    return max(values) if values else 1


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for multiplier in (1, 2, 2.5, 5, 10):
        ceiling = multiplier * magnitude
        if value <= ceiling:
            return ceiling
    return 10 * magnitude


def _tick(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return format_qty(value)


def _xml(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
