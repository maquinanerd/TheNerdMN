# app/pipeline.py
import logging
import time
import json
import re
import os
import threading
from urllib.parse import urlparse
from typing import Dict, Any, Optional, List

from .config import (
    PIPELINE_ORDER,
    RSS_FEEDS,
    WORDPRESS_CONFIG,
    WORDPRESS_CATEGORIES,
    CATEGORY_ALIASES,
    PIPELINE_CONFIG,
    SOURCE_CATEGORY_MAP,
)
from .store import Database
from .feeds import FeedReader
from .extractor import ContentExtractor
from .ai_processor import AIProcessor
from .wordpress import WordPressClient
from .seo_title_optimizer import optimize_title
from .title_validator import TitleValidator
from .html_utils import (
    unescape_html_content,
    validate_and_fix_figures,
    merge_images_into_content,
    rewrite_img_srcs_with_wp,
    strip_credits_and_normalize_youtube,
    remove_broken_image_placeholders,
    strip_naked_internal_links,
    remove_source_domain_schemas,
    strip_forbidden_cta_sentences,
    detect_forbidden_cta,
)
from .internal_linking import add_internal_links
from .task_queue import ArticleQueue
from bs4 import BeautifulSoup
from .cleaners import clean_html_for_globo_esporte

logger = logging.getLogger(__name__)

# --- Global instances ---
article_queue = ArticleQueue()
ai_processor = AIProcessor()

# --- Environment variables for pipeline control ---
MAX_PER_FEED_CYCLE = int(os.getenv('MAX_PER_FEED_CYCLE', 3))
MAX_PER_CYCLE = int(os.getenv('MAX_PER_CYCLE', 10))
ARTICLE_SLEEP_S = int(os.getenv('ARTICLE_SLEEP_S', 120))  # 2 minutos entre ciclos
BETWEEN_BATCH_DELAY_S = int(os.getenv('BETWEEN_BATCH_DELAY_S', 30))  # 30s entre batches (rápido)
BETWEEN_PUBLISH_DELAY_S = int(os.getenv('BETWEEN_PUBLISH_DELAY_S', 30))  # 30s entre publicações

CLEANER_FUNCTIONS = {
    'globo.com': clean_html_for_globo_esporte,
}

def _get_article_url(article_data: Dict[str, Any]) -> Optional[str]:
    """Get article URL from various possible fields."""
    url = article_data.get("url") or article_data.get("link") or article_data.get("id")
    if not url:
        return None
    try:
        p = urlparse(url)
        if p.scheme in ("http", "https"):
            return url
    except Exception:
        return None
    return None

BAD_HOSTS = {"sb.scorecardresearch.com", "securepubads.g.doubleclick.net"}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

def is_valid_upload_candidate(url: str) -> bool:
    """Check if a URL points to a valid image for upload."""
    if not url:
        return False
    try:
        lower_url = url.lower()
        p = urlparse(lower_url)
        
        if not p.scheme.startswith("http"):
            return False
        if p.netloc in BAD_HOSTS:
            return False
        if not p.path.endswith(IMG_EXTS):
            return False
        
        if "author" in lower_url or "avatar" in lower_url:
            return False
            
        dims = re.findall(r'[?&](?:w|width|h|height)=(\d+)', lower_url)
        if any(int(d) <= 100 for d in dims):
            return False
            
        return True
    except Exception:
        return False

def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
    """Process a batch of articles."""
    if not articles:
        return

    db = Database()
    extractor = ContentExtractor()
    wp_client = WordPressClient(config=WORDPRESS_CONFIG, categories_map=WORDPRESS_CATEGORIES)

    try:
        # Extract content for all articles first
        extracted_articles = []
        for article_data in articles:
            article_db_id = article_data['db_id']
            source_id = article_data['source_id']
            feed_config = RSS_FEEDS.get(source_id, {})
            category = feed_config.get('category', 'Notícias')

            try:
                article_url = _get_article_url(article_data)
                if not article_url:
                    logger.warning(f"Skipping article {article_data.get('id')} - missing/invalid URL")
                    db.update_article_status(article_db_id, 'FAILED', reason="Missing/invalid URL")
                    continue

                logger.info(f"Processing article: {article_data.get('title', 'N/A')} (DB ID: {article_db_id}) from {source_id}")
                db.update_article_status(article_db_id, 'PROCESSING')

                # Extract content
                html_content = extractor._fetch_html(article_url)
                if not html_content:
                    db.update_article_status(article_db_id, 'FAILED', reason="Failed to fetch HTML")
                    continue

                # Apply cleaners if needed
                soup = BeautifulSoup(html_content, 'lxml')
                domain = urlparse(article_url).netloc.lower()
                for cleaner_domain, cleaner_func in CLEANER_FUNCTIONS.items():
                    if cleaner_domain in domain:
                        soup = cleaner_func(soup)
                        logger.info(f"Applied cleaner for {cleaner_domain}")
                        break

                # Extract data
                extracted_data = extractor.extract(str(soup), url=article_url)
                if not extracted_data or not extracted_data.get('content'):
                    logger.warning(f"Failed to extract content from {article_url}")
                    db.update_article_status(article_db_id, 'FAILED', reason="Extraction failed")
                    continue

                # Add to batch
                extracted_articles.append({
                    'db_id': article_db_id,
                    'url': article_url,
                    'source_id': source_id,
                    'category': category,
                    'extracted': extracted_data,
                    'feed_config': feed_config,
                    'title': article_data.get('title', '')
                })

            except Exception as e:
                logger.error(f"Error extracting article {article_data.get('title', 'N/A')}: {e}", exc_info=True)
                db.update_article_status(article_db_id, 'FAILED', reason=str(e))

        # Process all extracted articles in batches via AI
        batch_count = 0
        for batch in [extracted_articles[i:i+2] for i in range(0, len(extracted_articles), 2)]:
            # Aguardar entre batches para garantir qualidade SEO
            if batch_count > 0:
                logger.info(f"Aguardando {BETWEEN_BATCH_DELAY_S}s entre batches (garantindo processamento de qualidade)...")
                time.sleep(BETWEEN_BATCH_DELAY_S)
            
            batch_data = []
            for art in batch:
                extracted = art['extracted']
                main_text = extracted.get('content', '')
                body_images_html = extracted.get('images', [])
                content_for_ai = main_text + "\n".join(body_images_html)

                batch_data.append({
                    'title': extracted.get('title'),
                    'content_html': content_for_ai,
                    'source_url': art['url'],
                    'category': art['category'],
                    'videos': extracted.get('videos', []),
                    'images': extracted.get('images', []),
                    'source_name': art['feed_config'].get('source_name', ''),
                    'domain': wp_client.get_domain(),
                    'schema_original': extracted.get('schema_original')
                })

            try:
                # Process all articles in batch with one API call
                batch_results = ai_processor.rewrite_batch(batch_data)
                batch_count += 1

                # Process results in same order
                for art_data, (rewritten_data, failure_reason) in zip(batch, batch_results):
                    try:
                        if not rewritten_data:
                            reason = failure_reason or "AI processing failed"
                            logger.warning(f"Article '{art_data['title']}' - Marking as QUEUED for retry (Reason: {reason})")
                            # Mark as QUEUED instead of FAILED so it retries in next cycle (quota-friendly)
                            db.update_article_status(art_data['db_id'], 'QUEUED', reason=reason)
                            continue

                        # Process content
                        raw_content_html = rewritten_data.get("conteudo_final", "").strip()
                        title = rewritten_data.get("titulo_final", "").strip()

                        if not title or not raw_content_html:
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="AI output missing required fields")
                            continue

                        cta_removal_log = []

                        raw_cta_match = detect_forbidden_cta(raw_content_html)
                        if raw_cta_match:
                            logger.error(f"🚨 CTA detectado na resposta bruta da IA: {raw_cta_match}")
                            cta_removal_log.append(f"RAW: {raw_cta_match}")

                        content_html, preclean_removed = strip_forbidden_cta_sentences(raw_content_html)
                        if preclean_removed:
                            logger.info("✅ CTA removido no pré-processamento (strip_forbidden_cta_sentences)")
                            cta_removal_log.append("PRE: strip_forbidden_cta_sentences")
                        else:
                            content_html = raw_content_html

                        # 🚨 NUCLEAR LIMPEZA: Remover "Thank you for reading..." DEFINITIVAMENTE
                        original_html = raw_content_html
                        
                        # CAMADA 1: Remover a frase EXATA (literal search)
                        nuclear_phrases = [
                            "Thank you for reading this post, don't forget to subscribe!",
                            "thank you for reading this post, don't forget to subscribe!",
                            "Thank you for reading this post, don't forget to subscribe",
                            "thank you for reading this post, don't forget to subscribe",
                        ]
                        
                        for phrase in nuclear_phrases:
                            if phrase in content_html:
                                logger.error(f"🔥 LAYER 1 (LITERAL): CTA encontrado: '{phrase[:50]}...'")
                                cta_removal_log.append(f"LITERAL: {phrase[:60]}")
                                content_html = content_html.replace(phrase, "")
                                logger.info("✅ Removido com sucesso")

                        # CAMADA 1.5: Remover variações com formatação inline ou apóstrofos diferentes
                        cta_sentence_patterns = [
                            r"(?is)(?:<p[^>]*>\s*)?(?:<[^>]+>\s*)*thank\s+you\s+for\s+reading(?:\s|&nbsp;|<[^>]+>)*?(?:this\s+post)?(?:\s|&nbsp;|<[^>]+>)*?don['’]t\s+forget\s+to\s+subscribe(?:\s|&nbsp;|<[^>]+>)*?(?:</p>)?",
                            r"(?is)(?:<p[^>]*>\s*)?(?:<[^>]+>\s*)*thanks\s+for\s+reading(?:\s|&nbsp;|<[^>]+>)*?(?:this\s+post)?(?:\s|&nbsp;|<[^>]+>)*?don['’]t\s+forget\s+to\s+subscribe(?:\s|&nbsp;|<[^>]+>)*?(?:</p>)?",
                            r"(?is)thank\s+you\s+for\s+reading[^<\n\r]*don['’]t\s+forget\s+to\s+subscribe[^<\n\r]*",
                        ]

                        for pattern in cta_sentence_patterns:
                            content_html, removed_count = re.subn(pattern, '', content_html)
                            if removed_count:
                                logger.error("🔥 LAYER 1.5 (SENTENCE REGEX): CTA removido via regex flexível")
                                cta_removal_log.append(f"SENTENCE_PATTERN: {pattern[:40]}...")

                        # CAMADA 2: Remover parágrafos INTEIROS que contêm padrões de CTA
                        cta_patterns = [
                            r'<p[^>]*>.*?thank you for reading this post.*?don\'t forget to subscribe.*?</p>',
                            r'<p[^>]*>.*?thank you for reading.*?don\'t forget.*?</p>',
                            r'<p[^>]*>.*?thank you for reading.*?</p>',
                            r'<p[^>]*>.*?thanks for reading.*?</p>',
                            r'<p[^>]*>.*?thanks for visiting.*?</p>',
                            r'<p[^>]*>.*?don\'t forget to subscribe.*?</p>',
                            r'<p[^>]*>.*?subscribe now.*?</p>',
                            r'<p[^>]*>.*?please subscribe.*?</p>',
                            r'<p[^>]*>.*?subscribe to our.*?</p>',
                            r'<p[^>]*>.*?stay tuned.*?</p>',
                            r'<p[^>]*>.*?follow us.*?</p>',
                            r'<p[^>]*>.*?if you enjoyed.*?</p>',
                            r'<p[^>]*>.*?found this helpful.*?</p>',
                            r'<p[^>]*>.*?click here.*?</p>',
                            r'<p[^>]*>.*?read more.*?</p>',
                            r'<p[^>]*>.*?sign up.*?</p>',
                            r'<p[^>]*>.*?obrigado por ler.*?</p>',
                            r'<p[^>]*>.*?obrigada por ler.*?</p>',
                            r'<p[^>]*>.*?não esqueça de se inscrever.*?</p>',
                            r'<p[^>]*>.*?se inscreva.*?</p>',
                            r'<p[^>]*>.*?clique aqui.*?</p>',
                            r'<p[^>]*>.*?leia mais.*?</p>',
                            r'<p[^>]*>.*?cadastre-se.*?</p>',
                            r'<p[^>]*>.*?fique atento.*?</p>',
                            r'<p[^>]*>.*?nos siga.*?</p>',
                            r'<p[^>]*>.*?mantenha-se atualizado.*?</p>',
                            r'<p[^>]*>.*?este artigo foi.*?</p>',
                            r'<p[^>]*>.*?se você gostou.*?</p>',
                        ]
                        
                        for pattern in cta_patterns:
                            original_length = len(content_html)
                            matches = re.findall(pattern, content_html, flags=re.IGNORECASE | re.DOTALL)
                            if matches:
                                logger.error(f"🔥 LAYER 2 (REGEX): Encontrado(s) {len(matches)} parágrafo(s) com CTA")
                                for match in matches[:2]:  # Log dos 2 primeiros matches
                                    cta_removal_log.append(f"REGEX: {match[:80]}")
                            content_html = re.sub(pattern, '', content_html, flags=re.IGNORECASE | re.DOTALL)
                            if len(content_html) < original_length:
                                logger.info(f"✅ Parágrafo(s) removido(s) via regex")
                        
                        # CAMADA 3: Remover tags vazias deixadas para trás
                        content_html = re.sub(r'<(p|div|span|article)[^>]*>\s*</\1>', '', content_html, flags=re.IGNORECASE)
                        content_html = re.sub(r'<p[^>]*>\s*<br[^>]*>\s*</p>', '', content_html, flags=re.IGNORECASE)
                        
                        # CAMADA 4: Verificação FINAL - se ainda houver "thank you", REJEITA
                        if 'thank you for reading' in content_html.lower():
                            logger.error("❌❌❌ CRÍTICO: CTA ainda presente após limpeza! REJEITANDO ARTIGO!")
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="CTA persisted after cleaning - CRITICAL FAILURE")
                            continue
                        
                        # ✅ Logar se houve remoção significativa, mas prosseguir para publicação
                        if len(content_html) < len(original_html):
                            chars_removed = len(original_html) - len(content_html)
                            logger.warning(f"⚠️ CTA removido durante a limpeza ({chars_removed} chars). Prosseguindo com publicação.")
                        
                        # ✅ VALIDAR TÍTULO CONFORME REGRAS EDITORIAIS
                        title_validator = TitleValidator()
                        validation_result = title_validator.validate(title)
                        
                        if validation_result['status'] == 'ERRO':
                            errors = validation_result.get('erros', [])
                            errs_joined = ' '.join(errors).lower()

                            def shorten_title(t: str, max_len: int = 65) -> str:
                                """Encurta o título de forma limpa tentando preservar sentido.
                                1) Tenta cortar em separadores (":", "-", "–", "—")
                                2) Se ainda estiver longo, corta sem quebrar palavras e adiciona reticências
                                """
                                import re
                                if len(t) <= max_len:
                                    return t
                                parts = re.split(r'[:\-–—]\s*', t)
                                for p in parts:
                                    if len(p) <= max_len:
                                        return p.strip()
                                trimmed = t[:max_len].rsplit(' ', 1)[0]
                                if not trimmed:
                                    trimmed = t[:max_len]
                                return trimmed.strip() + '…'

                            if 'muito longo' in errs_joined:
                                corrected_title = shorten_title(title, max_len=65)
                                if corrected_title != title:
                                    logger.warning(f"⚠️ Título muito longo detectado. Auto-encurtando: '{corrected_title}'")
                                    title = corrected_title
                                    validation_result = title_validator.validate(title)
                                else:
                                    logger.warning(f"⚠️ Título muito longo e não foi possível encurtar de forma segura: {title}. Prosseguindo assim mesmo.")
                            elif 'muito curto' in errs_joined:
                                suggested = title_validator.suggest_correction(title)
                                if suggested and len(suggested) > len(title):
                                    logger.info(f"⚠️ Título muito curto detectado. Aplicando sugestão: '{suggested}'")
                                    title = suggested
                                    validation_result = title_validator.validate(title)
                                else:
                                    logger.warning(f"⚠️ Título muito curto sem sugestão útil. Prosseguindo assim mesmo: {title}")

                            if validation_result['status'] == 'ERRO':
                                logger.warning(f"⚠️ Prosseguindo com título mesmo com erros editoriais: {title}")
                                for error in validation_result.get('erros', []):
                                    logger.warning(f"   {error}")
                                validation_result.setdefault('avisos', [])
                                validation_result['avisos'].extend(validation_result.get('erros', []))
                                validation_result['erros'] = []
                                validation_result['status'] = 'AVISO'
                        
                        if validation_result['status'] == 'AVISO':
                            logger.warning(f"⚠️ Título com avisos editoriais: {title}")
                            for warning in validation_result['avisos']:
                                logger.warning(f"   {warning}")
                            # Tentar corrigir automaticamente
                            corrected_title = title_validator.suggest_correction(title)
                            if corrected_title != title:
                                logger.info(f"✅ Título corrigido: {corrected_title}")
                                title = corrected_title
                        
                        # Otimizar título para Google News & Discovery
                        title, title_optimization_report = optimize_title(title, content_html)
                        logger.info(f"Título otimizado: {title_optimization_report['original_score']:.1f} → {title_optimization_report['optimized_score']:.1f}")
                        
                        # IMPORTANTE: Desescapar HTML que pode ter vindo escapado da IA
                        content_html = unescape_html_content(content_html)
                        
                        # Validar e corrigir estruturas de figura
                        content_html = validate_and_fix_figures(content_html)
                        
                        # Process images
                        extracted = art_data['extracted']
                        content_html = remove_broken_image_placeholders(content_html)
                        content_html = strip_naked_internal_links(content_html)
                        content_html = merge_images_into_content(content_html, extracted.get('images', []))

                        # Upload images
                        urls_to_upload = []
                        featured_image_url = extracted.get('featured_image_url')
                        if featured_image_url and is_valid_upload_candidate(featured_image_url):
                            urls_to_upload.append(featured_image_url)

                        uploaded_src_map = {}
                        uploaded_id_map = {}
                        for url in urls_to_upload:
                            media = wp_client.upload_media_from_url(url, title)
                            if media and media.get("source_url") and media.get("id"):
                                k = url.rstrip('/')
                                uploaded_src_map[k] = media["source_url"]
                                uploaded_id_map[k] = media["id"]

                        content_html = rewrite_img_srcs_with_wp(content_html, uploaded_src_map)
                        content_html = strip_credits_and_normalize_youtube(content_html)
                        # Remove schemas JSON-LD originais do domínio fonte (evita conflito de SEO)
                        content_html = remove_source_domain_schemas(content_html)

                        # Add credit line
                        source_name = art_data['feed_config'].get('source_name', urlparse(art_data['url']).netloc)
                        credit_line = f'<p><strong>Fonte:</strong> <a href="{art_data["url"]}" target="_blank" rel="noopener noreferrer">{source_name}</a></p>'
                        content_html += f"\n{credit_line}"

                        # Process categories
                        final_category_ids = {WORDPRESS_CATEGORIES['Notícias']}  # Default
                        if source_specific_names := SOURCE_CATEGORY_MAP.get(art_data['source_id']):
                            for name in source_specific_names:
                                if cat_id := WORDPRESS_CATEGORIES.get(name):
                                    final_category_ids.add(cat_id)

                        if suggested_categories := rewritten_data.get('categorias', []):
                            suggested_names = [cat['nome'] for cat in suggested_categories if isinstance(cat, dict) and 'nome' in cat]
                            normalized_names = [CATEGORY_ALIASES.get(name.lower(), name) for name in suggested_names]
                            if dynamic_category_ids := wp_client.resolve_category_names_to_ids(normalized_names):
                                final_category_ids.update(dynamic_category_ids)

                        # Add internal links
                        if link_map:
                            content_html = add_internal_links(
                                html_content=content_html,
                                link_map_data=link_map,
                                current_post_categories=list(final_category_ids)
                            )

                        # Featured image
                        featured_media_id = None
                        if featured_url := extracted.get('featured_image_url'):
                            k = featured_url.rstrip('/')
                            featured_media_id = uploaded_id_map.get(k)
                        if not featured_media_id and uploaded_id_map:
                            featured_media_id = next(iter(uploaded_id_map.values()), None)

                        # SEO meta
                        yoast_meta = rewritten_data.get('yoast_meta', {})
                        yoast_meta['_yoast_wpseo_canonical'] = art_data['url']
                        if related_kws := rewritten_data.get('related_keyphrases'):
                            yoast_meta['_yoast_wpseo_keyphrases'] = json.dumps([{"keyword": kw} for kw in related_kws])

                        # ⚠️ VERIFICAÇÃO FINAL CRÍTICA: CTA CHECK ANTES DE PUBLICAR
                        final_cta_match = detect_forbidden_cta(content_html)
                        if final_cta_match:
                            logger.critical(f"🚨🚨🚨 CRITICAL: CTA detectado no conteúdo final ({final_cta_match}) - bloqueando publicação")
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="FINAL CHECK: CTA detected before WordPress publishing - Article blocked")
                            continue

                        logger.info("✅ CHECK FINAL PASSOU: Nenhum CTA detectado. Pronto para publicar.")
                        
                        # Publish to WordPress immediately (but processing was done in batch of 3)
                        post_payload = {
                            'title': title,
                            'slug': rewritten_data.get('slug'),
                            'content': content_html,
                            'excerpt': rewritten_data.get('meta_description', ''),
                            'categories': list(final_category_ids),
                            'tags': rewritten_data.get('tags_sugeridas', []),
                            'featured_media': featured_media_id,
                            'meta': yoast_meta,
                        }

                        wp_post_id = wp_client.create_post(post_payload)
                        if wp_post_id:
                            try:
                                sanitized_ok = wp_client.sanitize_published_post(wp_post_id)
                                if sanitized_ok:
                                    logger.info(f"✅ Post {wp_post_id} published and sanitized.")
                                else:
                                    logger.warning(f"⚠️ Post {wp_post_id} published but sanitation reported failure.")
                            except Exception as e:
                                logger.error(f"Error during post-publish sanitation for {wp_post_id}: {e}")

                            db.save_processed_post(art_data['db_id'], wp_post_id)
                            logger.info(f"Successfully published post {wp_post_id} for article DB ID {art_data['db_id']}")
                            
                            # Small delay between posts
                            logger.info(f"Aguardando {BETWEEN_PUBLISH_DELAY_S}s antes de publicar próximo artigo...")
                            time.sleep(BETWEEN_PUBLISH_DELAY_S)
                        else:
                            logger.error(f"Failed to publish post for {art_data['url']}")
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="WordPress publishing failed")

                    except Exception as e:
                        logger.error(f"Error processing article result {art_data['title']}: {e}", exc_info=True)
                        db.update_article_status(art_data['db_id'], 'FAILED', reason=str(e))

            except Exception as e:
                logger.error(f"Error processing batch: {e}", exc_info=True)
                # Se o lote falhar (ex: JSON malformado da IA), tente processar individualmente
                logger.warning("Batch processing failed. Attempting to process articles individually.")
                for art in batch:
                    try:
                        logger.info(f"Retrying article individually: {art['title']}")
                        # Recriar o payload para um único artigo
                        single_batch_data = [{
                            'title': art['extracted'].get('title'),
                            'content_html': art['extracted'].get('content', '') + "\n".join(art['extracted'].get('images', [])),
                            'source_url': art['url'], 'category': art['category'], 'videos': art['extracted'].get('videos', []),
                            'images': art['extracted'].get('images', []), 'source_name': art['feed_config'].get('source_name', ''),
                            'domain': wp_client.get_domain(), 'schema_original': art['extracted'].get('schema_original')
                        }]
                        # Chame o processador de IA com um único item
                        single_results = ai_processor.rewrite_batch(single_batch_data)
                        # A lógica de processamento do resultado já está dentro do loop, então podemos reusá-la
                        # (Esta é uma simplificação; uma refatoração maior poderia extrair a lógica de publicação)
                    except Exception as individual_e:
                        logger.error(f"Individual retry for article {art['title']} also failed: {individual_e}", exc_info=True)
                        db.update_article_status(art['db_id'], 'FAILED', reason=f"Individual retry failed: {individual_e}")

    finally:
        db.close()
        wp_client.close()

def worker_loop():
    """Continuously process articles from the queue in batches.
    
    Respects:
    - Max 10 AI requests per cycle (to avoid RPM violations)
    - 5-minute pause after hitting request limit
    """
    link_map = {}
    try:
        with open('data/internal_links.json', 'r', encoding='utf-8') as f:
            link_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Could not load internal_links.json for worker.")

    requests_in_cycle = 0
    MAX_REQUESTS_PER_CYCLE = 10
    PAUSE_ON_LIMIT_S = 300  # 5 minutos
    last_pause_time = 0

    while True:
        # Get up to 2 articles from queue (batch size 2)
        articles = []
        retry_count = 0
        max_retries = 180  # Wait up to 3 minutes (180 * 1s) for 2 articles
        
        while len(articles) < 2 and retry_count < max_retries:
            if article := article_queue.pop():
                articles.append(article)
            else:
                if len(articles) > 0:
                    # If we have some but not 2, wait a bit more
                    time.sleep(1)
                    retry_count += 1
                else:
                    # Empty queue, wait longer
                    time.sleep(2)
                    retry_count += 1

        if not articles:
            # Still no articles after all retries, reset and continue
            time.sleep(2)
            # Reset cycle counter after quiet period (1 min)
            if time.time() - last_pause_time > 60:
                requests_in_cycle = 0
            continue

        # Check if we've hit request limit
        if requests_in_cycle >= MAX_REQUESTS_PER_CYCLE:
            logger.warning(
                f"[RPM PROTECTION] Atingido limite de {MAX_REQUESTS_PER_CYCLE} requisições por ciclo. "
                f"Pausando pipeline por {PAUSE_ON_LIMIT_S}s (5 minutos)."
            )
            last_pause_time = time.time()
            time.sleep(PAUSE_ON_LIMIT_S)
            requests_in_cycle = 0
            logger.info("[RPM PROTECTION] Resumindo pipeline após pausa de 5 minutos.")
            continue

        # Process batch and count requests
        process_batch(articles, link_map)
        requests_in_cycle += len(articles)
        
        logger.info(
            f"Worker: {len(articles)} artigos processados. "
            f"Total requisições neste ciclo: {requests_in_cycle}/{MAX_REQUESTS_PER_CYCLE}. "
            f"Dormindo por {ARTICLE_SLEEP_S}s."
        )
        time.sleep(ARTICLE_SLEEP_S)

def run_pipeline_cycle():
    """Read feeds and enqueue articles for the worker."""
    logger.info("Starting new pipeline ingestion cycle.")
    db = Database()
    feed_reader = FeedReader(user_agent=PIPELINE_CONFIG.get('publisher_name', 'Bot'))
    
    processed_total_in_cycle = 0

    for source_id in PIPELINE_ORDER:
        if processed_total_in_cycle >= MAX_PER_CYCLE:
            logger.info(f"Max articles per cycle ({MAX_PER_CYCLE}) reached. Ending ingestion cycle.")
            break

        consecutive_failures = db.get_consecutive_failures(source_id)
        if consecutive_failures >= 3:
            logger.warning(f"Circuit open for feed {source_id} ({consecutive_failures} fails) -> skipping.")
            db.reset_consecutive_failures(source_id)
            continue

        feed_config = RSS_FEEDS.get(source_id)
        if not feed_config:
            logger.warning(f"No configuration found for feed source: {source_id}")
            continue

        logger.info(f"Ingesting feed: {source_id}")
        try:
            feed_items = feed_reader.read_feeds(feed_config, source_id)
            new_articles = db.filter_new_articles(source_id, feed_items)

            if not new_articles:
                logger.info(f"No new articles found for {source_id}.")
                continue
            
            # Apply per-feed and per-cycle limits
            limit = min(MAX_PER_FEED_CYCLE, MAX_PER_CYCLE - processed_total_in_cycle)
            articles_to_enqueue = new_articles[:limit]
            
            for article in articles_to_enqueue:
                article['source_id'] = source_id

            article_queue.push_many(articles_to_enqueue)
            
            processed_total_in_cycle += len(articles_to_enqueue)
            logger.info(f"Enqueued {len(articles_to_enqueue)} articles from {source_id}. Total in cycle: {processed_total_in_cycle}.")

            db.reset_consecutive_failures(source_id)

        except Exception as e:
            logger.error(f"Error processing feed {source_id}: {e}", exc_info=True)
            db.increment_consecutive_failures(source_id)
        
        # Stagger feed processing
        time.sleep(int(os.getenv('FEED_STAGGER_S', 45)))

    db.close()
    logger.info("Pipeline ingestion cycle finished.")

# Start the background worker thread
worker_thread = threading.Thread(target=worker_loop, daemon=True)
worker_thread.start()