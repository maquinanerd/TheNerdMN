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

# ai_client.py
from limiter import RateLimiter, KeyPool
import logging

class AIClient:
    def __init__(self, keys, min_interval_s, backoff_base=20, backoff_max=300):
        self.pool = KeyPool(keys)
        self.rl = RateLimiter(min_interval_s)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

    def generate(self, payload):
        backoff = self.backoff_base
        while True:
            slot = self.pool.next_ready()
            self.rl.wait()
            resp = call_provider(payload, api_key=slot.key)  # sua função
            if resp.status == 200:
                return resp.data
            if resp.status == 429:
                ra = parse_retry_after(resp.headers)  # int|None
                logging.warning(f"429 na chave ****{slot.key[-4:]}; Retry-After={ra}")
                self.pool.penalize(slot, retry_after=ra)
                backoff = min(backoff*2, self.backoff_max)
                continue
            if 500 <= resp.status < 600:
                self.pool.penalize(slot, retry_after=10)
                continue
            raise RuntimeError(f"Erro {resp.status}: {resp.text}")

# queue_worker.py
from collections import deque
import time

class ArticleQueue:
    def __init__(self):
        self.q = deque()
    def push_many(self, items):
        self.q.extend(items)
    def pop(self):
        return self.q.popleft() if self.q else None
    def __len__(self): return len(self.q)

def run_cycle(ingest_feeds, q: ArticleQueue, max_per_feed_cycle, max_per_cycle):
    processed_total = 0
    for feed in ingest_feeds():
        new_items = feed.items  # já deduplicados
        allow = min(max_per_feed_cycle, max_per_cycle - processed_total)
        q.push_many(new_items[:allow])
        # o resto fica para o próximo ciclo
        processed_total += allow
        if processed_total >= max_per_cycle:
            break

def worker_loop(q, ai_client, article_sleep_s):
    while True:
        art = q.pop()
        if not art:
            time.sleep(2)
            continue
        process_article(art, ai_client)  # extrai -> IA -> mídia -> WP
        time.sleep(article_sleep_s)
