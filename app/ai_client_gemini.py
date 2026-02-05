# app/ai_client_gemini.py
import os
import logging
import json
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
        self.last_used_key = None  # Track qual chave foi usada
        logging.info(f"AI CLIENT: Inicializado com {len(keys)} chaves de API")

    def generate_text(self, prompt: str, **kwargs) -> tuple:
        """Generate text and return (text, tokens_info) tuple.
        tokens_info is a dict with 'prompt_tokens' and 'completion_tokens'.
        """
        backoff = self.backoff_base
        attempt = 0
        while True:
            attempt += 1
            slot = self.pool.next_ready()
            self.rl.wait()
            
            # Armazenar qual chave está sendo usada
            self.last_used_key = slot.key
            
            try:
                logging.info(f"IA TENTATIVA {attempt}: Chave ****{slot.key[-4:]} | Modelo {MODEL}")
                genai.configure(api_key=slot.key)
                m = genai.GenerativeModel(MODEL)
                resp = m.generate_content(prompt, **kwargs)
                logging.info(f"IA OK: Tentativa {attempt} sucesso com chave ****{slot.key[-4:]}")
                
                # Capturar informações de tokens
                tokens_info = {}
                if hasattr(resp, 'usage_metadata') and resp.usage_metadata:
                    tokens_info['prompt_tokens'] = getattr(resp.usage_metadata, 'prompt_token_count', 0)
                    tokens_info['completion_tokens'] = getattr(resp.usage_metadata, 'candidates_token_count', 0)
                    logging.info(f"TOKENS: Entrada={tokens_info.get('prompt_tokens', 0)} | Saída={tokens_info.get('completion_tokens', 0)}")
                
                return ((resp.text or "").strip(), tokens_info)

            except google_exceptions.ResourceExhausted as e:
                # This is the specific exception for 429 errors in the google-generativeai library
                logging.error(f"IA ERRO 429: Chave ****{slot.key[-4:]} quota excedida")
                self.pool.penalize(slot, retry_after=30) # Default 30s cooldown for 429
                backoff = min(backoff * 2, self.backoff_max)
                logging.warning(f"IA RETRY: Aguardando {backoff}s antes de tentar novamente...")
                continue
            
            except google_exceptions.GoogleAPICallError as e:
                 if e.code in [HTTPStatus.INTERNAL_SERVER_ERROR, HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE]:
                    logging.error(f"IA ERRO {e.code}: Chave ****{slot.key[-4:]}")
                    self.pool.penalize(slot, retry_after=10)
                    continue
                 logging.error(f"IA ERRO {e.code}: {e}")
                 raise RuntimeError(f"Erro {e.code}: {e}") from e

            except Exception as e:
                # Catch any other unexpected errors
                logging.error(f"IA ERRO: Excecao inesperada na chave ****{slot.key[-4:]}: {e}", exc_info=True)
                self.pool.penalize(slot, retry_after=60) # Longer cooldown for unknown errors
                continue
    
    def get_last_used_key(self) -> str:
        """Retorna as últimas 4 caracteres da chave usada (para logging)"""
        if self.last_used_key:
            return f"****{self.last_used_key[-4:]}"
        return "UNKNOWN"