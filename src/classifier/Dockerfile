FROM python:3.12-slim
WORKDIR /service
COPY src/classifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /contracts
COPY database/seeds/reference/complaint_routing/dongjak_*.csv /seeds/reference/complaint_routing/
COPY src/classifier/app ./app
ENV CONTRACTS_DIR=/contracts PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
