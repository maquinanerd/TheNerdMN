# app/html_utils.py
import re
import logging
import html
import unicodedata
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# =========================
# CTA sanitization helpers
# =========================

def _normalize_text_for_cta(text: str) -> str:
    """Normaliza texto para comparação de CTA (minúsculas, sem acentos/pontuação)."""
    if not text:
        return ""
    # Desescapar entidades HTML e normalizar aspas/apóstrofos
    text = html.unescape(text)
    text = text.replace("’", "'").replace("`", "'")
    # Normalizar para remover acentos mantendo caracteres base
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = text.lower()
    # Remover qualquer caractere que não seja alfanumérico (substituir por espaço)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_fragment(fragment: str) -> str:
    """Normaliza pequenos fragmentos usados nas regras de detecção."""
    return _normalize_text_for_cta(fragment)


_CTA_SEQUENCE_RULES = [
    ("thank you for reading + subscribe", ("thank you for reading", "subscribe")),
    ("thank you for reading this post + subscribe", ("thank you for reading this post", "subscribe")),
    ("thank you for reading this article + subscribe", ("thank you for reading this article", "subscribe")),
    ("thanks for reading + subscribe", ("thanks for reading", "subscribe")),
    ("thanks for visiting + subscribe", ("thanks for visiting", "subscribe")),
    ("thank you for visiting + subscribe", ("thank you for visiting", "subscribe")),
    ("obrigado por ler + inscreva-se", ("obrigado por ler", "inscreva")),
    ("obrigada por ler + inscreva-se", ("obrigada por ler", "inscreva")),
]

_CTA_SEQUENCE_RULES_NORMALIZED = [
    (label, tuple(_normalize_fragment(part) for part in parts))
    for label, parts in _CTA_SEQUENCE_RULES
]

_CTA_PHRASE_RULES = [
    ("thank you for reading this post don't forget to subscribe", "thank you for reading this post don't forget to subscribe"),
    ("thank you for reading don't forget to subscribe", "thank you for reading don't forget to subscribe"),
    ("thanks for reading don't forget to subscribe", "thanks for reading don't forget to subscribe"),
    ("please subscribe", "please subscribe"),
    ("subscribe now", "subscribe now"),
    ("subscribe to our", "subscribe to our"),
    ("don't forget to subscribe", "don't forget to subscribe"),
    ("dont forget to subscribe", "dont forget to subscribe"),
    ("thanks for reading", "thanks for reading"),
    ("thank you for reading", "thank you for reading"),
    ("obrigado por ler", "obrigado por ler"),
    ("obrigada por ler", "obrigada por ler"),
    ("nao esqueça de se inscrever", "nao esqueça de se inscrever"),
    ("não esqueça de se inscrever", "não esqueça de se inscrever"),
    ("nao deixe de se inscrever", "nao deixe de se inscrever"),
    ("se inscreva", "se inscreva"),
    ("inscreva se", "inscreva se"),
    ("cadastre se", "cadastre se"),
    ("stay tuned", "stay tuned"),
    ("follow us", "follow us"),
]

_CTA_PHRASE_RULES_NORMALIZED = [
    (label, _normalize_fragment(phrase)) for label, phrase in _CTA_PHRASE_RULES
]

_CTA_FALLBACK_REGEXES = [
    r"(?is)thank\s+you\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)thanks\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)thank\s+you\s+for\s+visiting[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)obrigad[ao]\s+por\s+ler[^<]{0,200}?(?:inscreva|inscricao|inscreve)[^<]{0,200}?",
    r"(?is)nao\s+esquec[aã]\s+de\s+se\s+inscrever[^<]{0,200}?",
]


def detect_forbidden_cta_from_text(text: str) -> Optional[str]:
    """Retorna uma descrição da regra de CTA detectada no texto normalizado, se existir."""
    norm = _normalize_text_for_cta(text)
    if not norm:
        return None
    for label, parts in _CTA_SEQUENCE_RULES_NORMALIZED:
        if all(part in norm for part in parts):
            return label
    for label, phrase in _CTA_PHRASE_RULES_NORMALIZED:
        if phrase and phrase in norm:
            return label
    return None


def strip_forbidden_cta_sentences(html_content: str) -> Tuple[str, bool]:
    """Remove blocos que contenham CTAs proibidos. Retorna HTML limpo e flag se removeu algo."""
    if not html_content:
        return html_content, False

    soup = BeautifulSoup(html_content, "lxml")
    removed = False
    target_tags = ("p", "div", "span", "section", "article", "blockquote", "li", "strong", "em", "footer")

    for tag_name in target_tags:
        for node in list(soup.find_all(tag_name)):
            if not node.parent:
                continue
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            if detect_forbidden_cta_from_text(text):
                logger.debug("Removendo nó com CTA proibido: %s", text[:100])
                node.decompose()
                removed = True

    cleaned_html = soup.body.decode_contents() if soup.body else str(soup)

    # Passo adicional: remover frases residuais diretamente no HTML bruto
    for pattern in _CTA_FALLBACK_REGEXES:
        cleaned_html, substitutions = re.subn(pattern, "", cleaned_html)
        if substitutions:
            removed = True

    return cleaned_html, removed


def detect_forbidden_cta(html_content: str) -> Optional[str]:
    """Detecta CTAs proibidos em HTML, retornando a descrição da regra se encontrada."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "lxml")
    text = soup.get_text(" ", strip=True)
    return detect_forbidden_cta_from_text(text)

# =========================
# HTML unescape utility
# =========================

def unescape_html_content(content: str) -> str:
    """
    Desescapa HTML escapado (ex: &lt; para <, &gt; para >, etc).
    Útil para conteúdo que retorna da IA com HTML escapado no JSON.
    """
    if not content:
        return content
    return html.unescape(content)

# =========================
# YouTube helpers/normalizer
# =========================

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"
}

def _yt_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            return None
        # /embed/ID
        if u.path.startswith("/embed/"):
            return u.path.split("/")[2].split("?")[0]
        # /shorts/ID
        if u.path.startswith("/shorts/"):
            return u.path.split("/")[2].split("?")[0]
        # youtu.be/ID
        if host.endswith("youtu.be"):
            return u.path.lstrip("/").split("?")[0]
        # /watch?v=ID
        if u.path == "/watch":
            q = parse_qs(u.query)
            return (q.get("v") or [None])[0]
    except Exception:
        pass
    return None


def strip_credits_and_normalize_youtube(html: str) -> str:
    """
    - Remove linhas de crédito (figcaption/p/span iniciando com Crédito/Credito/Fonte)
    - Converte iframes do YouTube em <p> com URL watch (WordPress oEmbed)
    - Remove iframes não-YouTube, vazios ou com placeholders (ex.: URL_DO_EMBED_AQUI)
    - Remove <p> vazios após a limpeza e desfaz <figure> que só envolvem embed
    """
    if not html:
        return html

    soup = BeautifulSoup(html, "lxml")

    # 1) Remover “Crédito:”, “Credito:”, “Fonte:”
    for node in soup.find_all(["figcaption", "p", "span"]):
        t = (node.get_text() or "").strip().lower()
        if t.startswith(("crédito:", "credito:", "fonte:")):
            node.decompose()

    # 2) Tratar iframes
    for iframe in list(soup.find_all("iframe")):
        src = (iframe.get("src") or "").strip()
        # placeholder ou vazio? remover
        if (not src) or ("URL_DO_EMBED_AQUI" in src):
            iframe.decompose()
            continue
        # YouTube -> URL watch
        vid = _yt_id_from_url(src)
        if vid:
            p = soup.new_tag("p")
            p.string = f"https://www.youtube.com/watch?v={vid}"
            iframe.replace_with(p)
        else:
            # não-YouTube -> remove
            iframe.decompose()

    # 3) Limpar <figure> que só envolvem o embed ou ficaram vazias
    for fig in list(soup.find_all("figure")):
        if fig.find("img"):
            continue
        children_tags = [c for c in fig.contents if getattr(c, "name", None)]
        only_p = (len(children_tags) == 1 and getattr(children_tags[0], "name", None) == "p")
        p = children_tags[0] if only_p else None
        p_text = (p.get_text().strip() if p else "")
        if only_p and ("youtube.com/watch" in p_text or "youtu.be/" in p_text):
            fig.replace_with(p)
        elif not fig.get_text(strip=True):
            fig.unwrap()

    # 4) Remover <p> vazios (sem texto e sem elementos)
    for p in list(soup.find_all("p")):
        if not p.get_text(strip=True) and not p.find(True):
            p.decompose()

    return soup.body.decode_contents() if soup.body else str(soup)


def hard_filter_forbidden_html(html: str) -> str:
    """
    Sanitiza HTML:
      - remove: script, style, noscript, form, input, button, select, option,
                textarea, object, embed, svg, canvas, link, meta
      - iframes: permite só YouTube (vira oEmbed); remove vazios/placeholder
      - remove atributos on* e href/src com javascript:
      - remove <p> vazios após limpeza
    """
    if not html:
        return html

    soup = BeautifulSoup(html, "lxml")

    REMOVE_TAGS = {
        "script","style","noscript","form","input","button","select","option",
        "textarea","object","embed","svg","canvas","link","meta"
    }
    for tag_name in REMOVE_TAGS:
        for t in soup.find_all(tag_name):
            t.decompose()

    # iframes
    for iframe in list(soup.find_all("iframe")):
        src = (iframe.get("src") or "").strip()
        if (not src) or ("URL_DO_EMBED_AQUI" in src):
            iframe.decompose()
            continue
        vid = _yt_id_from_url(src)
        if vid:
            p = soup.new_tag("p")
            p.string = f"https://www.youtube.com/watch?v={vid}"
            iframe.replace_with(p)
        else:
            iframe.decompose()

    # atributos perigosos
    for el in soup.find_all(True):
        for attr in list(el.attrs.keys()):
            if attr.lower().startswith("on"):
                del el.attrs[attr]
        for attr in ("href", "src"):
            if el.has_attr(attr):
                val = (el.get(attr) or "").strip()
                if val.lower().startswith("javascript:"):
                    del el.attrs[attr]

    # <p> vazios
    for p in list(soup.find_all("p")):
        if not p.get_text(strip=True) and not p.find(True):
            p.decompose()

    return soup.body.decode_contents() if soup.body else str(soup)


# =========================
# Imagens: merge e rewrite
# =========================

def _norm_key(u: str) -> str:
    """Normaliza URL para comparação/chave de dicionário."""
    if not u:
        return ""
    return (u.strip().rstrip("/")).lower()


def _replace_in_srcset(srcset: str, mapping: Dict[str, str]) -> str:
    """
    Substitui URLs dentro de um atributo srcset usando o mapping (url_original -> nova_url).
    Mantém os sufixos (ex.: '320w').
    """
    if not srcset:
        return srcset
    parts = []
    for chunk in srcset.split(","):
        item = chunk.strip()
        if not item:
            continue
        tokens = item.split()
        url = tokens[0]
        rest = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        new_url = mapping.get(_norm_key(url), url)
        parts.append((new_url + (" " + rest if rest else "")).strip())
    return ", ".join(parts)


def merge_images_into_content(content_html: str, image_urls: List[str], max_images: int = 6) -> str:
    """
    Garante imagens no corpo:
      - mantém as que já existem
      - injeta até `max_images` novas (que não estejam no HTML)
      - não adiciona créditos/legendas
      - insere após o primeiro parágrafo; se não houver, ao final
      
    IMPORTANTE: Garante que cada imagem tem pelo menos um alt text e está dentro
    de uma figura com figcaption corretamente estruturada.
    """
    if not content_html:
        content_html = ""
    soup = BeautifulSoup(content_html, "lxml")

    # conjunto de URLs já presentes
    present: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src:
            present.add(_norm_key(src))
        # considerar srcset como presença também
        if img.get("srcset"):
            for chunk in img["srcset"].split(","):
                u = chunk.strip().split()[0]
                if u:
                    present.add(_norm_key(u))

    to_add: List[str] = []
    for u in (image_urls or []):
        key = _norm_key(u)
        if not key or key in present:
            continue
        to_add.append(u)
        if len(to_add) >= max_images:
            break

    if to_add:
        # ponto de inserção: após o primeiro <p>; senão, ao final do body/raiz
        insertion_point = soup.find("p")
        parent = insertion_point.parent if insertion_point and insertion_point.parent else (soup.body or soup)

        for u in to_add:
            fig = soup.new_tag("figure")
            img = soup.new_tag("img", src=u, alt="")
            
            # Extrair um nome descritivo da URL para usar como alt text básico
            try:
                filename = u.split('/')[-1].split('?')[0]
                # Limpar extensão e caracteres especiais
                clean_name = re.sub(r'\.(jpg|jpeg|png|gif|webp)$', '', filename, flags=re.IGNORECASE)
                clean_name = re.sub(r'[-_]', ' ', clean_name)
                if clean_name:
                    img['alt'] = clean_name.strip()
            except Exception:
                img['alt'] = 'Imagem do artigo'
            
            fig.append(img)
            
            # Adicionar figcaption vazio (para ser preenchido posteriormente se necessário)
            figcaption = soup.new_tag("figcaption")
            figcaption.string = img.get('alt', 'Imagem')
            fig.append(figcaption)
            
            if insertion_point:
                insertion_point.insert_after(fig)
                insertion_point = fig  # próximo entra depois do que inserimos
            else:
                parent.append(fig)

    return soup.body.decode_contents() if soup.body else str(soup)


def rewrite_img_srcs_with_wp(content_html: str, uploaded_src_map: Dict[str, str]) -> str:
    """
    Reaponta <img> e srcset para as URLs do WordPress já enviadas.
    - uploaded_src_map: {url_original (normalizada) -> new_source_url_no_wp}
    """
    if not content_html or not uploaded_src_map:
        return content_html

    # normalizar chaves do mapping
    norm_map: Dict[str, str] = {_norm_key(k): v for k, v in uploaded_src_map.items() if k and v}

    soup = BeautifulSoup(content_html, "lxml")
    for img in soup.find_all("img"):
        # src
        src = (img.get("src") or "").strip()
        key = _norm_key(src)
        if key in norm_map:
            img["src"] = norm_map[key]

        # srcset
        if img.get("srcset"):
            img["srcset"] = _replace_in_srcset(img["srcset"], norm_map)

        # data-* (evita rehydration quebrado)
        for a in ("data-src", "data-original", "data-lazy-src", "data-image", "data-img-url"):
            if img.has_attr(a):
                k2 = _norm_key(img.get(a) or "")
                if k2 in norm_map:
                    img[a] = norm_map[k2]

    return soup.body.decode_contents() if soup.body else str(soup)


def validate_and_fix_figures(html: str) -> str:
    """
    Valida e corrige estruturas de <figure> no HTML.
    
    Problemas corrigidos:
    - Figuras com src contendo HTML (ex: src="<figure><img src=...>")
    - Figuras sem <figcaption>
    - <img> fora de <figure>
    - URLs inválidas em src
    
    NOTA: BeautifulSoup desescapa automaticamente ao fazer parsing,
    então procuramos por '<' no src, não '&lt;'.
    """
    if not html:
        return html
    
    # PASSO 0: Detectar e corrigir src malformados ANTES do parsing
    # Se temos src="<figure>... ou src="<img... extrair a URL real
    def fix_malformed_src(text):
        """Fixa srcs que contêm HTML."""
        # Procurar por src="...conteúdo com < e >"
        # E extrair a URL https://... dentro
        pattern = r'src="[^"]*<[^"]*https?://[^\s"\'<>]*[^"]*"'
        
        def extract_and_fix(match):
            src_with_html = match.group(0)  # ex: src="<figure><img src="https://..."...>"
            # Extrair apenas a URL
            url_match = re.search(r'https?://[^\s"\'<>]+', src_with_html)
            if url_match:
                url = url_match.group(0)
                return f'src="{url}"'
            return ''
        
        return re.sub(pattern, extract_and_fix, text)
    
    html = fix_malformed_src(html)
    
    soup = BeautifulSoup(html, "lxml")
    
    # 1. Corrigir figuras com img que tem src contendo HTML estrutural
    # (BeautifulSoup desescapa automaticamente, então procuramos por '<' literal)
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        
        # Detectar se o src contém estrutura HTML (começa com < ou contém <img)
        if src and (src.startswith("<") or "<img" in src or "<figure" in src):
            logger.warning(f"Encontrada imagem com src contendo HTML estrutural: {src[:100]}...")
            
            # Tentar encontrar uma URL https://... ou http://... dentro do src
            url_match = re.search(r'https?://[^\s"\'<>]+', src)
            if url_match:
                real_url = url_match.group(0)
                logger.info(f"Extraída URL real do HTML no src: {real_url}")
                img["src"] = real_url
            else:
                logger.info("Removendo <img> com src contendo HTML estrutural (URL não encontrada)")
                fig = img.find_parent("figure")
                if fig:
                    fig.decompose()
                else:
                    img.decompose()
    
    # 2. Garantir que toda <img> está dentro de <figure> e tem <figcaption>
    for img in soup.find_all("img"):
        # Se a imagem não está dentro de figure, envolver
        fig_parent = img.find_parent("figure")
        if not fig_parent:
            fig = soup.new_tag("figure")
            img.wrap(fig)
            fig_parent = fig
        
        # Garantir que tem figcaption
        if not fig_parent.find("figcaption"):
            figcaption = soup.new_tag("figcaption")
            alt_text = img.get("alt", "Imagem")
            figcaption.string = alt_text if alt_text else "Imagem"
            fig_parent.append(figcaption)
        
        # Garantir que img tem alt text
        if not img.get("alt"):
            img["alt"] = "Imagem"
    
    # 3. Remover figuras vazias ou com conteúdo inválido
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if not img:
            # Figure sem img, remover
            fig.decompose()
            continue
        
        src = (img.get("src") or "").strip()
        if not src or not src.startswith(("http://", "https://")):
            logger.warning(f"Removendo figura com URL inválida: {src}")
            fig.decompose()
    
    return soup.body.decode_contents() if soup.body else str(soup)

# --- Stub para compatibilidade com pipeline: não adiciona crédito nenhum ---
from typing import Optional

def add_credit_to_figures(html: str, source_url: Optional[str] = None) -> str:
    """
    Compat: função mantida apenas para evitar ImportError.
    Não faz nada e retorna o HTML intacto (sem créditos).
    """
    logger.info("add_credit_to_figures desabilitada: retornando HTML sem alterações.")
    return html

# =========================
# Post-AI Defensive Cleanup
# =========================

def remove_broken_image_placeholders(html: str) -> str:
    """
    Removes text-based image placeholders that the AI might mistakenly add,
    like '[Imagem Destacada]' on its own line, without affecting real content.
    """
    if not html or "Imagem" not in html:
        return html
    # This regex targets lines that ONLY contain the placeholder.
    # `^` and `$` anchor to the start and end of a line due to MULTILINE flag.
    # It avoids touching legitimate text that happens to contain the word "Imagem".
    return re.sub(
        r'^\s*(\[?Imagem[^\n<]*\]?)\s*$',
        '',
        html,
        flags=re.IGNORECASE | re.MULTILINE
    )


def strip_naked_internal_links(html: str) -> str:
    """
    Removes paragraphs that contain nothing but a bare URL to an internal
    tag or category page, a common AI formatting error.
    """
    if not html or ("/tag/" not in html and "/categoria/" not in html):
        return html
    # This regex looks for a <p> tag containing only a URL to /tag/ or /categoria/.
    return re.sub(
        r'<p>\s*https?://[^<>\s]+/(?:tag|categoria)/[a-z0-9\-_/]+/?\s*</p>',
        '',
        html,
        flags=re.IGNORECASE
    )


def remove_source_domain_schemas(html: str) -> str:
    """
    Remove todos os blocos JSON-LD que vieram do conteúdo original da fonte.
    
    O WordPress injeta automaticamente os schemas corretos com o domínio 
    maquinanerd.com.br via WPCode ou plugins. Remover os schemas originais 
    evita conflitos de Schema.org e problemas de SEO/Google News.
    
    Remove:
    - <script type="application/ld+json">...</script> com qualquer conteúdo
    
    Preserva outros scripts (analytics, publicidade, etc.)
    """
    if not html:
        return html
    
    # Remove qualquer bloco de script JSON-LD
    # Usa re.DOTALL para capturar conteúdo multi-linha
    cleaned = re.sub(
        r'<script[^>]*type="application/ld\+json"[^>]*>.*?</script>',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )
    
    # Remove também a variação inversa do atributo type
    cleaned = re.sub(
        r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>.*?</script>',
        '',
        cleaned,
        flags=re.DOTALL | re.IGNORECASE
    )
    
    logger.debug("Removed source domain JSON-LD schemas from content")
    return cleaned
