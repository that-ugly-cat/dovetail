"""Environment the app reads at import time.

`PUBLIC_URL` is one of them, and it matters here for a reason worth stating: the
MCP transport checks the request's Host and Origin against it and answers **421
Misdirected Request** to anything else. The test client calls itself
`testserver`, so without this line the suite fails with a status nobody
associates with a misconfigured URL — which is exactly how this fails in
production too.
"""

import os

os.environ.setdefault("PUBLIC_URL", "http://testserver")
os.environ.setdefault("JWT_SECRET", "test-secret")
