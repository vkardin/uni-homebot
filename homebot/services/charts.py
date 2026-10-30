"""Отрисовка графиков метрик (matplotlib, тёмная тема). Синхронная — вызывать через `asyncio.to_thread`."""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from datetime import datetime, tzinfo

from homebot.services.db import MetricRow
from homebot.ui.style import DEEP_BG as _BG
from homebot.ui.style import GRID as _GRID
from homebot.ui.style import NEON_AMBER, NEON_CYAN, NEON_PINK, NEON_PURPLE
from homebot.ui.style import PANEL_BG as _PANEL
from homebot.ui.style import TEXT as _TEXT

_GAP_SEC = 300  # разрыв больше 5 минут — линию не соединяем (бот был выключен)
_MAX_POINTS = 900


class NoDataError(ValueError):
    """За выбранный период нет ни одной записи."""


def downsample(rows: Sequence[MetricRow], max_points: int = _MAX_POINTS) -> list[MetricRow]:
    """Усредняет соседние записи, чтобы график за неделю не рисовал 10 000 точек."""
    if len(rows) <= max_points:
        return list(rows)
    step = math.ceil(len(rows) / max_points)
    result: list[MetricRow] = []
    for start in range(0, len(rows), step):
        chunk = rows[start : start + step]
        temps = [r.temp for r in chunk if r.temp is not None]
        disks = [r.disk for r in chunk if r.disk is not None]
        result.append(
            MetricRow(
                ts=chunk[len(chunk) // 2].ts,
                cpu=sum(r.cpu for r in chunk) / len(chunk),
                ram=sum(r.ram for r in chunk) / len(chunk),
                swap=sum(r.swap for r in chunk) / len(chunk),
                temp=sum(temps) / len(temps) if temps else None,
                disk=sum(disks) / len(disks) if disks else None,
            )
        )
    return result


def _with_gaps(rows: Sequence[MetricRow], tz: tzinfo, getter) -> tuple[list[datetime], list[float]]:
    """Пары (время, значение); в местах пропусков вставляет NaN, чтобы линия разрывалась."""
    xs: list[datetime] = []
    ys: list[float] = []
    previous_ts: int | None = None
    for row in rows:
        value = getter(row)
        if value is None:
            continue
        if previous_ts is not None and row.ts - previous_ts > _GAP_SEC:
            xs.append(datetime.fromtimestamp(previous_ts + 1, tz))
            ys.append(math.nan)
        xs.append(datetime.fromtimestamp(row.ts, tz))
        ys.append(float(value))
        previous_ts = row.ts
    return xs, ys


def render_speed_chart(points: Sequence[tuple[datetime, float, float]], *, tz: tzinfo, title: str) -> bytes:
    """График скорости интернета: (время, загрузка Мбит/с, отдача Мбит/с)."""
    if not points:
        raise NoDataError("нет замеров за выбранный период")

    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(9, 4.2), dpi=110, facecolor=_BG)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    xs = [p[0] for p in points]
    ax.set_facecolor(_PANEL)
    ax.plot(xs, [p[1] for p in points], color=NEON_CYAN, linewidth=1.8, marker="o", markersize=3, label="загрузка")
    ax.plot(xs, [p[2] for p in points], color=NEON_PURPLE, linewidth=1.8, marker="o", markersize=3, label="отдача")
    ax.set_ylabel("Мбит/с", color=_TEXT, fontsize=9)
    ax.set_ylim(bottom=0)
    ax.tick_params(colors=_TEXT, labelsize=8)
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.8)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m", tz=tz))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=4, maxticks=8))
    legend = ax.legend(facecolor=_PANEL, edgecolor=_GRID, fontsize=9)
    for text in legend.get_texts():
        text.set_color(_TEXT)
    fig.suptitle(title, color=_TEXT, fontsize=13, fontweight="bold", x=0.06, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=_BG)
    return buffer.getvalue()


def render_p2p_chart(points: Sequence[tuple[datetime, float, float]], *, tz: tzinfo, title: str, fiat: str = "") -> bytes:
    """График P2P-курса: (время, лучшая цена, типичная цена)."""
    if len(points) < 2:
        raise NoDataError("для графика нужно хотя бы две точки")

    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(9, 4.2), dpi=110, facecolor=_BG)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    xs = [p[0] for p in points]
    ax.set_facecolor(_PANEL)
    ax.plot(xs, [p[1] for p in points], color=NEON_AMBER, linewidth=1.2, alpha=0.8, label="лучшая")
    ax.plot(xs, [p[2] for p in points], color=NEON_CYAN, linewidth=2.0, label="типичная")
    ax.set_ylabel(fiat or "цена", color=_TEXT, fontsize=9)
    ax.tick_params(colors=_TEXT, labelsize=8)
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.8)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m", tz=tz))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=4, maxticks=8))
    legend = ax.legend(facecolor=_PANEL, edgecolor=_GRID, fontsize=9)
    for text in legend.get_texts():
        text.set_color(_TEXT)
    fig.suptitle(title, color=_TEXT, fontsize=13, fontweight="bold", x=0.06, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=_BG)
    return buffer.getvalue()


def render_price_chart(
    points: Sequence[tuple[datetime, float]],
    *,
    tz: tzinfo,
    title: str,
    currency: str = "",
    target: float | None = None,
    until: datetime | None = None,
) -> bytes:
    """График цены товара: ступенчатая линия (цена держится, пока не изменилась), минимум и цель."""
    if not points:
        raise NoDataError("цен пока нет")

    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if until is not None and until > xs[-1]:
        xs.append(until)  # цена держится до сих пор
        ys.append(ys[-1])
    fig = Figure(figsize=(9, 4.2), dpi=110, facecolor=_BG)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set_facecolor(_PANEL)
    ax.step(xs, ys, where="post", color=NEON_CYAN, linewidth=2.2)
    ax.scatter([p[0] for p in points], [p[1] for p in points], color=NEON_CYAN, s=26, zorder=3)
    low = min(points, key=lambda p: p[1])
    ax.scatter([low[0]], [low[1]], color=NEON_PINK, s=70, zorder=4, label=f"минимум {low[1]:,.0f}".replace(",", " "))
    if target:
        ax.axhline(target, color=NEON_AMBER, linewidth=1.4, linestyle="--", label=f"цель {target:,.0f}".replace(",", " "))
    lo, hi = min([*ys, target or min(ys)]), max([*ys, target or max(ys)])
    pad = max((hi - lo) * 0.15, hi * 0.01, 1)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_ylabel(currency or "цена", color=_TEXT, fontsize=9)
    ax.tick_params(colors=_TEXT, labelsize=8)
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.8)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m", tz=tz))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=3, maxticks=8))
    legend = ax.legend(facecolor=_PANEL, edgecolor=_GRID, fontsize=9)
    for text in legend.get_texts():
        text.set_color(_TEXT)
    fig.suptitle(title, color=_TEXT, fontsize=13, fontweight="bold", x=0.06, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=_BG)
    return buffer.getvalue()


def render_chart(rows: Sequence[MetricRow], *, tz: tzinfo, title: str, long_period: bool = False) -> bytes:
    """PNG-график: CPU, RAM, температура (если есть датчик), диск «/» (если есть)."""
    if not rows:
        raise NoDataError("нет данных за выбранный период")

    import matplotlib.dates as mdates  # импорт здесь: библиотека тяжёлая, а график нужен редко
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    data = downsample(rows)
    panels: list[tuple[str, str, object, str, tuple[float, float] | None]] = [
        ("CPU, %", NEON_CYAN, lambda r: r.cpu, "%", (0, 100)),
        ("RAM, %", NEON_PURPLE, lambda r: r.ram, "%", (0, 100)),
    ]
    if any(r.temp is not None for r in data):
        panels.append(("Температура, °C", NEON_PINK, lambda r: r.temp, "°C", None))
    if any(r.disk is not None for r in data):
        panels.append(("Диск /, %", NEON_AMBER, lambda r: r.disk, "%", (0, 100)))

    fig = Figure(figsize=(9, 2.1 * len(panels) + 0.9), dpi=110, facecolor=_BG)
    FigureCanvasAgg(fig)
    axes = fig.subplots(len(panels), 1, sharex=True)
    if len(panels) == 1:
        axes = [axes]

    fig.suptitle(title, color=_TEXT, fontsize=13, fontweight="bold", x=0.06, ha="left")
    formatter = mdates.DateFormatter("%d.%m" if long_period else "%H:%M", tz=tz)

    for ax, (label, color, getter, _unit, limits) in zip(axes, panels, strict=True):
        xs, ys = _with_gaps(data, tz, getter)
        ax.set_facecolor(_PANEL)
        ax.plot(xs, ys, color=color, linewidth=1.6)
        finite = [y for y in ys if not math.isnan(y)]
        if finite:
            ax.fill_between(xs, ys, min([*finite, limits[0] if limits else min(finite)]), color=color, alpha=0.18)
        if limits:
            ax.set_ylim(*limits)
        ax.set_ylabel(label, color=_TEXT, fontsize=9)
        ax.tick_params(colors=_TEXT, labelsize=8)
        ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.8)
        for spine in ax.spines.values():
            spine.set_color(_GRID)
        ax.xaxis.set_major_formatter(formatter)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=4, maxticks=8))

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=_BG)
    return buffer.getvalue()
