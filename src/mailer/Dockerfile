FROM python:3.12-slim
WORKDIR /service
COPY src/mailer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /contracts
COPY database/seeds/mock_contacts.csv /seeds/mock_contacts.csv
ENV CONTACTS_CSV=/seeds/mock_contacts.csv
COPY src/mailer/app ./app
ENV CONTRACTS_DIR=/contracts PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
