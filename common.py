"""
common.py
=========
Configuração compartilhada do fluxo de Autorizações da Carol (Unimed Nacional).

Diferente da versão original (que usava OpenRouter/Gemma via langchain_openai),
aqui o modelo é o **Gemini 2.5 Flash servido pelo Vertex AI** do projeto
`poc-inovacao`, autenticado pela service account
`rafel-mendonca_poc-inovacao-869b256d401f.json`.

Variáveis de ambiente reconhecidas (todas opcionais):
    VERTEX_MODEL                 # default: gemini-2.5-flash
    VERTEX_LOCATION              # default: us-central1
    VERTEX_PROJECT               # default: poc-inovacao

Credenciais (ordem de prioridade) — pensado para deploy no Render:
    1. GOOGLE_CREDENTIALS_JSON           # conteúdo do JSON da SA (cole no Render)
    2. GOOGLE_APPLICATION_CREDENTIALS    # caminho de um arquivo já existente
    3. /etc/secrets/<arquivo>            # "Secret File" do Render
    4. o JSON da SA nesta pasta          # fallback para desenvolvimento local
"""

import json
import os
import sqlite3
import tempfile

# Carrega .env se existir (opcional).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from langgraph.checkpoint.sqlite import SqliteSaver


# ---------------------------------------------------------------------------
# Credenciais do Vertex AI (service account do projeto poc-inovacao)
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
_CRED_LOCAL = os.path.join(_DIR, "rafel-mendonca_poc-inovacao-869b256d401f.json")
_SECRET_RENDER = "/etc/secrets/rafel-mendonca_poc-inovacao-869b256d401f.json"


def _resolver_credenciais() -> str | None:
    """Garante que GOOGLE_APPLICATION_CREDENTIALS aponte para um arquivo válido.

    Útil no Render: a forma mais simples é colar o conteúdo do JSON na variável
    GOOGLE_CREDENTIALS_JSON — aqui ele é materializado em um arquivo temporário.
    """
    # 1) JSON inteiro numa variável de ambiente (recomendado no Render).
    bruto = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if bruto:
        try:
            json.loads(bruto)  # valida
        except json.JSONDecodeError as e:
            raise RuntimeError("GOOGLE_CREDENTIALS_JSON não é um JSON válido") from e
        caminho = os.path.join(tempfile.gettempdir(), "vertex_sa.json")
        with open(caminho, "w") as f:
            f.write(bruto)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = caminho
        return caminho

    # 2) Caminho já definido e existente.
    atual = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if atual and os.path.exists(atual):
        return atual

    # 3) Secret File do Render.
    if os.path.exists(_SECRET_RENDER):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _SECRET_RENDER
        return _SECRET_RENDER

    # 4) Arquivo local (desenvolvimento).
    if os.path.exists(_CRED_LOCAL):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _CRED_LOCAL
        return _CRED_LOCAL

    return None


_CRED_ATIVA = _resolver_credenciais()

PROJETO = os.environ.get("VERTEX_PROJECT", "poc-inovacao")
LOCALIZACAO = os.environ.get("VERTEX_LOCATION", "us-central1")
MODELO = os.environ.get("VERTEX_MODEL", "gemini-2.5-flash")


# ---------------------------------------------------------------------------
# Compatibilidade do checkpointer SQLite (mesmo shim do projeto original)
# ---------------------------------------------------------------------------
# Em versões recentes do `langgraph-checkpoint-sqlite`,
# `SqliteSaver.from_conn_string` virou um *context manager*, o que quebra o
# `compile(checkpointer=...)`. Normalizamos para devolver um saver utilizável.
def _from_conn_string(conn_string: str = ":memory:") -> SqliteSaver:
    conn = sqlite3.connect(conn_string, check_same_thread=False)
    saver = SqliteSaver(conn)
    try:
        saver.setup()
    except Exception:
        pass
    return saver


SqliteSaver.from_conn_string = staticmethod(_from_conn_string)


# ---------------------------------------------------------------------------
# Modelo compartilhado — Gemini 2.5 Flash (Vertex AI)
# ---------------------------------------------------------------------------
from langchain_google_vertexai import ChatVertexAI

# max_retries=1 → 1 tentativa, sem retry: falha rápida (0.2s) quando a SA key é inválida.
# No Render com key válida, set LLM_MAX_RETRIES=3 para resiliência contra erros 5xx.
_LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))

model = ChatVertexAI(
    model=MODELO,
    project=PROJETO,
    location=LOCALIZACAO,
    temperature=0,
    max_retries=_LLM_MAX_RETRIES,
)


def rodar(graph, entrada: dict, thread_id: str = "1"):
    """Executa um grafo em streaming (igual ao loop do projeto original)."""
    thread = {"configurable": {"thread_id": thread_id}}
    s = None
    for s in graph.stream(entrada, thread):
        print("#" * 79)
        print(s)
    return s
