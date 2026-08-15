"""Camada de banco de dados — funciona com Postgres (produção) ou SQLite (local).

Se a variável de ambiente DATABASE_URL existir, usa Postgres.
Caso contrário, usa um arquivo SQLite local — assim o app continua rodando
na sua máquina sem precisar instalar nada.

O resto do código não muda: continua usando "?" como placeholder e
acessando as colunas pelo nome (row['nome']).
"""

import os
import sqlite3
from datetime import date, datetime, time

DB_PATH = "descontos_jardins_sky.db"
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
USANDO_POSTGRES = bool(DATABASE_URL)

if USANDO_POSTGRES:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:
        print(f"⚠️  psycopg2 não disponível ({e}). Usando SQLite.")
        DATABASE_URL = ''
        USANDO_POSTGRES = False


# ==================== ADAPTADORES ====================

def _normalizar_params(params):
    """Converte date/time/datetime em texto ISO (as colunas de data são TEXT)."""
    if params is None:
        return None

    def conv(v):
        if isinstance(v, datetime):
            return v.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(v, date):
            return v.strftime('%Y-%m-%d')
        if isinstance(v, time):
            return v.strftime('%H:%M:%S')
        return v

    if isinstance(params, dict):
        return {k: conv(v) for k, v in params.items()}
    return [conv(v) for v in params]


class CursorPostgres:
    """Deixa o psycopg2 com a mesma cara do sqlite3 usado no projeto."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    def execute(self, sql, params=()):
        sql = sql.replace('?', '%s')
        precisa_id = (
            sql.lstrip().upper().startswith('INSERT')
            and 'RETURNING' not in sql.upper()
        )
        if precisa_id:
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'

        self._cursor.execute(sql, _normalizar_params(params))

        if precisa_id:
            try:
                linha = self._cursor.fetchone()
                self._lastrowid = linha['id'] if linha else None
            except Exception:
                self._lastrowid = None
        return self

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self._cursor.close()


class ConexaoPostgres:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return CursorPostgres(
            self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        )

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur


def get_db():
    """Retorna uma conexão pronta para uso (Postgres ou SQLite)."""
    if USANDO_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return ConexaoPostgres(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ==================== ESQUEMA ====================
# Datas ficam como TEXT (ISO) nos dois bancos, para o código tratar tudo igual.

def _schema(pg):
    serial = 'SERIAL PRIMARY KEY' if pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    real = 'DOUBLE PRECISION' if pg else 'REAL'
    agora = 'CURRENT_TIMESTAMP'

    return [
        f'''CREATE TABLE IF NOT EXISTS clientes (
            id {serial},
            cpf TEXT UNIQUE NOT NULL,
            nome TEXT NOT NULL,
            ocupacao TEXT NOT NULL,
            tel TEXT NOT NULL,
            endereco TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            desconto_tipo TEXT NOT NULL,
            desconto_valor {real} NOT NULL,
            status TEXT DEFAULT 'ativo',
            confirmado INTEGER DEFAULT 0,
            data_criacao TIMESTAMP DEFAULT {agora},
            data_atualizacao TIMESTAMP DEFAULT {agora}
        )''',

        f'''CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            preco_atual {real} NOT NULL,
            unidade TEXT NOT NULL,
            icone TEXT,
            ativo INTEGER DEFAULT 1,
            desconto_valor {real} DEFAULT 0,
            desconto_tipo TEXT DEFAULT 'fixo',
            limite_litros {real} DEFAULT 50,
            data_atualizacao TEXT,
            criado_em TIMESTAMP DEFAULT {agora}
        )''',

        f'''CREATE TABLE IF NOT EXISTS cupons (
            id {serial},
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER,
            qrcode TEXT UNIQUE NOT NULL,
            data_geracao TEXT NOT NULL,
            data_uso TEXT,
            timestamp_uso TEXT,
            turno TEXT,
            poster_id TEXT,
            foto_url TEXT,
            status TEXT DEFAULT 'pendente',
            quantidade_permitida {real} DEFAULT 50,
            quantidade_utilizada {real} DEFAULT 0,
            data_ultimo_uso TEXT,
            turno_ultimo_uso TEXT,
            preco_unitario {real} DEFAULT 0,
            desconto_unitario {real} DEFAULT 0,
            desconto_valor {real} DEFAULT 0,
            desconto_tipo TEXT DEFAULT 'fixo'
        )''',

        f'''CREATE TABLE IF NOT EXISTS abastecimentos (
            id {serial},
            cupom_id INTEGER,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            poster_id TEXT NOT NULL,
            data TEXT NOT NULL,
            hora TEXT NOT NULL,
            turno TEXT NOT NULL,
            quantidade {real} NOT NULL,
            valor_original {real} NOT NULL,
            valor_desconto {real} NOT NULL,
            valor_final {real} NOT NULL,
            timestamp TIMESTAMP DEFAULT {agora}
        )''',

        f'''CREATE TABLE IF NOT EXISTS logs (
            id {serial},
            acao TEXT NOT NULL,
            usuario_id INTEGER,
            data TIMESTAMP DEFAULT {agora},
            descricao TEXT,
            ip_address TEXT
        )''',

        f'''CREATE TABLE IF NOT EXISTS admin (
            id {serial},
            usuario TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            poster_id TEXT NOT NULL,
            token TEXT,
            token_expira TEXT,
            nivel TEXT DEFAULT 'master',
            nome TEXT,
            ativo INTEGER DEFAULT 1,
            data_criacao TIMESTAMP DEFAULT {agora}
        )''',
    ]


PRODUTOS_PADRAO = [
    (1, 'Gasolina Comum', 'combustivel', 5.89, 'L', '⛽'),
    (2, 'Gasolina Premium', 'combustivel', 6.49, 'L', '⛽'),
    (3, 'Etanol Comum', 'combustivel', 3.89, 'L', '🌱'),
    (4, 'Diesel S10', 'combustivel', 6.19, 'L', '🚛'),
    (5, 'Gasolina Aditivada', 'combustivel', 6.59, 'L', '⛽'),
    (6, 'Óleo Sintético 5W30', 'oleo', 85.00, 'L', '🛢️'),
    (7, 'Óleo Semissintético 5W40', 'oleo', 55.00, 'L', '🛢️'),
    (8, 'Óleo Mineral 20W50', 'oleo', 35.00, 'L', '🛢️'),
    (9, 'Óleo Hidráulico', 'oleo', 45.00, 'L', '🛢️'),
]

# Colunas acrescentadas depois — aplicadas em bancos que já existem
COLUNAS_NOVAS = {
    'cupons': [
        ('quantidade_permitida', 'DOUBLE PRECISION DEFAULT 50', 'REAL DEFAULT 50'),
        ('quantidade_utilizada', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('data_ultimo_uso', 'TEXT', 'TEXT'),
        ('turno_ultimo_uso', 'TEXT', 'TEXT'),
        # preço e desconto congelados no momento da geração
        ('preco_unitario', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('desconto_unitario', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('desconto_valor', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('desconto_tipo', "TEXT DEFAULT 'fixo'", "TEXT DEFAULT 'fixo'"),
    ],
    'produtos': [
        ('desconto_valor', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('desconto_tipo', "TEXT DEFAULT 'fixo'", "TEXT DEFAULT 'fixo'"),
        ('limite_litros', 'DOUBLE PRECISION DEFAULT 50', 'REAL DEFAULT 50'),
    ],
    'admin': [
        ('token', 'TEXT', 'TEXT'),
        ('token_expira', 'TEXT', 'TEXT'),
        ('nivel', "TEXT DEFAULT 'master'", "TEXT DEFAULT 'master'"),
        ('nome', 'TEXT', 'TEXT'),
        ('ativo', 'INTEGER DEFAULT 1', 'INTEGER DEFAULT 1'),
    ],
}


def _colunas_existentes(cursor, tabela, pg):
    if pg:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (tabela,)
        )
        return {linha['column_name'] for linha in cursor.fetchall()}

    cursor.execute(f'PRAGMA table_info({tabela})')
    return {linha[1] for linha in cursor.fetchall()}


def init_db():
    """Cria as tabelas e aplica migrações. Seguro rodar várias vezes."""
    pg = USANDO_POSTGRES
    conn = get_db()
    cursor = conn.cursor()

    for comando in _schema(pg):
        cursor.execute(comando)

    for p in PRODUTOS_PADRAO:
        if pg:
            cursor.execute('''
                INSERT INTO produtos (id, nome, tipo, preco_atual, unidade, icone)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
            ''', p)
        else:
            cursor.execute('''
                INSERT OR IGNORE INTO produtos
                (id, nome, tipo, preco_atual, unidade, icone)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', p)

    # Renomeações de produto (mantém o histórico de abastecimentos ligado ao mesmo id)
    renomear = [
        (5, 'Diesel Premium', 'Gasolina Aditivada', '⛽'),
    ]
    for pid, nome_antigo, nome_novo, icone in renomear:
        cursor.execute('SELECT nome FROM produtos WHERE id = ?', (pid,))
        atual = cursor.fetchone()
        if atual and atual['nome'] == nome_antigo:
            cursor.execute('UPDATE produtos SET nome = ?, icone = ? WHERE id = ?',
                           (nome_novo, icone, pid))
            print(f"🔧 Produto {pid}: '{nome_antigo}' renomeado para '{nome_novo}'")

    for tabela, colunas in COLUNAS_NOVAS.items():
        existentes = _colunas_existentes(cursor, tabela, pg)
        for nome, tipo_pg, tipo_sqlite in colunas:
            if nome not in existentes:
                cursor.execute(
                    f'ALTER TABLE {tabela} ADD COLUMN {nome} {tipo_pg if pg else tipo_sqlite}'
                )
                print(f"🔧 Migração: coluna {tabela}.{nome} adicionada")

    conn.commit()
    conn.close()

    print(f"✅ Banco inicializado ({'PostgreSQL' if pg else 'SQLite local'})")


if __name__ == "__main__":
    init_db()
