# WasteStack — imagem única para a aplicação local, o pipeline e os testes.
# Os dados (data/) não entram na imagem: são montados como volume pelo docker-compose.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WASTESTACK_HOST=0.0.0.0 \
    WASTESTACK_PORTA=8156

WORKDIR /app

COPY requirements-app.txt .
RUN pip install -r requirements-app.txt

COPY src/ src/
COPY app/ app/
COPY config/ config/
COPY pipeline/ pipeline/
COPY notebooks/ notebooks/
COPY tests/ tests/
COPY docs/ docs/

# Usuário sem privilégios; data/ e notebooks/ chegam por volume.
RUN useradd --create-home --uid 1000 wastestack && mkdir -p data && chown -R wastestack /app
USER wastestack

EXPOSE 8156
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8156/api/saude', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app.servidor"]
