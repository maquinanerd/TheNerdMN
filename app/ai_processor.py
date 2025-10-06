# app/ai_processor.py
import os
import json
import logging
from urllib.parse import urlparse
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, ClassVar

from .config import (
    AI_API_KEYS,
    PROMPT_FILE_PATH,
)
from .exceptions import AIProcessorError
from .ai_client_gemini import AIClient

# Get rate limit configs from environment or use defaults from the user's plan
AI_MIN_INTERVAL_S = float(os.getenv('AI_MIN_INTERVAL_S', 6))
BACKOFF_BASE_S = int(os.getenv('BACKOFF_BASE_S', 20))
BACKOFF_MAX_S = int(os.getenv('BACKOFF_MAX_S', 300))

logger = logging.getLogger(__name__)

AI_SYSTEM_RULES = """
[REGRAS OBRIGATÓRIAS — CUMPRIR 100%]

NÃO incluir e REMOVER de forma explícita:
- Qualquer texto de interface/comentários dos sites (ex.: "Your comment has not been saved").
- Caixas/infobox de ficha técnica com rótulos como: "Release Date", "Runtime", "Director", "Writers", "Producers", "Cast".
- Elementos de comentários, “trending”, “related”, “read more”, “newsletter”, “author box”, “ratings/review box".

Somente produzir o conteúdo jornalístico reescrito do artigo principal.
Se algum desses itens aparecer no texto de origem, exclua-os do resultado.
"""

class AIProcessor:
    _prompt_template: ClassVar[Optional[str]] = None
    _ai_client: ClassVar[Optional[AIClient]] = None

    def __init__(self):
        if not AI_API_KEYS:
            raise AIProcessorError("No GEMINI_ API keys found in the environment.")
        
        # Initialize the AI client singleton
        if AIProcessor._ai_client is None:
            logger.info(f"Initializing AIClient with {len(AI_API_KEYS)} keys.")
            AIProcessor._ai_client = AIClient(
                keys=AI_API_KEYS,
                min_interval_s=AI_MIN_INTERVAL_S,
                backoff_base=BACKOFF_BASE_S,
                backoff_max=BACKOFF_MAX_S,
            )

    @classmethod
    def _load_prompt_template(cls) -> str:
        if cls._prompt_template is None:
            try:
                prompt_path = Path(PROMPT_FILE_PATH)
                if not prompt_path.exists():
                    raise FileNotFoundError(f"Prompt file not found at {PROMPT_FILE_PATH}")
                
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    base_template = f.read()
                cls._prompt_template = f"{AI_SYSTEM_RULES}\n\n{base_template}"
            except FileNotFoundError as e:
                logger.critical(e)
                raise AIProcessorError("Prompt template file not found.") from e
        return cls._prompt_template

    @staticmethod
    def _safe_format_prompt(template: str, fields: Dict[str, Any]) -> str:
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return ""
        
        s = template.replace('{', '{{').replace('}', '}}')
        for key in fields:
            s = s.replace('{{' + key + '}}', '{' + key + '}')
        
        return s.format_map(_SafeDict(fields))

    def rewrite_content(
        self,
        title: Optional[str] = None,
        content_html: Optional[str] = None,
        source_url: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        
        prompt_template = self._load_prompt_template()

        fonte = kwargs.get("source_name", "")
        if not fonte and source_url:
            try:
                fonte = urlparse(source_url).netloc.replace("www.", "")
            except Exception:
                fonte = ""

        fields = {
            "titulo_original": title or "",
            "url_original": source_url or "",
            "content": content_html or "",
            "domain": kwargs.get("domain", ""),
            "fonte_nome": fonte,
            "categoria": kwargs.get("category", ""),
            "schema_original": json.dumps(kwargs.get("schema_original"), indent=2, ensure_ascii=False) if kwargs.get("schema_original") else "Nenhum",
            "videos_list": "\n".join([v.get("embed_url", "") for v in kwargs.get("videos", []) if isinstance(v, dict) and v.get("embed_url")]) or "Nenhum",
            "imagens_list": "\n".join(kwargs.get("images", [])) or "Nenhuma",
        }
        prompt = self._safe_format_prompt(prompt_template, fields)

        try:
            logger.info(f"Sending content from {source_url} to AI.")
            
            generation_config = {"response_mime_type": "application/json"}
            response_text = self._ai_client.generate_text(prompt, generation_config=generation_config)
            
            parsed_data = self._parse_response(response_text)

            if not parsed_data:
                return None, "Failed to parse or validate AI response."

            if "erro" in parsed_data:
                logger.warning(f"AI returned a handled error: {parsed_data['erro']}")
                return None, parsed_data["erro"]

            logger.info(f"Successfully processed content from {source_url}.")
            return parsed_data, None

        except RuntimeError as e:
            logger.critical(f"AI processing failed for {source_url} after all retries: {e}")
            return None, str(e)
        except Exception as e:
            logger.critical(f"An unexpected error occurred during AI processing for {source_url}: {e}", exc_info=True)
            return None, str(e)

    @staticmethod
    def _parse_response(text: str) -> Optional[Dict[str, Any]]:
        try:
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:-3].strip()
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:-3].strip()

            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            with open(debug_dir / f"ai_response_{timestamp}.json", "w", encoding="utf-8") as f:
                f.write(clean_text)

            data = json.loads(clean_text)

            if not isinstance(data, dict):
                logger.error(f"AI response is not a dictionary. Received type: {type(data)}")
                return None

            if "erro" in data:
                logger.warning(f"AI returned a rejection error: {data['erro']}")
                return data

            required_keys = [
                "titulo_final", "conteudo_final", "meta_description",
                "focus_keyphrase", "tags_sugeridas", "yoast_meta"
            ]
            missing_keys = [key for key in required_keys if key not in data]

            if missing_keys:
                logger.error(f"AI response is missing required keys: {', '.join(missing_keys)}")
                return None

            return data

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from AI response: {e}")
            logger.debug(f"Received text: {text[:500]}...")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing AI response: {e}")
            return None