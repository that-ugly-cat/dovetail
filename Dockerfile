FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .
RUN pip install --no-cache-dir --no-deps -e .

RUN mkdir -p data

# The schema is brought up to date at boot, not by hand: a container that starts
# against an older database and serves anyway is how a missing column becomes a
# 500 in front of someone.
CMD ["sh", "-c", "alembic upgrade head && uvicorn dovetail.web:app --host 0.0.0.0 --port 8021"]
