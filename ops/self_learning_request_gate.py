"""Bounded shared background model gate. No credentials or message bodies stored."""
import asyncio
import time
import weakref
from contextlib import asynccontextmanager

class BackgroundGate:
    def __init__(self, capacity=32, wait_timeout=180, foreground_grace=10, cooldown=30):
        self.capacity, self.wait_timeout = capacity, wait_timeout
        self.foreground_grace, self.cooldown = foreground_grace, cooldown
        self.pending = 0
        self.lock = asyncio.Lock()
        self.quiet_until = 0.0

    def foreground(self):
        self.quiet_until = max(self.quiet_until, time.monotonic() + self.foreground_grace)

    @asynccontextmanager
    async def slot(self):
        if self.pending >= self.capacity:
            raise RuntimeError('background model queue full')
        self.pending += 1
        acquired = False
        deadline = time.monotonic() + self.wait_timeout
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=self.wait_timeout)
            acquired = True
            while self.quiet_until > time.monotonic():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('background model queue deadline exceeded')
                await asyncio.sleep(min(self.quiet_until-time.monotonic(), remaining))
            yield
        except Exception as exc:
            if getattr(exc, 'status_code', None) == 429 or 'gateway_concurrency_limit' in str(exc):
                self.quiet_until = max(self.quiet_until,time.monotonic()+self.cooldown)
            raise
        finally:
            if acquired:
                self.lock.release()
            self.pending -= 1

_gates = weakref.WeakKeyDictionary()
def gate():
    loop=asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop]=BackgroundGate()
    return _gates[loop]

def note_foreground_request():
    gate().foreground()

async def queued_text_chat(provider, **kwargs):
    async with gate().slot():
        return await provider.text_chat(**kwargs)
