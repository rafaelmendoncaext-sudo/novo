"""
Popula o banco SQLite local (pedidos.db) a partir de pedidos.xlsx.
Execute uma única vez:  python seed_db.py
"""
import os
import sqlite3
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(BASE, "pedidos.xlsx")
DB_PATH   = os.path.join(BASE, "pedidos.db")


def seed():
    print(f"Lendo {XLSX_PATH} ...")
    df = pd.read_excel(XLSX_PATH, dtype=str)
    df = df.where(pd.notna(df), None)          # NaN → None (NULL no SQLite)
    print(f"  {len(df):,} linhas / {len(df.columns)} colunas")

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

    print("Inserindo registros...")
    cur.executemany(
        "INSERT INTO pedidos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        df.itertuples(index=False, name=None),
    )

    print("Criando índice em NR_BENEFICIARIO...")
    cur.execute("CREATE INDEX idx_beneficiario ON pedidos(NR_BENEFICIARIO)")

    conn.commit()
    conn.close()
    print(f"Banco criado: {DB_PATH}  ({os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    seed()
