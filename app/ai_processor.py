# app/ai_processor.py
import os
import json
import logging
import re
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

**1. OUTPUT OBRIGATÓRIO: JSON VÁLIDO**
DEVOLVE EXCLUSIVAMENTE JSON VÁLIDO.
ESTRUTURA FIXA DO TOPO:
{
  "resultados": [ /* 1 ou N objetos, um por artigo, na mesma ordem da entrada */ ]
}
NUNCA devolva texto fora do JSON. NUNCA inclua comentários. NUNCA mude os nomes de chaves.
Cada artigo DEVE conter: "titulo_final", "conteudo_final", "meta_description", "focus_keyphrase", "related_keyphrases", "slug", "categorias", "tags_sugeridas", "yoast_meta".

**2. VALIDAÇÃO DE TÍTULO (titulo_final)**
✅ OBRIGATÓRIO: Cada título DEVE passar no CHECKLIST:
  - Entre 55–65 caracteres
  - Começa com ENTIDADE (ator/franquia/plataforma)
  - Verbo NO PRESENTE (não infinitivo)
  - Afirmação direta, SEM pergunta
  - Concordância e regência perfeitas
  - Plataforma NO FIM se relevante
  - SEM sensacionalismo (bomba, explode, nerfado, morto, surpreendente)
  - Maiúsculas APENAS em nomes próprios
  - SEM múltiplos dois-pontos ou pontuação excessiva
SE ALGUM CRITÉRIO FALHAR, REFAZER O TÍTULO COMPLETAMENTE.

**3. VALIDAÇÃO DE CONTEÚDO (conteudo_final)**
  - SEM menção a concorrentes (Omelete, IGN, Jovem Nerd, AdoroCinema)
  - SEM hashtags (#)
  - SEM frases fracas ("veja", "entenda", "clique aqui", "saiba mais")
  - SEM termos em inglês (exceto nomes próprios internacionais)
  - SEM CTAs ou calls-to-action ("Thank you for reading", "Subscribe", etc.)
  - Links internos COM https:// completo: <a href="https://{domain}/tag/{tag}">Texto</a>
  - Parágrafos com máx 3–4 frases
  - MÍNIMO 3 subtítulos <h2>
  - Palavra-chave no primeiro parágrafo
  - Imagens em <figure> com <figcaption>

**4. META DESCRIPTION (meta_description)**
  - 140–155 caracteres
  - Contém palavra-chave principal
  - SEM call-to-action

**5. 🚨 REMOVER ABSOLUTAMENTE (CTAs/JUNK/GARBAGE)**
É PROIBIDO incluir no conteudo_final:
- "Thank you for reading" / "Thanks for reading" / "Thanks for visiting"
- "Don't forget to subscribe" / "Please subscribe" / "Subscribe now"
- "Click here" / "Read more" / "Sign up"
- "Stay tuned" / "Follow us" / "Keep up to date"
- Newsletter signup / Author boxes / Promotional content
SE ESSES TEXTOS APARECEREM NA FONTE, REMOVA-OS COMPLETAMENTE.

**6. LIMPEZA OBRIGATÓRIA**
NÃO incluir e REMOVER de forma explícita:
- Qualquer texto de interface/comentários dos sites (ex.: "Your comment has not been saved").
- Caixas/infobox de ficha técnica com rótulos como: "Release Date", "Runtime", "Director", "Writers", "Producers", "Cast".
- Elementos de comentários, "trending", "related", "read more", "newsletter", "author box", "ratings/review box".

[MODO PROCESSAMENTO EM LOTE]
- Recebe múltiplos artigos para processar
- Retorna um objeto JSON com um array "resultados" contendo a saída de cada artigo
- Cada resultado segue o mesmo formato de saída de artigo único
- Preserva a ordem dos artigos na resposta

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

    def rewrite_batch(
        self,
        batch_data: List[Dict[str, Any]]
    ) -> List[Tuple[Optional[Dict[str, Any]], Optional[str]]]:
        """Process multiple articles in a single batch."""
        prompt_template = self._load_prompt_template()
        
        batch_fields = []
        for data in batch_data:
            title = data.get('title')
            content_html = data.get('content_html')
            source_url = data.get('source_url')
            
            fonte = data.get("source_name", "")
            if not fonte and source_url:
                try:
                    fonte = urlparse(source_url).netloc.replace("www.", "")
                except Exception:
                    fonte = ""

            fields = {
                "titulo_original": title or "",
                "url_original": source_url or "",
                "content": content_html or "",
                "domain": data.get("domain", ""),
                "fonte_nome": fonte,
                "categoria": data.get("category", ""),
                "schema_original": json.dumps(data.get("schema_original"), indent=2, ensure_ascii=False) if data.get("schema_original") else "Nenhum",
                "videos_list": "\n".join([v.get("embed_url", "") for v in data.get("videos", []) if isinstance(v, dict) and v.get("embed_url")]) or "Nenhum",
                "imagens_list": "\n".join(data.get("images", [])) or "Nenhuma",
            }
            batch_fields.append(fields)

        batch_prompt = "Processe os seguintes artigos em lote:\n\n"
        for i, fields in enumerate(batch_fields, 1):
            batch_prompt += f"=== ARTIGO {i} ===\n"
            batch_prompt += self._safe_format_prompt(prompt_template, fields)
            batch_prompt += "\n\n"

        try:
            logger.info(f"Sending batch of {len(batch_data)} articles to AI.")

            # Be more deterministic for structured output
            generation_config = {
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "top_p": 0.9,
                "max_output_tokens": 12000,  # 12K para batch de 2 artigos completos
            }
            response_text = self._ai_client.generate_text(batch_prompt, generation_config=generation_config)

            parsed_data = self._parse_batch_response(response_text, len(batch_data))
            if parsed_data is None:  # Batch parsing failed
                logger.error(f"Batch JSON parse failed for {len(batch_data)} articles. NOT falling back to per-article to save quota.")
                logger.debug(f"Failed batch response (first 500 chars): {response_text[:500]}")
                # Return error for all items - they'll be retried in next cycle
                error_msg = "Batch JSON parsing failed (insufficient quota budget to retry individually)"
                return [(None, error_msg)] * len(batch_data)

            logger.info(f"Successfully processed batch of {len(batch_data)} articles.")
            return [(result, None) if result else (None, "Article data missing or invalid in batch response") 
                   for result in parsed_data]

        except RuntimeError as e:
            logger.critical(f"AI batch processing failed after all retries: {e}")
            return [(None, str(e))] * len(batch_data)
        except Exception as e:
            logger.critical(f"An unexpected error occurred during AI batch processing: {e}", exc_info=True)
            return [(None, str(e))] * len(batch_data)

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
            
            generation_config = {
                "response_mime_type": "application/json",
                "max_output_tokens": 12000,  # 12K para artigo individual completo
            }
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
    def _extract_json_block(text: str) -> str:
        """Tenta extrair um bloco JSON válido de um texto possivelmente misto/formatado."""
        # 0) Remover cercas de código Markdown ```json ... ``` ou ``` ... ```
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1)

        # 1) Se não, pegar do primeiro '{' ao último '}' (heurística ampla)
        # Isso funciona melhor para estruturas aninhadas
        first = text.find('{')
        last = text.rfind('}')
        if first != -1 and last != -1 and last > first:
            return text[first:last + 1]

        # 2) Como alternativa, tentar array
        first = text.find('[')
        last = text.rfind(']')
        if first != -1 and last != -1 and last > first:
            return text[first:last + 1]

        return text

    @staticmethod
    def _auto_fix_common_issues(s: str) -> str:
        """
        Clean up common JSON issues from AI responses.
        IMPORTANT: Must preserve the JSON structure!
        """
        # Remove BOM
        s = s.replace('\ufeff', '')
        
        # PASSO 1: Remove truly invalid control characters (not tab/newline/CR)
        def remove_truly_invalid_control_chars(text: str) -> str:
            """Remove invalid control characters while preserving valid JSON escapes"""
            result = []
            i = 0
            while i < len(text):
                char = text[i]
                code = ord(char)
                
                # If it's a backslash, check if it's escaping the next character
                if char == '\\' and i + 1 < len(text):
                    result.append(char)
                    i += 1
                    # Append the next character (it's being escaped)
                    result.append(text[i])
                    i += 1
                    continue
                
                # Valid whitespace that can appear in JSON
                if code in (9, 10, 13):  # tab, LF, CR
                    result.append(char)
                    i += 1
                    continue
                
                # Remove problematic control characters
                if code < 32 or code == 127 or (128 <= code <= 159):
                    i += 1
                    continue
                
                result.append(char)
                i += 1
            
            return ''.join(result)
        
        s = remove_truly_invalid_control_chars(s)
        
        # PASSO 2: Escapar newlines que apareçam dentro de strings JSON (que devem estar escapados)
        def escape_unescaped_newlines_in_strings(text: str) -> str:
            """Escape literal newlines that appear inside JSON strings"""
            result = []
            in_string = False
            escape_next = False
            i = 0
            
            while i < len(text):
                char = text[i]
                
                # Track if we're in an escape sequence
                if escape_next:
                    result.append(char)
                    escape_next = False
                    i += 1
                    continue
                
                # Check for escape character
                if char == '\\' and in_string:
                    result.append(char)
                    escape_next = True
                    i += 1
                    continue
                
                # Toggle string state on quotes
                if char == '"':
                    result.append(char)
                    in_string = not in_string
                    i += 1
                    continue
                
                # If we're inside a string and we hit a literal newline, escape it
                if in_string and char == '\n':
                    result.append('\\n')
                    i += 1
                    continue
                
                # If we're inside a string and we hit a carriage return, escape it
                if in_string and char == '\r':
                    result.append('\\r')
                    i += 1
                    continue
                
                result.append(char)
                i += 1
            
            return ''.join(result)
        
        s = escape_unescaped_newlines_in_strings(s)
        
        # PASSO 3: Limpar espaço após \\n e chaves
        s = re.sub(r'\\n\s+("[\w_]+"\s*:)', r'\\n\1', s)
        
        # PASSO 4: Remover comentários JSON
        # NOTA: Remover isso porque pode quebrar URLs como //www.example.com dentro de strings
        # s = re.sub(r"//.*?$", "", s, flags=re.MULTILINE)
        # s = re.sub(r"/\*[\s\S]*?\*/", "", s)
        
        # PASSO 5: Fixar estrutura JSON
        s = re.sub(r'\}\s*\{', r'}, {', s)
        s = re.sub(r',\s*([\]\}])', r'\1', s)
        s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.IGNORECASE)
        
        return s

    @staticmethod
    def _escape_unescaped_quotes_in_html(text: str) -> str:
        """Escape unescaped quotes that appear inside HTML content within JSON strings.
        
        This fixes issues like: <blockquote><p>He said "something"</p></blockquote>
        which should be: <blockquote><p>He said \"something\"</p></blockquote>
        """
        # Pattern to match "conteudo_final": "...HTML content..."
        # We use a regex to find the field and its value, then process the HTML content
        
        def escape_html_quotes(match):
            """Escape quotes inside the HTML content."""
            prefix = match.group(1)  # "conteudo_final": "
            html_content = match.group(2)  # The HTML content
            suffix = match.group(3)  # Final quote
            
            # Escape unescaped quotes in HTML content (but not those already escaped)
            # Be careful: we need to escape " that isn't already escaped with \
            result = []
            i = 0
            while i < len(html_content):
                if html_content[i] == '\\' and i + 1 < len(html_content):
                    # Already escaped character, keep both
                    result.append(html_content[i])
                    result.append(html_content[i + 1])
                    i += 2
                elif html_content[i] == '"':
                    # Unescaped quote, escape it
                    result.append('\\')
                    result.append('"')
                    i += 1
                else:
                    result.append(html_content[i])
                    i += 1
            
            return prefix + ''.join(result) + suffix
        
        # Match: "conteudo_final": "..." where ... is HTML content
        # This is tricky because the HTML can contain backslashes and quotes
        # So we use a non-greedy match and process carefully
        text = re.sub(
            r'("conteudo_final"\s*:\s*")([^"\\]*(?:(?:\\.|"(?:[^"\\]*\\.)*[^"\\]*)*[^"\\])*?)(")',
            escape_html_quotes,
            text,
            flags=re.DOTALL
        )
        
        return text
    
    @classmethod
    def _parse_and_normalize_ai_response(cls, raw_text: str) -> Dict[str, Any]:
        """Extracts, cleans, and normalizes the AI's JSON response."""
        # PRIMEIRO: Extrair o bloco JSON das cercas e ruído
        s = cls._extract_json_block(raw_text).strip()
        
        # PRÉ-PROCESSAMENTO: Escapar aspas não escapadas no conteúdo HTML
        s = cls._escape_unescaped_quotes_in_html(s)
        
        # SEGUNDO: Aplicar limpeza agressiva DEPOIS de extrair
        s = cls._auto_fix_common_issues(s)
        
        # TERCEIRO: Fazer uma limpeza adicional de caracteres de controle estendidos
        # Isso é importante antes de tentar o json.loads
        def final_control_char_cleanup(text: str) -> str:
            """Remove any remaining control characters"""
            result = []
            for char in text:
                code = ord(char)
                # Remove qualquer caractere de controle (< 32 ou == 127) exceto tab, newline, CR
                if code < 32 and code not in (9, 10, 13):
                    continue
                if code == 127:
                    continue
                if 128 <= code <= 159:  # Extended control characters
                    continue
                result.append(char)
            return ''.join(result)
        
        s = final_control_char_cleanup(s)
        
        try:
            # Try direct parsing first
            data = json.loads(s)
        except json.JSONDecodeError as e:
            logger.error(f"Initial JSON decoding failed: {e}. Trying with secondary fixes.")
            
            # More aggressive cleaning on second attempt
            s2 = cls._auto_fix_common_issues(s)
            s2 = final_control_char_cleanup(s2)
            s2 = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', s2)
            
            try:
                data = json.loads(s2)
            except json.JSONDecodeError as second_e:
                logger.error(f"Secondary JSON decoding failed: {second_e}. Trying aggressive escape.")
                
                # Last resort: Try to escape any remaining problematic sequences
                try:
                    s3 = s2.encode('utf-8', 'ignore').decode('utf-8')
                    data = json.loads(s3)
                except json.JSONDecodeError as final_e:
                    debug_dir = Path("debug")
                    debug_dir.mkdir(exist_ok=True)
                    timestamp = time.strftime("%Y%m%d-%H%M%S")
                    failed_path = debug_dir / f"failed_ai_{timestamp}.json.txt"
                    with open(failed_path, "w", encoding="utf-8") as f:
                        f.write(raw_text)
                    logger.critical(f"JSON decoding failed permanently. Raw response saved to {failed_path}")
                    raise ValueError("Failed to decode JSON from AI response after multiple attempts.") from final_e

        # Normalize the structure if the model returns a single object or a list
        if isinstance(data, dict) and "resultados" not in data:
            # If data looks like a single result, wrap it
            data = {"resultados": [data]}
        elif isinstance(data, list):
            data = {"resultados": data}

        if not isinstance(data, dict) or "resultados" not in data or not isinstance(data["resultados"], list):
            raise ValueError("AI batch response is missing 'resultados' list after normalization")
            
        return data

    @classmethod
    def _parse_response(cls, text: str) -> Optional[Dict[str, Any]]:
        """Parse the AI response for a single article using the robust parser."""
        try:
            data = cls._parse_and_normalize_ai_response(text)
            results = data.get("resultados")

            if not results:
                logger.error("AI response is empty after parsing.")
                return None
            
            result = results[0] # For single response, take the first item

            if not isinstance(result, dict):
                logger.error(f"AI response item is not a dictionary. Received type: {type(result)}")
                return None

            if "erro" in result:
                logger.warning(f"AI returned a rejection error: {result['erro']}")
                return result

            required_keys = [
                "titulo_final", "conteudo_final", "meta_description",
                "focus_keyphrase", "tags_sugeridas", "yoast_meta"
            ]
            missing_keys = [key for key in required_keys if key not in result]

            if missing_keys:
                logger.error(f"AI response is missing required keys: {', '.join(missing_keys)}")
                return None

            return result

        except (ValueError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing or normalizing AI response: {e}")
            logger.debug(f"Received text: {text[:500]}...")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing AI response: {e}")
            return None

    @classmethod
    def _parse_batch_response(cls, text: str, expected_count: int) -> Optional[List[Optional[Dict[str, Any]]]]:
        """Parse the AI response for a batch of articles using the robust parser."""
        try:
            data = cls._parse_and_normalize_ai_response(text)
            results = data["resultados"]

            if len(results) != expected_count:
                logger.error(f"AI batch response count mismatch. Expected {expected_count}, got {len(results)}")
                # We can still try to process what we got, but it's a partial failure.
                # For now, returning None as the original logic did.
                return None

            required_keys = [
                "titulo_final", "conteudo_final", "meta_description",
                "focus_keyphrase", "tags_sugeridas", "yoast_meta"
            ]

            valid_results: List[Optional[Dict[str, Any]]] = []
            for i, result in enumerate(results):
                if not isinstance(result, dict):
                    logger.error(f"AI batch result {i} is not a dictionary")
                    valid_results.append(None)
                    continue

                if "erro" in result:
                    logger.warning(f"AI returned a rejection error for batch item {i}: {result['erro']}")
                    valid_results.append(None)
                    continue

                missing_keys = [key for key in required_keys if key not in result]
                if missing_keys:
                    logger.error(f"AI batch result {i} is missing required keys: {', '.join(missing_keys)}")
                    valid_results.append(None)
                    continue

                valid_results.append(result)

            return valid_results

        except (ValueError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing or normalizing AI batch response: {e}")
            logger.debug(f"Received text: {text[:500]}...")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing AI batch response: {e}")
            return None