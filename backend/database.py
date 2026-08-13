import sqlite3
import os
from datetime import datetime

DB_PATH = "descontos_jardins_sky.db"

def init_db():
    """Inicializa o banco de dados com as tabelas necessárias"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tabela de Clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cpf TEXT UNIQUE NOT NULL,
            nome TEXT NOT NULL,
            ocupacao TEXT NOT NULL,
            tel TEXT NOT NULL,
            endereco TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            desconto_tipo TEXT NOT NULL,
            desconto_valor REAL NOT NULL,
            status TEXT DEFAULT 'ativo',
            confirmado BOOLEAN DEFAULT 0,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabela de Cupons (ATUALIZADA)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER,
            qrcode TEXT UNIQUE NOT NULL,
            data_geracao DATE NOT NULL,
            data_uso DATE,
            timestamp_uso TIMESTAMP,
            turno TEXT,
            poster_id TEXT,
            foto_url TEXT,
            status TEXT DEFAULT 'pendente',
            FOREIGN KEY (cliente_id) REFERENCES clientes(id),
            FOREIGN KEY (produto_id) REFERENCES produtos(id)
        )
    ''')

    # Tabela de Abastecimentos (ATUALIZADA)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS abastecimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cupom_id INTEGER,
            cliente_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            poster_id TEXT NOT NULL,
            data DATE NOT NULL,
            hora TIME NOT NULL,
            turno TEXT NOT NULL,
            quantidade REAL NOT NULL,
            valor_original REAL NOT NULL,
            valor_desconto REAL NOT NULL,
            valor_final REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cupom_id) REFERENCES cupons(id),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id),
            FOREIGN KEY (produto_id) REFERENCES produtos(id)
        )
    ''')

    # Tabela de Logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acao TEXT NOT NULL,
            usuario_id INTEGER,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            descricao TEXT,
            ip_address TEXT
        )
    ''')

    # Tabela de Admin (para gerenciar acessos)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            poster_id TEXT NOT NULL,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabela de Produtos (NOVO)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            preco_atual REAL NOT NULL,
            unidade TEXT NOT NULL,
            icone TEXT,
            ativo BOOLEAN DEFAULT 1,
            data_atualizacao TIMESTAMP,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Inserir produtos padrão se não existirem
    produtos_padrao = [
        (1, 'Gasolina Comum', 'combustivel', 5.89, 'L', '⛽'),
        (2, 'Gasolina Premium', 'combustivel', 6.49, 'L', '⛽'),
        (3, 'Etanol Comum', 'combustivel', 3.89, 'L', '🌱'),
        (4, 'Diesel S10', 'combustivel', 6.19, 'L', '🚛'),
        (5, 'Diesel Premium', 'combustivel', 6.59, 'L', '🚛'),
        (6, 'Óleo Sintético 5W30', 'oleo', 85.00, 'L', '🛢️'),
        (7, 'Óleo Semissintético 5W40', 'oleo', 55.00, 'L', '🛢️'),
        (8, 'Óleo Mineral 20W50', 'oleo', 35.00, 'L', '🛢️'),
        (9, 'Óleo Hidráulico', 'oleo', 45.00, 'L', '🛢️'),
    ]

    for p in produtos_padrao:
        cursor.execute('''
            INSERT OR IGNORE INTO produtos
            (id, nome, tipo, preco_atual, unidade, icone)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', p)

    conn.commit()
    conn.close()
    print("✅ Database inicializado com sucesso!")
    print("✅ Produtos padrão inseridos!")

def get_db():
    """Retorna conexão com o banco de dados"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

if __name__ == "__main__":
    init_db()
