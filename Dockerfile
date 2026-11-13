FROM python:3.12-slim
WORKDIR /app
COPY leilao ./leilao
COPY servidor.py .
RUN useradd --create-home app
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude')"
CMD ["python", "servidor.py", "--host", "0.0.0.0", "--porta", "8000"]
