import os
from dotenv import load_dotenv
from typing import Dict, List, Any

# Carrega variáveis de ambiente de um arquivo .env
load_dotenv()

# --- Ordem de processamento dos feeds ---
PIPELINE_ORDER: List[str] = [
    'screenrant_movies',
    'screenrant_tv',
    'collider_movienews',
    'collider_tvnews',
    'comicbook_movies',
    'comicbook_tvshows',
    'gamerant_gaming',
    'thegamer_gamenews',
]

# --- Feeds RSS (padronizados, sem "synthetic_from") ---
RSS_FEEDS: Dict[str, Dict[str, Any]] = {
    'screenrant_movies': {
        'urls': ['https://screenrant.com/feed/movies/'],
        'category': 'movies',
        'source_name': 'ScreenRant',
    },
    'screenrant_tv': {
        'urls': ['https://screenrant.com/feed/tv/'],
        'category': 'tv',
        'source_name': 'ScreenRant',
    },
    'collider_movienews': {
        'urls': ['https://collider.com/feed/category/movie-news/'],
        'category': 'movies',
        'source_name': 'Collider',
    },
    'collider_tvnews': {
        'urls': ['https://collider.com/feed/category/tv-news/'],
        'category': 'tv',
        'source_name': 'Collider',
    },
    'comicbook_movies': {
        'urls': ['https://comicbook.com/category/movies/feed/'],
        'category': 'movies',
        'source_name': 'ComicBook.com',
    },
    'comicbook_tvshows': {
        'urls': ['https://comicbook.com/category/tv-shows/feed/'],
        'category': 'tv',
        'source_name': 'ComicBook.com',
    },
    'gamerant_gaming': {
        'urls': ['https://gamerant.com/feed/gaming/'],
        'category': 'gaming',
        'source_name': 'GameRant',
    },
    'thegamer_gamenews': {
        'urls': ['https://www.thegamer.com/feed/category/game-news/'],
        'category': 'gaming',
        'source_name': 'TheGamer',
    },
}

# --- HTTP ---
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/91.0.4472.124 Safari/537.36'
)

# --- Configuração da IA ---
def _load_ai_keys() -> List[str]:
    """
    Lê todas as chaves GEMINI_* do ambiente e as retorna em uma lista única e ordenada.
    """
    keys = {}
    for key, value in os.environ.items():
        if value and key.startswith('GEMINI_'):
            keys[key] = value
    
    # Sort by key name for predictable order (e.g., GEMINI_ECONOMIA_1, GEMINI_POLITICA_1)
    sorted_key_names = sorted(keys.keys())
    
    return [keys[k] for k in sorted_key_names]

AI_API_KEYS = _load_ai_keys()

# Caminho para o prompt universal na raiz do projeto
PROMPT_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..',
    'universal_prompt.txt'
)

AI_MODEL = os.getenv('AI_MODEL', 'gemini-2.5-flash-lite')

AI_GENERATION_CONFIG = {
    'temperature': 0.7,
    'top_p': 1.0,
    'max_output_tokens': 4096,
}

# --- WordPress ---
WORDPRESS_CONFIG = {
    'url': os.getenv('WORDPRESS_URL'),
    'user': os.getenv('WORDPRESS_USER'),
    'password': os.getenv('WORDPRESS_PASSWORD'),
}

# --- Posts Pilares para Linkagem Interna ---
# Adicione aqui as URLs completas dos seus posts mais importantes.
# A lógica de linkagem interna dará prioridade máxima a links que apontam para estes artigos.
PILAR_POSTS: List[str] = [
    # Ex: "https://seusite.com/guia-completo-de-futebol",
    # Ex: "https://seusite.com/historia-das-copas-do-mundo",
]

# IDs das categorias no WordPress (ajuste os IDs conforme o seu WP)
WORDPRESS_CATEGORIES: Dict[str, int] = {
    'movies': 11, # TODO: Substitua 11 pelo ID correto da categoria Movies
    'tv': 12, # TODO: Substitua 12 pelo ID correto da categoria TV
    'gaming': 13, # TODO: Substitua 13 pelo ID correto da categoria Gaming
    # Categorias genéricas
    'Notícias': 1,
}

# --- Sinônimos de Categorias ---
# Mapeia nomes alternativos (em minúsculas) para o slug canônico em WORDPRESS_CATEGORIES
CATEGORY_ALIASES: Dict[str, str] = {}


# --- Agendador / Pipeline ---
SCHEDULE_CONFIG = {
    'check_interval_minutes': int(os.getenv('CHECK_INTERVAL_MINUTES', 15)),
    'max_articles_per_feed': int(os.getenv('MAX_ARTICLES_PER_FEED', 3)),
    'per_article_delay_seconds': int(os.getenv('PER_ARTICLE_DELAY_SECONDS', 8)),
    'per_feed_delay_seconds': int(os.getenv('PER_FEED_DELAY_SECONDS', 15)),
    'cleanup_after_hours': int(os.getenv('CLEANUP_AFTER_HOURS', 72)),
}

PIPELINE_CONFIG = {
    'images_mode': os.getenv('IMAGES_MODE', 'hotlink'),  # 'hotlink' ou 'download_upload'
    'attribution_policy': 'Fonte: {domain}',
    'publisher_name': 'MaquinaNerd',
    'publisher_logo_url': os.getenv(
        'PUBLISHER_LOGO_URL',
        'https://exemplo.com/logo.png'  # TODO: atualizar para a URL real do logo
    ),
}
