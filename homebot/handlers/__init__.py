"""Роутеры бота.

Порядок подключения важен: aiogram отдаёт событие первому подошедшему обработчику.
Поэтому сначала идут «узкие» роутеры, затем `errors`, и в самом конце `fallback`,
который ловит всё остальное.
"""

from __future__ import annotations

from aiogram import Router


def build_routers() -> list[Router]:
    # Импорт внутри функции: модули роутеров тянут за собой весь UI-слой,
    # а пакет `homebot.handlers` должен импортироваться дёшево.
    from homebot.handlers import (
        access,
        apps,
        backup,
        charts,
        cleanup,
        common,
        doctor,
        errors,
        fallback,
        finance,
        guest,
        logs,
        monitoring,
        movies,
        network,
        news,
        ollama,
        p2p,
        power,
        quick,
        reminders,
        repair,
        security,
        settings,
        sites,
        tasks,
        tiles,
        tools,
        update,
        webapp,
    )

    admin_routers = [
        common.router,  # /start, /menu, /cancel, «⬅️ Меню», «🖥 Сервер»
        news.router,  # 🗞 новости
        cleanup.router,  # 🧹 чистка диска
        movies.router,  # 🎬 кино и сериалы (FSM: поиск, «своими словами»)
        settings.router,  # ⚙️ настройки (FSM: добавление ленты)
        tiles.router,  # 🧩 подключение приложений из чата (FSM: адрес и ключ)
        monitoring.router,  # состояние, live, топ процессов, службы
        charts.router,  # графики
        logs.router,  # журналы служб + разбор моделью
        network.router,  # Tailscale, IP, скорость
        security.router,  # SSH, обновления, SMART
        power.router,  # перезапуск служб, питание
        repair.router,  # 🛠 ремонт сервера по PIN (FSM: PIN)
        apps.router,  # Docker, Speedtest, qBittorrent, AdGuard, Jellyfin
        tasks.router,  # фоновые задачи
        backup.router,  # 💾 бэкап
        webapp.router,  # 🖥 /app
        update.router,  # ⬆️ обновление из чата (архив .tar.gz)
        doctor.router,  # 🩺 самопроверка
        reminders.router,  # напоминания и заметки (FSM)
        ollama.router,  # панель Ollama + FSM-режимы (чат, резюме)
        finance.router,  # 💰 финансы (Actual Budget); свободный текст «кофе 85» — строгий формат
        p2p.router,  # 💱 P2P-курс Binance (FSM: целевая цена)
        sites.router,  # 🔎 слежка за ценами и страницами (FSM: ссылка)
        quick.router,  # 🧲 magnet, ссылки на товары, 🎙 голосовые
        access.router,  # 👥 заявки и права людей
        fallback.router,  # всё, что не подошло выше
    ]
    # Двойная защита: даже если гость каким-то образом пройдёт мимо роутера гостей, админские обработчики его не примут.
    for router in admin_routers:
        if getattr(router, "_homebot_gated", False):  # build_routers зовут не раз, роутеры — одни и те же объекты
            continue
        router.message.filter(guest.not_guest)
        router.callback_query.filter(guest.not_guest)
        router._homebot_gated = True  # type: ignore[attr-defined]
    # Гости — первыми и со своим «запасным» обработчиком; errors — перед fallback, как раньше.
    # Инструменты — самыми первыми: роутер общий для админа и гостей с правом, остальным пропускает событие дальше
    return [tools.router, guest.router, *admin_routers[:-1], errors.router, admin_routers[-1]]
