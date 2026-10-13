FROM python:3.12-slim-bookworm
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && playwright install --with-deps chromium
RUN useradd --create-home scraper && mkdir -p /app/data /app/exports \
    && chown -R scraper:scraper /app && chmod -R a+rX /ms-playwright
USER scraper
ENTRYPOINT ["automobile-scraper"]
CMD ["--help"]
