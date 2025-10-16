# Guia de Reutilização e Configuração do Projeto

Este documento serve como um guia essencial para reconfigurar este projeto Python para um novo nicho, site ou conjunto de chaves de API. Ao copiar a estrutura de pastas para um novo projeto, siga os passos abaixo para garantir que tudo funcione corretamente e que o sistema se adapte perfeitamente às suas novas necessidades.

## Visão Geral das Funcionalidades do Projeto

Este aplicativo de produção em Python automatiza a pipeline de conteúdo, desde a leitura de feeds RSS até a publicação otimizada no WordPress. Suas principais funcionalidades incluem:

-   **Leitura de Feeds RSS**: Processa múltiplos feeds de notícias em uma ordem definida.
-   **Extração de Conteúdo Completo**: Obtém título, conteúdo integral, imagens e vídeos (incluindo YouTube) das páginas dos artigos.
-   **Reescrita com IA (Gemini)**: Utiliza um modelo de linguagem para reescrever e otimizar o conteúdo para SEO, seguindo um prompt customizável.
-   **Publicação no WordPress**: Publica artigos reescritos via API REST, configurando título, conteúdo, resumo, categorias, tags e imagem destacada.
-   **Agendamento Contínuo**: Opera em ciclos usando `APScheduler`.
-   **Resiliência**: Inclui retentativas com backoff exponencial, failover de chaves de API e deduplicação de artigos.
-   **Armazenamento Local**: Usa SQLite para rastrear artigos processados e falhas.
-   **Modularidade**: Código organizado em módulos com responsabilidades claras.

## Passo a Passo para Nova Configuração

Para adaptar o projeto a um novo contexto, você precisará ajustar as seguintes partes:

### 1. Chaves de API e Credenciais (`.env`)

A configuração mais crítica, que inclui chaves de API, senhas e URLs sensíveis, é gerenciada por meio de variáveis de ambiente.

1.  **Crie o arquivo `.env`**: Se ainda não existir, renomeie ou copie o arquivo `.env.example` para `.env` na raiz do projeto.
2.  **Preencha as variáveis**: Abra o arquivo `.env` e preencha as variáveis com os valores correspondentes ao seu novo projeto. As variáveis essenciais a serem alteradas são:
    *   `WORDPRESS_URL`: A URL base da API REST do seu novo site WordPress (ex: `https://meunovosite.com/wp-json/wp/v2/`).
    *   `WORDPRESS_USER`: O nome de usuário para autenticação no WordPress (recomenda-se um usuário com permissões de editor/autor).
    *   `WORDPRESS_PASSWORD`: A senha de aplicativo gerada no WordPress para o usuário acima.
    *   `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, etc.: Suas chaves de API do Google Gemini. Se você tiver um esquema de failover por categoria (como no projeto original), preencha as chaves específicas para `GEMINI_MOVIES_1`, `GEMINI_SERIES_1`, `GEMINI_GAMES_1`, e as chaves de backup (`GEMINI_BACKUP_1`, etc.).
    *   Outras chaves de API ou credenciais que você possa ter adicionado ou que sejam necessárias para serviços externos.

### 2. Configurações Principais do Aplicativo (`app/config.py`)

Este arquivo centraliza as configurações que definem o comportamento geral do pipeline.

*   **Caminho do arquivo:** `e:\Área de Trabalho 2\Portal The News\Nerd\TheNews_MaquinaNerd\app\config.py`
*   **O que fazer:**
    *   **`PIPELINE_ORDER`**: Altere a lista de `source_id`s para refletir a ordem em que você deseja processar seus novos feeds.
    *   **`RSS_FEEDS`**: Substitua os dicionários de feeds RSS pelos URLs das novas fontes de conteúdo do seu nicho. Certifique-se de mapear cada feed para uma `category` relevante para o seu novo projeto.
    *   **`USER_AGENT`**: Mantenha um User-Agent válido para evitar bloqueios ao extrair conteúdo.
    *   **`AI_CONFIG`**: Se o seu novo projeto usar um esquema de chaves Gemini por categoria, ajuste a estrutura e os nomes das variáveis de ambiente que serão lidas para cada categoria (ex: `movies`, `series`, `games`).
    *   **`WORDPRESS_CONFIG`**: A estrutura base é lida do `.env`, mas você pode precisar ajustar outros parâmetros se houver.
    *   **`WORDPRESS_CATEGORIES`**: Atualize este dicionário para mapear os nomes das categorias que você usará no seu novo projeto para os IDs correspondentes no seu WordPress.
    *   **`SCHEDULE_CONFIG`**: Ajuste os intervalos de `check_interval`, `max_articles_per_feed`, `api_call_delay` e `cleanup_after_hours` conforme a cadência desejada para o seu novo pipeline.
    *   **`PIPELINE_CONFIG`**: Configure `images_mode` (`'hotlink'` ou `'download_upload'`), `attribution_policy`, `publisher_name` e `publisher_logo_url` para refletir a identidade do seu novo site.
    *   **`DEFAULT_CATEGORY_ID`**: Defina o ID da categoria padrão no WordPress para onde os posts serão enviados caso uma categoria específica não seja encontrada.
    *   **`DEFAULT_POST_STATUS`**: Altere o status padrão da postagem (ex: `'draft'` para revisão manual, ou `'publish'` para publicação automática).

### 3. Prompt da IA (`universal_prompt.txt`)

Este arquivo é crucial para guiar o comportamento da IA na reescrita e otimização do conteúdo.

*   **Caminho do arquivo:** `e:\Área de Trabalho 2\Portal The News\Nerd\TheNews_MaquinaNerd\universal_prompt.txt`
*   **O que fazer:** Adapte o texto do prompt para instruir a IA a reescrever, categorizar e gerar tags de acordo com o estilo, tom e regras de SEO do seu novo nicho. Mantenha as seções `Novo Título:`, `Novo Resumo:`, `Novo Conteúdo:` para garantir o formato de saída esperado.

### 4. Lógica de Categorização e Tags

Estes módulos contêm a inteligência para classificar e marcar o conteúdo.

*   **`app/categorizer.py`**
    *   **Caminho do arquivo:** `e:\Área de Trabalho 2\Portal The News\Nerd\TheNews_MaquinaNerd\app\categorizer.py`
    *   **O que fazer:** Revise a lógica de mapeamento de feeds para categorias do WordPress. Se o seu novo nicho tiver padrões diferentes (ex: em vez de `_movies` e `_tv`, você pode ter `_tecnologia` e `_ciencia`), ajuste as regras para que o conteúdo seja corretamente categorizado.

*   **`app/tags.py`**
    *   **Caminho do arquivo:** `e:\Área de Trabalho 2\Portal The News\Nerd\TheNews_MaquinaNerd\app\tags.py`
    *   **O que fazer:** Adapte as heurísticas e as listas de apoio usadas para extrair tags do conteúdo original. Se o novo nicho tiver termos-chave, franquias, nomes de pessoas ou entidades específicas, você precisará atualizar as regras para que as tags geradas sejam relevantes.

### 5. Outros Módulos (Revisão Opcional)

Embora os módulos abaixo geralmente não exijam alterações para uma simples reutilização, uma análise rápida pode ser útil se o seu novo nicho tiver requisitos muito específicos:

*   **`app/extractor.py`**: Se as páginas dos artigos do seu novo nicho tiverem estruturas HTML muito diferentes ou usarem tecnologias incomuns, talvez seja necessário ajustar a lógica de extração de conteúdo (trafilatura, BeautifulSoup).
*   **`app/rewriter.py`**: A lógica de validação e sanitização da saída da IA é genérica, mas se a IA começar a gerar HTML inesperado para o seu novo nicho, este é o lugar para ajustar as regras.
*   **`app/media.py`**: A política de imagens (`hotlink` vs. `download_upload`) é configurável via `PIPELINE_CONFIG`. Se você precisar de um tratamento de imagem mais complexo ou integração com outro serviço de mídia, este módulo seria o ponto de partida.
*   **`app/wordpress.py`**: O cliente da API REST do WordPress é bastante genérico. Apenas se o seu WordPress tiver plugins muito específicos que alterem o comportamento padrão da API (ex: campos Yoast customizados que não são os padrões), você precisaria revisar este módulo.

### 6. Instalação e Execução

Para colocar o novo projeto para rodar em um novo ambiente:

1.  **Clone o repositório** (ou copie a pasta do projeto).
2.  **Crie um Ambiente Virtual:**
    ```bash
    python -m venv venv
    ```
3.  **Ative o Ambiente Virtual:**
    *   No Windows: `venv\Scripts\activate`
    *   No Linux/macOS: `source venv/bin/activate`
4.  **Instale as Dependências:**
    ```bash
    pip install -r requirements.txt
    ```
5.  **Execute a Aplicação:**
    ```bash
    python main.py
    ```
    Ou utilize o script de inicialização, se houver (`StartMN.bat`), adaptando-o se necessário para o novo ambiente.

Ao seguir este guia, você poderá adaptar o seu pipeline de conteúdo para atender às necessidades de qualquer novo projeto, mantendo a robustez e a automação que o sistema oferece.