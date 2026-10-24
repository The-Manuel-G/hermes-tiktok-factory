FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir pyyaml playwright && playwright install --with-deps chromium

COPY . /app
EXPOSE 8085
CMD ["python", "dashboard/dashboard_service.py"]
