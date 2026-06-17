"""
Acesso ao banco SQLite de pedidos (pedidos.db).
"""
import os
import sqlite3
from typing import List

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pedidos.db")


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
