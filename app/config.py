import os
from dotenv import load_dotenv
from typing import Dict, List, Any

# Carrega variáveis de ambiente de um arquivo .env
load_dotenv()

# --- Ordem de processamento dos feeds ---
PIPELINE_ORDER: List[str] = [
    'screenrant_movie_lists',
    'screenrant_movie_news',
    'screenrant_tv_news',
    'screenrant_tv_lists',
    'thegamer_gamenews',
]

# --- Feeds RSS (padronizados, sem "synthetic_from") ---
RSS_FEEDS: Dict[str, Dict[str, Any]] = {
    'screenrant_movie_lists': {
        'urls': ['https://screenrant.com/feed/movie-lists/'],
        'category': 'movies',
        'source_name': 'ScreenRant',
    },
    'screenrant_movie_news': {
        'urls': ['https://screenrant.com/feed/movie-news/'],
        'category': 'movies',
        'source_name': 'ScreenRant',
    },
    'screenrant_tv_news': {
        'urls': ['https://screenrant.com/tv-news/'],
        'category': 'tv',
        'source_name': 'ScreenRant',
    },
    'screenrant_tv_lists': {
        'urls': ['https://screenrant.com/tv-lists/'],
        'category': 'tv',
        'source_name': 'ScreenRant',
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
    'Notícias': 20,
    'Filmes': 24,
    'Séries': 21,  # ID correto de Séries (era 24 antes, mesmo ID de Filmes!)
    'Games': 73,
}

# Mapeia o source_id para uma lista de nomes de categorias
SOURCE_CATEGORY_MAP: Dict[str, List[str]] = {
    'screenrant_movie_lists': ['Filmes'],
    'screenrant_movie_news': ['Filmes'],
    'screenrant_tv_news': ['Séries'],
    'screenrant_tv_lists': ['Séries'],
    'thegamer_gamenews': ['Games'],
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
