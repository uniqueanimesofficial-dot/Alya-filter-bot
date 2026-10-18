import asyncio
import logging
import os

from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger("keepalive")


async def _health(_request):
    return web.Response(text="Filter bot is running")


async def start_web():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health server on port %s", port)
    return runner


async def ping_forever(interval=600):
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if not url:
        log.info("RENDER_EXTERNAL_URL not set - self-ping disabled")
        return
    async with ClientSession(timeout=ClientTimeout(total=20)) as session:
        while True:
            await asyncio.sleep(interval)
            try:
                async with session.get(url):
                    pass
            except Exception as e:
                log.warning("self-ping failed: %s", e)