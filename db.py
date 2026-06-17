"""
Acesso ao banco SQLite de pedidos (pedidos.db).
"""
import os
import sqlite3
import sys
from typing import List

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(_DIR, "pedidos.db")
XLSX_PATH = os.path.join(_DIR, "pedidos.xlsx")


def _seed_agora():
    """Cria pedidos.db a partir de pedidos.xlsx quando o arquivo está ausente."""
    import pandas as pd
    print("db.py: pedidos.db ausente — criando a partir de pedidos.xlsx ...", flush=True)
    df = pd.read_excel(XLSX_PATH, dtype=str)
    df = df.where(pd.notna(df), None)
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS pedidos")
    cur.execute("""
        CREATE TABLE pedidos (
            NUM_PEDIDO          TEXT,
            NUM_SEQ_PEDIDO      TEXT,
            NOM_SITUACAO        TEXT,
            NR_BENEFICIARIO     TEXT,
            ITEM_MEDICO         TEXT,
            NOME_ITEM           TEXT,
            DS_SITUACAOITEM     TEXT,
            DS_SITUACAOESPECIAL TEXT,
            COD_PRESTADOR_EXEC  TEXT,
            NOME_PRESTADOR      TEXT,
            TXT_OBS_EMISSAO     TEXT,
            TXT_OBS_OPERADORA   TEXT
        )
    """)
    cur.executemany(
        "INSERT INTO pedidos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        df.itertuples(index=False, name=None),
    )
    cur.execute("CREATE INDEX idx_beneficiario ON pedidos(NR_BENEFICIARIO)")
    conn.commit()
    conn.close()
    print(f"db.py: pedidos.db criado ({os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB).", flush=True)


def _garantir_db():
    """Garante que pedidos.db existe e tem a tabela pedidos."""
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.execute("SELECT 1 FROM pedidos LIMIT 1")
    except sqlite3.OperationalError:
        if os.path.exists(XLSX_PATH):
            _seed_agora()
        else:
            raise RuntimeError(
                f"pedidos.db ausente e pedidos.xlsx não encontrado em {_DIR}. "
                "Inclua pedidos.xlsx no repositório ou rode seed_db.py manualmente."
            )


_garantir_db()


def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def beneficiario_existe(nr: str) -> bool:
    """Retorna True se o NR_BENEFICIARIO constar em qualquer registro."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM pedidos WHERE NR_BENEFICIARIO = ? LIMIT 1", (nr,)
        ).fetchone()
    return row is not None


def get_autorizacoes(nr: str) -> List[dict]:
    """Retorna um registro por NUM_PEDIDO (último item) para exibição no chat."""
    sql = """
        SELECT
            NUM_PEDIDO,
            NOM_SITUACAO,
            NOME_ITEM,
            DS_SITUACAOITEM,
            NOME_PRESTADOR
        FROM pedidos
        WHERE NR_BENEFICIARIO = ?
        GROUP BY NUM_PEDIDO          -- um pedido por linha no menu
        ORDER BY NUM_PEDIDO DESC
        LIMIT 10                     -- evita listas gigantes no WhatsApp
    """
    with _conn() as con:
        cur = con.execute(sql, (nr,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_pedidos_resumo(nr: str) -> List[dict]:
    """Retorna todos os pedidos agrupados por NUM_PEDIDO com contagem de itens."""
    sql = """
        SELECT
            NUM_PEDIDO,
            NOM_SITUACAO,
            COUNT(*) AS QTD_ITENS,
            NOME_PRESTADOR
        FROM pedidos
        WHERE NR_BENEFICIARIO = ?
        GROUP BY NUM_PEDIDO
        ORDER BY NUM_PEDIDO DESC
        LIMIT 10
    """
    with _conn() as con:
        cur = con.execute(sql, (nr,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
