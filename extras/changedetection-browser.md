# Браузерный режим для changedetection.io

OLX, Rozetka и подобные сайты отвечают 403 на обычные запросы. Нужен настоящий Chrome в отдельном контейнере,
к которому changedetection подключается по websocket (Playwright).

1. Запустите браузер в той же docker-сети, что и changedetection:

   ```
   docker run -d --name browser-sockpuppet-chrome --restart unless-stopped \
     --cap-add SYS_ADMIN --shm-size=1g \
     -e SCREEN_WIDTH=1920 -e SCREEN_HEIGHT=1080 -e SCREEN_DEPTH=16 \
     -e MAX_CONCURRENT_CHROME_PROCESSES=3 \
     dgtlmoon/sockpuppetbrowser:latest
   ```

2. Добавьте в контейнер changedetection переменную `PLAYWRIGHT_DRIVER_URL=ws://browser-sockpuppet-chrome:3000`
   и пересоздайте его (`docker rm -f changedetection`, затем тот же `docker run` с новой переменной; данные лежат в томе).
   Если контейнеры в разных сетях: `docker network connect <сеть_changedetection> browser-sockpuppet-chrome`.
3. В карточке слежки в боте нажмите «🌐 Браузер». Или `SITES_BROWSER=true` в `.env` — тогда все новые слежки сразу через браузер.

Chrome заметно тяжелее обычных запросов: оставляйте проверки раз в час и реже. Если сайт всё равно не пускает —
он особенно строгий; на такие страницы автоматические проверки могут не проходить.
