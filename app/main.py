import argparse
import logging
import sys
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler

from app.pipeline import run_pipeline_cycle
from app.store import Database
from app.config import SCHEDULE_CONFIG

# Configura o logging para exibir informações no terminal e salvar em um arquivo
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/app.log", mode='a', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

def initialize_database():
    """Inicializa o banco de dados e garante que as tabelas sejam criadas."""
    logger.info("Verificando o esquema do banco de dados...")
    try:
        db = Database()
        db.initialize()  # Garante que as tabelas sejam criadas
        db.close()
        logger.info("Verificação do banco de dados concluída com sucesso.")
    except Exception as e:
        logger.critical(f"Falha ao inicializar o banco de dados: {e}", exc_info=True)
        sys.exit(1)

def main():
    """Função principal para executar o pipeline de conteúdo."""
    parser = argparse.ArgumentParser(description="Executa o pipeline de conteúdo VocMoney.")
    parser.add_argument(
        '--once',
        action='store_true',
        help="Executa o ciclo do pipeline uma vez e sai."
    )
    args = parser.parse_args()

    initialize_database()

    if args.once:
        logger.info("Executando um único ciclo do pipeline (--once).")
        try:
            run_pipeline_cycle()
        except Exception as e:
            logger.critical(f"Erro crítico durante a execução do ciclo único: {e}", exc_info=True)
        finally:
            logger.info("Ciclo único finalizado.")
    else:
        # Agenda as execuções futuras
        interval = SCHEDULE_CONFIG.get('check_interval_minutes', 15)
        logger.info(f"Agendador iniciado. O pipeline será executado a cada {interval} minutos entre 9h-19h (horário de Brasília).")

        # Executa imediatamente ao iniciar
        logger.info("🚀 Executando primeira verificação imediatamente...")
        try:
            run_pipeline_cycle()
        except Exception as e:
            logger.error(f"Erro na execução inicial do pipeline: {e}", exc_info=True)

        scheduler = BlockingScheduler(timezone='America/Sao_Paulo')

        # Executa a cada `interval` minutos, apenas entre 9h-19h horário de Brasília
        # cron: minute='*' = a cada minuto, hour='9-18' = 9h até 18h:59 (antes de 19h)
        scheduler.add_job(
            run_pipeline_cycle, 
            'cron',
            minute=f'*/{ interval}',  # A cada N minutos
            hour='9-18',  # 9h até 18h:59 (horário de Brasília)
            timezone='America/Sao_Paulo'
        )

        logger.info("Pressione Ctrl+C para sair.")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Agendador interrompido pelo usuário.")

if __name__ == "__main__":
    main()
