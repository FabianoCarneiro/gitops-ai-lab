#!/usr/bin/env python3
"""embedding-api: serviço mínimo de embeddings + busca vetorial (somente biblioteca padrão).

Propósito didático: o comportamento do serviço é controlado 100% por variáveis de
ambiente declaradas no Git (ConfigMap). Mudou a MODEL_VERSION no Git -> o Argo CD
sincroniza -> o pod reinicia -> o serviço passa a usar outro "modelo" e outra
coleção no Qdrant.

O "modelo" aqui é um embedding determinístico baseado em hash (sem download de
pesos), o que mantém o laboratório leve e reprodutível. Em produção, a mesma
variável apontaria para uma versão no model registry (MLflow / Hugging Face).

Endpoints:
  GET  /healthz            liveness/readiness
  GET  /info               versão do modelo, dimensão, coleção, Qdrant
  POST /index              {"docs": [{"id": 1, "text": "..."}]}  -> upsert no Qdrant
  GET  /search?q=...&k=3   busca semântica (aproximada) na coleção da versão ativa
"""
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_VERSION = os.getenv("MODEL_VERSION", "v1")
EMBED_DIM = int(os.getenv("EMBED_DIM", "64"))
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.qdrant.svc.cluster.local:6333").rstrip("/")
COLLECTION_PREFIX = os.getenv("COLLECTION_PREFIX", "docs")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
PORT = int(os.getenv("PORT", "8080"))

# Uma coleção por versão de modelo: vetores de modelos diferentes NÃO são comparáveis.
COLLECTION = f"{COLLECTION_PREFIX}-{MODEL_VERSION}"

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def embed(text: str) -> list:
    """Embedding determinístico (bag-of-words hasheado), normalizado em L2.

    A MODEL_VERSION entra no hash: trocar a versão muda o espaço vetorial,
    exatamente como acontece ao trocar o modelo de embeddings de verdade.
    """
    vec = [0.0] * EMBED_DIM
    for tok in TOKEN_RE.findall(text.lower()):
        h = hashlib.sha256(f"{MODEL_VERSION}:{tok}".encode()).digest()
        idx = int.from_bytes(h[:4], "big") % EMBED_DIM
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def qdrant(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        QDRANT_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read() or b"{}")


def ensure_collection():
    try:
        qdrant("GET", f"/collections/{COLLECTION}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        qdrant("PUT", f"/collections/{COLLECTION}",
               {"vectors": {"size": EMBED_DIM, "distance": "Cosine"}})


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):  # logs simples no stdout (kubectl logs)
        print(f"[{ENVIRONMENT}] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if url.path == "/info":
                return self._send(200, {
                    "environment": ENVIRONMENT,
                    "model_version": MODEL_VERSION,
                    "embed_dim": EMBED_DIM,
                    "collection": COLLECTION,
                    "qdrant_url": QDRANT_URL,
                })
            if url.path == "/search":
                q = (qs.get("q") or [""])[0]
                k = int((qs.get("k") or ["3"])[0])
                if not q:
                    return self._send(400, {"error": "parâmetro q é obrigatório"})
                ensure_collection()
                res = qdrant("POST", f"/collections/{COLLECTION}/points/search",
                             {"vector": embed(q), "limit": k, "with_payload": True})
                hits = [{"id": r["id"], "score": round(r["score"], 4),
                         "text": r.get("payload", {}).get("text")} for r in res.get("result", [])]
                return self._send(200, {"model_version": MODEL_VERSION,
                                        "collection": COLLECTION, "hits": hits})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 - didático: devolve o erro
            return self._send(502, {"error": str(e), "collection": COLLECTION})

    def do_POST(self):
        if self.path != "/index":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            docs = body.get("docs", [])
            ensure_collection()
            points = [{"id": d["id"], "vector": embed(d["text"]), "payload": {"text": d["text"]}}
                      for d in docs]
            qdrant("PUT", f"/collections/{COLLECTION}/points?wait=true", {"points": points})
            return self._send(200, {"indexed": len(points), "collection": COLLECTION,
                                    "model_version": MODEL_VERSION})
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": str(e), "collection": COLLECTION})


if __name__ == "__main__":
    print(f"embedding-api model={MODEL_VERSION} collection={COLLECTION} "
          f"qdrant={QDRANT_URL} env={ENVIRONMENT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
