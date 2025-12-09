@echo off
echo "Ativando o ambiente virtual..."
call venv\Scripts\activate
echo "Iniciando o programa..."
python -m app.main
pause