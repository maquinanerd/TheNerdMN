import logging
import requests
import time
import json
import re 
import html as _html
from .html_utils import detect_forbidden_cta, strip_forbidden_cta_sentences
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def _slugify(name: str) -> str:
    """Creates a simple, WordPress-compatible slug from a string."""
    s = name.strip().lower()
    # Remove characters that are not alphanumeric, whitespace, or hyphen
    s = re.sub(r'[^\w\s-]', '', s, flags=re.UNICODE)
    # Replace whitespace and underscores with a hyphen
    s = re.sub(r'[\s_-]+', '-', s, flags=re.UNICODE)
    # Strip leading/trailing hyphens and limit length
    return s.strip('-')[:190] or 'tag'

class WordPressClient:
    """A client for interacting with the WordPress REST API."""

    def __init__(self, config: Dict[str, str], categories_map: Dict[str, int]):
        self.api_url = (config.get('url') or "").rstrip('/')
        if not self.api_url:
            raise ValueError("WORDPRESS_URL is not configured.")
        self.user = config.get('user')
        self.password = config.get('password')
        self.categories_map = categories_map
        self.session = requests.Session()
        if self.user and self.password:
            self.session.auth = (self.user, self.password)
        self.session.headers.update({'User-Agent': 'VocMoney-Pipeline/1.0'})

    def get_domain(self) -> str:
        """Extracts the domain from the WordPress URL."""
        try:
            return urlparse(self.api_url).netloc
        except Exception:
            return ""

    def _get_existing_tag_id(self, name: str) -> Optional[int]:
        """Searches for an existing tag by name or slug and returns its ID."""
        slug = _slugify(name)
        tags_endpoint = f"{self.api_url}/tags"
        params = {"search": name, "per_page": 100}

        try:
            r = self.session.get(tags_endpoint, params=params, timeout=20)
            r.raise_for_status()
            items = r.json()
            
            # WordPress search can be broad, so we verify the match
            for item in items:
                if item.get('name', '').strip().lower() == name.strip().lower():
                    return int(item['id'])
            for item in items:
                if item.get('slug') == slug:
                    return int(item['id'])
        except requests.RequestException as e:
            logger.error(f"Error searching for tag '{name}': {e}")
        
        return None

    def _create_tag(self, name: str) -> Optional[int]:
        """Creates a new tag and returns its ID."""
        tags_endpoint = f"{self.api_url}/tags"
        payload = {"name": name, "slug": _slugify(name)}
        
        try:
            r = self.session.post(tags_endpoint, json=payload, timeout=20)
            
            if r.status_code in (200, 201):
                tag_id = int(r.json()['id'])
                logger.info(f"Created new tag '{name}' with ID {tag_id}.")
                return tag_id
            
            # Handle race condition where tag was created between search and post
            if r.status_code == 400 and isinstance(r.json(), dict) and r.json().get("code") == "term_exists":
                logger.warning(f"Tag '{name}' already exists (race condition). Re-fetching ID.")
                return self._get_existing_tag_id(name)
            
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error creating tag '{name}': {e}")
            if e.response is not None:
                logger.error(f"Response body: {e.response.text}")

        return None

    def _ensure_tag_ids(self, tags: List[Any], max_tags: int = 10) -> List[int]:
        """Converts a list of tag names/IDs into a list of integer IDs, creating tags if necessary."""
        if not tags:
            return []

        # Normalize input (handles strings, ints, and comma-separated strings)
        norm_tags: List[str] = []
        for t in tags:
            if isinstance(t, int):
                norm_tags.append(str(t))
            elif isinstance(t, str):
                norm_tags.extend([p.strip() for p in t.split(',') if p.strip()])
        
        # Deduplicate and limit
        cleaned_tags = list(dict.fromkeys(norm_tags))[:max_tags]
        
        tag_ids: List[int] = []
        for tag_name in cleaned_tags:
            if tag_name.isdigit():
                tag_ids.append(int(tag_name))
            elif len(tag_name) >= 2:
                tag_id = self._get_existing_tag_id(tag_name) or self._create_tag(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)
        
        logger.info(f"Resolved tags {tags} to IDs: {tag_ids}")
        return tag_ids

    def _get_existing_category_id(self, name: str) -> Optional[int]:
        """Searches for an existing category by name or slug and returns its ID."""
        slug = _slugify(name)
        endpoint = f"{self.api_url}/categories"
        # WordPress category search is not as reliable as tag search, so we get more results and filter
        params = {"search": name, "per_page": 100}

        try:
            r = self.session.get(endpoint, params=params, timeout=20)
            r.raise_for_status()
            items = r.json()
            
            # Exact match on name (case-insensitive)
            for item in items:
                if item.get('name', '').strip().lower() == name.strip().lower():
                    return int(item['id'])
            # Match on slug
            for item in items:
                if item.get('slug') == slug:
                    return int(item['id'])
        except requests.RequestException as e:
            logger.error(f"Error searching for category '{name}': {e}")
        
        return None

    def _create_category(self, name: str) -> Optional[int]:
        """Creates a new category and returns its ID."""
        endpoint = f"{self.api_url}/categories"
        payload = {"name": name, "slug": _slugify(name)}
        
        try:
            r = self.session.post(endpoint, json=payload, timeout=20)
            
            if r.status_code in (200, 201):
                cat_id = int(r.json()['id'])
                logger.info(f"Created new category '{name}' with ID {cat_id}.")
                return cat_id
            
            if r.status_code == 400 and isinstance(r.json(), dict) and r.json().get("code") == "term_exists":
                logger.warning(f"Category '{name}' already exists (race condition). Re-fetching ID.")
                return self._get_existing_category_id(name)
            
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error creating category '{name}': {e}")
            if e.response is not None:
                logger.error(f"Response body: {e.response.text}")

        return None

    def resolve_category_names_to_ids(self, category_names: List[str]) -> List[int]:
        """Converts a list of category names into a list of integer IDs, creating categories if necessary."""
        if not category_names:
            return []

        # Deduplicate while preserving order (for logging)
        cleaned_names = list(dict.fromkeys([name.strip() for name in category_names if name.strip() and len(name) >= 1]))
        
        cat_ids: List[int] = []
        for name in cleaned_names:
            # Check local map first (from config)
            cat_id = self.categories_map.get(name)
            if not cat_id:
                # Case-insensitive check on local map
                for map_name, map_id in self.categories_map.items():
                    if map_name.lower() == name.lower():
                        cat_id = map_id
                        break
            
            # If not in local map, query WordPress
            if not cat_id:
                cat_id = self._get_existing_category_id(name) or self._create_category(name)

            if cat_id:
                cat_ids.append(cat_id)
        
        logger.info(f"Resolved category names {cleaned_names} to IDs: {cat_ids}")
        return cat_ids

    def upload_media_from_url(self, image_url: str, alt_text: str = "", max_attempts: int = 3) -> Optional[Dict[str, Any]]:
        """
        Downloads an image and uploads it to WordPress with a retry mechanism.
        """
        last_err = None
        for attempt in range(1, max_attempts + 1):
            try:
                # 1. Download the image with a reasonable timeout
                img_response = requests.get(image_url, timeout=25)
                img_response.raise_for_status()
                content_type = img_response.headers.get('Content-Type', 'image/jpeg')
                # Sanitize filename
                filename = (urlparse(image_url).path.split('/')[-1] or "image.jpg").split("?")[0]

                # 2. Upload to WordPress
                media_endpoint = f"{self.api_url}/media"
                headers = {
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Type': content_type,
                }
                wp_response = self.session.post(media_endpoint, headers=headers, data=img_response.content, timeout=40)
                wp_response.raise_for_status()
                logger.info(f"Successfully uploaded image: {image_url}")
                return wp_response.json() # Success

            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                logger.warning(f"Upload attempt {attempt}/{max_attempts} for '{image_url}' failed with network error: {e}. Retrying in {2*attempt}s...")
                time.sleep(2 * attempt)  # Simple backoff
            except Exception as e:
                last_err = e
                logger.error(f"Upload of '{image_url}' failed with non-retriable error: {e}")
                break # Don't retry on WP errors (4xx, 5xx) or other issues

        logger.error(f"Final failure to upload image '{image_url}' after {attempt} attempt(s): {last_err}")
        return None

    def set_media_alt_text(self, media_id: int, alt_text: str) -> bool:
        """Sets the alt text for a media item in WordPress."""
        if not alt_text:
            return False
        try:
            endpoint = f"{self.api_url}/media/{media_id}"
            payload = {"alt_text": alt_text}
            r = self.session.post(endpoint, json=payload, timeout=20)
            r.raise_for_status()
            logger.info(f"Successfully set alt text for media ID {media_id}.")
            return True
        except requests.RequestException as e:
            logger.warning(f"Failed to set alt_text on media {media_id}: {e}")
            if e.response is not None:
                logger.warning(f"Response body: {e.response.text}")
            return False

    def find_related_posts(self, term: str, limit: int = 3) -> List[Dict[str, str]]:
        """Searches for posts on the site and returns their title and URL."""
        if not term:
            return []
        try:
            endpoint = f"{self.api_url}/search"
            params = {"search": term, "per_page": limit, "_embed": "self"}
            resp = self.session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            # The 'url' in the search result is the API URL, we need the 'link' from the embedded post object
            return [{"title": i.get("title", ""), "url": i.get("_embedded", {}).get("self", [{}])[0].get("link", "")} for i in resp.json()]
        except requests.RequestException as e:
            logger.error(f"Error searching for related posts with term '{term}': {e}")
            return []

    def create_post(self, payload: Dict[str, Any]) -> Optional[int]:
        """Creates a new post in WordPress."""
        try:
            # Resolve tag names to integer IDs before sending
            if 'tags' in payload and payload['tags']:
                payload['tags'] = self._ensure_tag_ids(payload['tags'])

            posts_endpoint = f"{self.api_url}/posts"
            payload.setdefault('status', 'publish')
            
            # Clean up payload: remove fields WordPress REST API doesn't accept
            # Only send recognized fields to avoid 500 errors
            safe_fields = ['title', 'slug', 'content', 'excerpt', 'categories', 'tags', 'featured_media', 'status']
            clean_payload = {k: v for k, v in payload.items() if k in safe_fields}
            
            # Remove featured_media if it's 0 or None (invalid)
            if 'featured_media' in clean_payload:
                if not clean_payload['featured_media'] or clean_payload['featured_media'] == 0:
                    logger.warning("Featured media ID is invalid (0 or None). Removing from payload.")
                    del clean_payload['featured_media']
                else:
                    # Ensure it's an integer
                    clean_payload['featured_media'] = int(clean_payload['featured_media'])
            
            # Ensure title is plain text (no HTML tags)
            if 'title' in clean_payload and clean_payload['title']:
                title = clean_payload['title']
                # Remove any HTML tags that might have leaked into the title
                title = re.sub(r'<[^>]+>', '', title).strip()
                clean_payload['title'] = title
            
            # Sanitize content to prevent malformed HTML
            if 'content' in clean_payload and clean_payload['content']:
                content = clean_payload['content']
                # Replace problematic whitespace characters
                content = content.replace('\u00a0', ' ')  # Non-breaking space
                clean_payload['content'] = content
            
            # Ensure minimum content length
            if 'content' not in clean_payload or not clean_payload.get('content', '').strip():
                logger.error("ERROR: Post content is empty or missing. Cannot publish empty post.")
                return None
            
            if len(clean_payload['content']) < 100:
                logger.error(f"ERROR: Post content too short ({len(clean_payload['content'])} chars). Minimum 100 required.")
                return None
            
            # Ensure categories is a valid list of integers
            if 'categories' in clean_payload and clean_payload['categories']:
                try:
                    clean_payload['categories'] = [int(c) for c in clean_payload['categories'] if c]
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid category IDs, removing: {e}")
                    del clean_payload['categories']
            
            # Log a summary of the payload
            try:
                logger.info(
                    "WP payload: title_len=%d content_len=%d featured_media=%s cat=%s tags=%s",
                    len(clean_payload.get('title', '')),
                    len(clean_payload.get('content', '')),
                    clean_payload.get('featured_media', 'None'),
                    clean_payload.get('categories'),
                    clean_payload.get('tags')
                )
                if logger.isEnabledFor(logging.DEBUG):
                    log_payload = json.dumps(clean_payload, indent=2, ensure_ascii=False)
                    logger.debug(f"Sending clean payload to WordPress:\n{log_payload}")
            except Exception as log_e:
                logger.warning(f"Could not serialize payload for logging: {log_e}")

            response = self.session.post(posts_endpoint, json=clean_payload, timeout=60)
            
            if not response.ok:
                logger.error(f"WordPress post creation failed with status {response.status_code}: {response.text}")
                response.raise_for_status()

            return response.json().get('id')
        except requests.RequestException as e:
            logger.error(f"Failed to create WordPress post: {e}", exc_info=False)
            return None

    def get_post_content(self, post_id: int) -> Optional[str]:
        """Fetches the raw content of a post for sanitization checks."""
        endpoint = f"{self.api_url}/posts/{post_id}"
        params = {"context": "edit", "_fields": "content"}
        try:
            r = self.session.get(endpoint, params=params, timeout=30)
            r.raise_for_status()
            data = r.json() or {}
            content = data.get("content", {})
            return content.get("raw") or content.get("rendered")
        except requests.RequestException as e:
            logger.error(f"Failed to fetch content for post {post_id}: {e}")
            if getattr(e, "response", None) is not None:
                logger.error(f"Response body: {e.response.text}")
            return None

    def update_post_content(self, post_id: int, content: str) -> bool:
        """Updates the content of an existing post."""
        endpoint = f"{self.api_url}/posts/{post_id}"
        payload = {"content": content}
        try:
            r = self.session.post(endpoint, json=payload, timeout=40)
            if not r.ok:
                logger.error(f"Failed to update post {post_id} content: {r.status_code} - {r.text}")
                r.raise_for_status()
            logger.info(f"Successfully updated post {post_id} content after sanitation.")
            return True
        except requests.RequestException as e:
            logger.error(f"Error updating post {post_id}: {e}")
            if getattr(e, "response", None) is not None:
                logger.error(f"Response body: {e.response.text}")
            return False

    def sanitize_published_post(self, post_id: int, max_attempts: int = 2, backoff_s: int = 2) -> bool:
        """
        Fetches the published post's content and excerpt, detects forbidden CTAs,
        attempts to sanitize them (content + excerpt) and updates the post via API.

        Returns True if an update was applied (or no CTA present). False if update failed.
        """
        endpoint = f"{self.api_url}/posts/{post_id}"
        params = {"context": "edit", "_fields": "content,excerpt"}

        try:
            r = self.session.get(endpoint, params=params, timeout=30)
            r.raise_for_status()
            data = r.json() or {}
        except requests.RequestException as e:
            logger.error(f"Failed to fetch post {post_id} for sanitation: {e}")
            if getattr(e, 'response', None) is not None:
                logger.error(f"Response body: {e.response.text}")
            return False

        content_obj = data.get('content') or {}
        excerpt_obj = data.get('excerpt') or {}
        content_raw = content_obj.get('raw') or content_obj.get('rendered') or ''
        excerpt_raw = excerpt_obj.get('raw') or excerpt_obj.get('rendered') or ''

        # Normalize HTML entities to increase detection coverage
        content_raw = _html.unescape(content_raw)
        excerpt_raw = _html.unescape(excerpt_raw)

        content_cta = detect_forbidden_cta(content_raw)
        excerpt_cta = detect_forbidden_cta(excerpt_raw)

        if not content_cta and not excerpt_cta:
            logger.debug(f"No CTA detected for post {post_id} (content/excerpt).")
            return True

        logger.warning(f"CTA detected in published post {post_id}: content_cta={content_cta} excerpt_cta={excerpt_cta}")

        # Attempt sanitization
        new_content, content_removed = strip_forbidden_cta_sentences(content_raw)
        new_excerpt, excerpt_removed = strip_forbidden_cta_sentences(excerpt_raw)

        # Fallback: aggressive simple string replacements for common variants
        def aggressive_fallback(s: str) -> str:
            if not s:
                return s
            s = s.replace("don\'t forget to subscribe", "")
            s = s.replace("don’t forget to subscribe", "")
            s = s.replace("don't forget to subscribe", "")
            s = s.replace("Thank you for reading this post, don't forget to subscribe!", "")
            s = s.replace("Thank you for reading this post, dont forget to subscribe!", "")
            # remove common English CTA fragments
            s = re.sub(r"(?is)thank\s+you\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?", "", s)
            s = re.sub(r"(?is)thanks\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?", "", s)
            return s

        if not content_removed:
            fallback_content = aggressive_fallback(new_content or content_raw)
            if fallback_content != (new_content or content_raw):
                new_content = fallback_content
                content_removed = True

        if not excerpt_removed:
            fallback_excerpt = aggressive_fallback(new_excerpt or excerpt_raw)
            if fallback_excerpt != (new_excerpt or excerpt_raw):
                new_excerpt = fallback_excerpt
                excerpt_removed = True

        if not content_removed and not excerpt_removed:
            logger.error(f"Sanitization attempted for post {post_id} but no removals applied. Will still attempt update if user requested.")

        payload = {}
        if content_removed and new_content is not None:
            payload['content'] = new_content
        if excerpt_removed and new_excerpt is not None:
            payload['excerpt'] = new_excerpt

        if not payload:
            # Nothing to update
            logger.info(f"No payload to update for post {post_id} after sanitation attempt.")
            return True

        # Try updating with retries
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                r = self.session.post(endpoint, json=payload, timeout=40)
                if r.ok:
                    logger.info(f"Sanitation update applied to post {post_id} (attempt {attempt}).")
                    return True
                else:
                    logger.error(f"Failed to apply sanitation update to post {post_id} (attempt {attempt}): {r.status_code} - {r.text}")
            except requests.RequestException as e:
                logger.error(f"Error applying sanitation update to post {post_id} (attempt {attempt}): {e}")
            time.sleep(backoff_s * attempt)

        logger.error(f"All attempts to sanitize post {post_id} failed.")
        return False

    def get_published_posts(self, fields: List[str], max_posts: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches published posts, handling pagination, with an optional limit.

        Args:
            fields: A list of fields to retrieve for each post.
            max_posts: Optional limit on the total number of posts to fetch.
        """
        all_posts = []
        page = 1
        per_page = 100
        
        fields_str = ','.join(fields)

        while True:
            # Exit if we have reached the desired number of posts
            if max_posts and len(all_posts) >= max_posts:
                logger.info(f"Reached max_posts limit of {max_posts}. Stopping fetch.")
                break

            endpoint = f"{self.api_url}/posts"
            params = {
                "status": "publish",
                "per_page": per_page,
                "page": page,
                "_fields": fields_str,
            }
            try:
                logger.info(f"Fetching page {page} of published posts...")
                r = self.session.get(endpoint, params=params, timeout=30)
                r.raise_for_status()
                
                posts = r.json()
                if not posts:
                    logger.info("No more posts found. Finished fetching.")
                    break
                
                all_posts.extend(posts)
                
                if len(posts) < per_page:
                    logger.info(f"Last page reached ({len(posts)} posts). Finished fetching.")
                    break
                    
                page += 1

            except requests.RequestException as e:
                logger.error(f"Error fetching published posts (page {page}): {e}")
                if e.response is not None:
                    logger.error(f"Response body: {e.response.text}")
                break
        
        # Trim the list to the exact number if max_posts is set
        if max_posts:
            all_posts = all_posts[:max_posts]

        logger.info(f"Successfully fetched a total of {len(all_posts)} posts.")
        return all_posts

    def get_tags_map_by_ids(self, tag_ids: List[int]) -> Dict[int, str]:
        """ 
        Fetches tag details from a list of IDs and returns a map of {id: name}.
        Handles pagination for large lists of IDs.
        """
        if not tag_ids:
            return {}

        tag_map = {}
        unique_ids = list(set(tag_ids))
        endpoint = f"{self.api_url}/tags"
        
        # The 'include' parameter can take a list of up to 100 IDs.
        # We chunk the requests to handle more than 100.
        for i in range(0, len(unique_ids), 100):
            chunk = unique_ids[i:i + 100]
            params = {
                "include": ",".join(map(str, chunk)),
                "per_page": 100, # Ensure we get all requested items in the chunk
                "_fields": "id,name"
            }
            try:
                logger.info(f"Fetching names for {len(chunk)} tag IDs...")
                r = self.session.get(endpoint, params=params, timeout=30)
                r.raise_for_status()
                tags_data = r.json()
                for tag in tags_data:
                    tag_map[tag['id']] = tag['name']
            except requests.RequestException as e:
                logger.error(f"Error fetching tag details: {e}")
                # Continue to next chunk even if one fails
                continue
        
        logger.info(f"Successfully mapped {len(tag_map)} tag IDs to names.")
        return tag_map

    def close(self):
        """Closes the requests session."""
        self.session.close()