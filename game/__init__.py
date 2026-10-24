import os

import certifi

# python.org's macOS Python ships without root certificates; use certifi's. Set before anything
# imports aiohttp, which builds its default SSL context at import time.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
