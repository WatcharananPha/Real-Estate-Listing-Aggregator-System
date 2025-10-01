FROM mcr.microsoft.com/devcontainers/python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    jq \
    libglib2.0-0 \
    libnss3 \
    libfontconfig1 \
    --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome Stable (official repo)
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Fetch a matching ChromeDriver (same major as installed Chrome)
# Uses the Chrome for Testing "known-good-versions-with-downloads" index
RUN set -eux; \
    CHROME_VERSION="$(google-chrome --version | awk '{print $3}')" ; \
    MAJOR="${CHROME_VERSION%%.*}" ; \
    JSON_URL="https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" ; \
    DRIVER_URL="$(wget -qO- "${JSON_URL}" \
      | jq -r --arg m "${MAJOR}" '(.versions | map(select(.version|startswith($m+"."))) | sort_by(.version))[-1].downloads.chromedriver[] | select(.platform=="linux64") | .url')" ; \
    wget -q -P /tmp "${DRIVER_URL}" ; \
    unzip -q /tmp/chromedriver-linux64.zip -d /usr/local/bin/ ; \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver ; \
    rm -rf /usr/local/bin/chromedriver-linux64 /tmp/chromedriver-linux64.zip ; \
    chmod +x /usr/local/bin/chromedriver

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY . .

CMD ["python", "scraping-postURL.py"]
