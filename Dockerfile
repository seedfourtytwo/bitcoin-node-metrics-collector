# Bitcoin node metrics collector: a Prometheus exporter for Bitcoin Core and Knots.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/

# Run as the user that can read the nodes' cookie files (set with `user:` in compose).
ENV PYTHONUNBUFFERED=1
EXPOSE 9332
CMD ["python", "src/collector.py"]
