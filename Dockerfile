# Imagem unica que roda os tres niveis. A versao do Python e fixada porque a solucao foi
# desenvolvida e executada em 3.12 - o pacote mcp 2.x exige >=3.10.
FROM python:3.12-slim

# Evita .pyc no volume e deixa o log sair na hora (util quando o lote demora e o
# usuario esta olhando o terminal).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias primeiro, em camada propria: o cache so e invalidado quando o
# requirements muda, nao a cada alteracao de codigo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# A chave NUNCA entra na imagem. Ela e injetada em runtime, via --env-file ou -e
# (ver README). Por isso .env esta no .dockerignore.
ENV PYTHONPATH=/app/nivel_2

# Sem CMD "esperto": o container e um ambiente de execucao, e cada nivel e um
# comando diferente. O default abre o Jupyter, que e o entregavel do Nivel 1.
EXPOSE 8888
CMD ["jupyter", "notebook", "--ip=0.0.0.0", "--port=8888", "--no-browser", \
     "--allow-root", "--NotebookApp.token=", "--NotebookApp.password="]
