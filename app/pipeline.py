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
from .html_utils import (
    merge_images_into_content,
    rewrite_img_srcs_with_wp,
    strip_credits_and_normalize_youtube,
    remove_broken_image_placeholders,
    strip_naked_internal_links,
)
from .internal_linking import add_internal_links
from .queue import ArticleQueue
from bs4 import BeautifulSoup
from .cleaners import clean_html_for_globo_esporte

logger = logging.getLogger(__name__)

# --- Global instances ---
article_queue = ArticleQueue()
ai_processor = AIProcessor()

# --- Environment variables for pipeline control ---
MAX_PER_FEED_CYCLE = int(os.getenv('MAX_PER_FEED_CYCLE', 3))
MAX_PER_CYCLE = int(os.getenv('MAX_PER_CYCLE', 12))
ARTICLE_SLEEP_S = int(os.getenv('ARTICLE_SLEEP_S', 60))

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
        for batch in [extracted_articles[i:i+3] for i in range(0, len(extracted_articles), 3)]:
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

                # Process results in same order
                for art_data, (rewritten_data, failure_reason) in zip(batch, batch_results):
                    try:
                        if not rewritten_data:
                            reason = failure_reason or "AI processing failed"
                            logger.warning(f"Article '{art_data['title']}' marked as FAILED (Reason: {reason})")
                            db.update_article_status(art_data['db_id'], 'FAILED', reason=reason)
                            continue

                        # Process content
                        content_html = rewritten_data.get("conteudo_final", "").strip()
                        title = rewritten_data.get("titulo_final", "").strip()

                        if not title or not content_html:
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="AI output missing required fields")
                            continue

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

                        # Publish to WordPress
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
                            db.save_processed_post(art_data['db_id'], wp_post_id)
                            logger.info(f"Successfully published post {wp_post_id} for article DB ID {art_data['db_id']}")
                        else:
                            logger.error(f"Failed to publish post for {art_data['url']}")
                            db.update_article_status(art_data['db_id'], 'FAILED', reason="WordPress publishing failed")

                    except Exception as e:
                        logger.error(f"Error processing article result {art_data['title']}: {e}", exc_info=True)
                        db.update_article_status(art_data['db_id'], 'FAILED', reason=str(e))

            except Exception as e:
                logger.error(f"Error processing batch: {e}", exc_info=True)
                for art in batch:
                    # Use a more specific reason if it's a parsing error
                    reason = str(e)
                    if "Failed to parse batch AI response" in reason:
                        db.update_article_status(art['db_id'], 'FAILED', reason="Failed to parse batch AI response")
                    else:
                        db.update_article_status(art['db_id'], 'FAILED', reason=f"Batch processing error: {reason}")

    finally:
        db.close()
        wp_client.close()

def worker_loop():
    """Continuously process articles from the queue in batches."""
    link_map = {}
    try:
        with open('data/internal_links.json', 'r', encoding='utf-8') as f:
            link_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Could not load internal_links.json for worker.")

    while True:
        # Get up to 3 articles from queue
        articles = []
        for _ in range(3):
            if article := article_queue.pop():
                articles.append(article)

        if not articles:
            time.sleep(2)
            continue

        # Process batch
        process_batch(articles, link_map)
        logger.info(f"Worker sleeping for {ARTICLE_SLEEP_S}s.")
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