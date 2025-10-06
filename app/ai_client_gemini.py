# app/ai_client_gemini.py
import os
import logging
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from http import HTTPStatus

from .limiter import RateLimiter, KeyPool

MODEL = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash-lite")

def parse_retry_after(headers):
    """Parses Retry-After header, returns seconds as int or None."""
    if 'retry-after' in headers:
        try:
            return int(headers['retry-after'])
        except (ValueError, TypeError):
            return None
    return None

class AIClient:
    def __init__(self, keys, min_interval_s, backoff_base=20, backoff_max=300):
        self.pool = KeyPool(keys)
        self.rl = RateLimiter(min_interval_s)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

    def generate_text(self, prompt: str, **kwargs) -> str:
        backoff = self.backoff_base
        while True:
            slot = self.pool.next_ready()
            self.rl.wait()
            
            try:
                genai.configure(api_key=slot.key)
                m = genai.GenerativeModel(MODEL)
                resp = m.generate_content(prompt, **kwargs)
                return (resp.text or "").strip()

            except google_exceptions.ResourceExhausted as e:
                # This is the specific exception for 429 errors in the google-generativeai library
                logging.warning(f"429 na chave ****{slot.key[-4:]}; {e}")
                self.pool.penalize(slot, retry_after=30) # Default 30s cooldown for 429
                backoff = min(backoff * 2, self.backoff_max)
                continue
            
            except google_exceptions.GoogleAPICallError as e:
                 if e.code in [HTTPStatus.INTERNAL_SERVER_ERROR, HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE]:
                    logging.warning(f"5xx na chave ****{slot.key[-4:]}; {e}")
                    self.pool.penalize(slot, retry_after=10)
                    continue
                 raise RuntimeError(f"Erro {e.code}: {e.message}") from e

            except Exception as e:
                # Catch any other unexpected errors
                logging.error(f"Erro inesperado com a chave ****{slot.key[-4:]}: {e}", exc_info=True)
                self.pool.penalize(slot, retry_after=60) # Longer cooldown for unknown errors
                continue