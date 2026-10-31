"""Пример внешнего модуля UNI (API 1.0): следит, открываются ли сайты."""

import json

import aiohttp


def setup(uni):
    store = uni.data_dir / "sites.json"
    down = set()

    def sites():
        try:
            return json.loads(store.read_text())
        except (OSError, ValueError):
            return []

    async def check(url):
        try:
            async with uni.http.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                return resp.status < 500
        except Exception:
            return False

    async def command(args):
        parts = args.split()
        if len(parts) == 2 and parts[0] == "add" and parts[1].startswith(("http://", "https://")):
            store.write_text(json.dumps([*sites(), parts[1]]))
            return "Добавил " + parts[1]
        if len(parts) == 2 and parts[0] == "del":
            store.write_text(json.dumps([s for s in sites() if s != parts[1]]))
            return "Убрал " + parts[1]
        if not sites():
            return "Сайтов нет. Добавить: <code>/uptime add https://example.com</code>"
        lines = [("🟢 " if await check(s) else "🔴 ") + s for s in sites()]
        return "\n".join(lines)

    async def job():
        changes = []
        for s in sites():
            ok = await check(s)
            if not ok and s not in down:
                down.add(s)
                changes.append("🔴 не открывается: " + s)
            elif ok and s in down:
                down.discard(s)
                changes.append("🟢 снова работает: " + s)
        return "\n".join(changes) or None

    uni.command("uptime", command)
    uni.every(300, job)
