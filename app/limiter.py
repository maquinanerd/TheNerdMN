# limiter.py
import time, random
from collections import deque
from time import monotonic

class RateLimiter:
    def __init__(self, min_interval_s=6.0):
        self.min_interval_s = float(min_interval_s)
        self.last_call = 0.0
    def wait(self):
        wait = self.min_interval_s - (monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.last_call = monotonic()

class KeySlot:
    def __init__(self, key): 
        self.key = key
        self.cooldown_until = 0.0

class KeyPool:
    def __init__(self, keys):
        self.slots = deque(KeySlot(k) for k in keys)
    def next_ready(self):
        now = monotonic()
        for _ in range(len(self.slots)):
            s = self.slots[0]
            if s.cooldown_until <= now:
                return s
            self.slots.rotate(-1)
        wait = min(s.cooldown_until for s in self.slots) - now
        if wait > 0: time.sleep(wait)
        return self.next_ready()
    def penalize(self, slot, retry_after=None, base=30):
        dur = (retry_after or base) + random.uniform(0, 2)
        slot.cooldown_until = monotonic() + dur
        self.slots.rotate(-1)