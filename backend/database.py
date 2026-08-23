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

# Em produção quem manda é o DATABASE_URL (PostgreSQL do Render). Este
# caminho é só o banco local de desenvolvimento — e fica configurável para
# os testes rodarem num arquivo temporário, sem risco de encostar no banco
# de trabalho de quem está mexendo no código.
DB_PATH = os.environ.get("DATABASE_PATH", "descontos_jardins_sky.db")
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
            aceita_promocoes INTEGER DEFAULT 0,
            data_consentimento TEXT,
            aceita_parceiros INTEGER DEFAULT 0,
            data_consentimento_parceiros TEXT,
            placa TEXT,
            data_placa TEXT,
            registro_tipo TEXT,
            registro_numero TEXT,
            empresa_convenio TEXT,
            foto_comprovante TEXT,
            foto_comprovante_tipo TEXT,
            data_foto_comprovante TEXT,
            reset_token_hash TEXT,
            reset_expira TEXT,
            reset_pedido_em TEXT,
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
            preco_custo {real} DEFAULT 0,
            margem_minima {real} DEFAULT 10,
            desconto_valor {real} DEFAULT 0,
            desconto_tipo TEXT DEFAULT 'fixo',
            limite_litros {real} DEFAULT 50,
            data_atualizacao TEXT,
            criado_em TIMESTAMP DEFAULT {agora}
        )''',

        f'''CREATE TABLE IF NOT EXISTS auditoria (
            id {serial},
            data_hora TEXT NOT NULL,
            admin_id INTEGER,
            admin_usuario TEXT,
            admin_nivel TEXT,
            acao TEXT NOT NULL,
            produto_id INTEGER,
            produto_nome TEXT,
            campo TEXT,
            valor_anterior TEXT,
            valor_novo TEXT,
            detalhe TEXT
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
            desconto_tipo TEXT DEFAULT 'fixo',
            categoria TEXT,
            liberacao_id INTEGER,
            trocado_por INTEGER,
            data_cancelamento TEXT
        )''',

        # Fechamento de turno do frentista.
        #
        # O turno NÃO é definido pelo relógio: é tudo o que aquele frentista
        # registrou desde o fechamento anterior dele até fechar de novo. Quem
        # entra 5h e sai 13h30 tem um relatório só, em vez de ter o movimento
        # partido em dois pela virada das 14h.
        #
        # Como isso é amarrado: cada abastecimento recebe o `fechamento_id`
        # no momento em que o turno fecha. O turno aberto é simplesmente
        # "meus abastecimentos ainda sem fechamento" — não depende de comparar
        # horários, então relógio errado ou fuso trocado não bagunçam a conta.
        f'''CREATE TABLE IF NOT EXISTS fechamentos_turno (
            id {serial},
            usuario TEXT NOT NULL,
            nome TEXT,
            poster_id TEXT,
            aberto_em TEXT,
            fechado_em TEXT NOT NULL,
            total_abastecimentos INTEGER DEFAULT 0,
            total_litros {real} DEFAULT 0,
            total_bruto {real} DEFAULT 0,
            total_desconto {real} DEFAULT 0,
            total_liquido {real} DEFAULT 0
        )''',

        # Liberações extras dadas pelo Master.
        #
        # A regra normal é um cupom de combustível por dia e um de óleo por
        # semana. Quando o Master abre exceção — motorista fez viagem longa,
        # cliente reclamou de algo, o que for — a exceção nasce aqui, com
        # motivo obrigatório e nome de quem liberou.
        #
        # É de uso único: assim que o cliente gera o cupom extra, a liberação
        # é marcada como usada e não vale mais. Sem isso, "liberar uma vez"
        # viraria "liberado para sempre" sem ninguém perceber.
        f'''CREATE TABLE IF NOT EXISTS liberacoes_extras (
            id {serial},
            cliente_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            motivo TEXT NOT NULL,
            liberado_por TEXT NOT NULL,
            data_liberacao TEXT NOT NULL,
            validade TEXT NOT NULL,
            usada INTEGER DEFAULT 0,
            cupom_id INTEGER,
            data_uso TEXT,
            cancelada INTEGER DEFAULT 0,
            cancelada_por TEXT,
            data_cancelamento TEXT
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
            registrado_por TEXT,
            fechamento_id INTEGER,
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
            email TEXT,
            reset_token_hash TEXT,
            reset_expira TEXT,
            reset_pedido_em TEXT,
            ativo INTEGER DEFAULT 1,
            data_criacao TIMESTAMP DEFAULT {agora}
        )''',

        # Empresas que assinaram convênio com os postos.
        #
        # Existe para inverter o ônus da prova: antes o funcionário digitava o
        # nome da empresa e ninguém conferia nada — bastava escrever "Itaú".
        # Agora a gerência cadastra quem de fato fechou contrato, e o
        # funcionário só escolhe de uma lista. Sem convênio assinado, a empresa
        # nem aparece na tela de cadastro.
        f'''CREATE TABLE IF NOT EXISTS empresas_convenio (
            id {serial},
            nome TEXT NOT NULL,
            cnpj TEXT UNIQUE NOT NULL,
            dominio_email TEXT,
            limite_funcionarios INTEGER DEFAULT 0,
            ativo INTEGER DEFAULT 1,
            observacao TEXT,
            criado_por TEXT,
            data_criacao TIMESTAMP DEFAULT {agora}
        )''',

        # Programa de indicação: "traga um amigo, ganhe um cupom".
        #
        # Linha única de configuração (id sempre 1), para o Master poder
        # ajustar sem mexer em código: de quantas em quantas indicações que
        # viraram cliente de verdade (gerou E usou cupom) o indicador ganha
        # um cupom-prêmio, e de quanto é esse prêmio.
        #
        # Os dois mínimos de litros existem para o programa premiar cliente
        # de verdade, e não abastecimento de 1 litro só para fechar a conta
        # do amigo. Cada indicado precisa fazer UM abastecimento que atinja
        # o mínimo da categoria dele para ser contado.
        #
        # O óleo tem mínimo 1 porque na pista existe reposição avulsa — o
        # motorista compra 1 litro para completar o nível, sem troca. Isso é
        # compra legítima e conta.
        # Os três prazos (decisão do Edmundo, 23/08) são independentes:
        #
        #   data_fim_campanha      — depois desta data o programa para de gerar
        #                            prêmios novos. Prêmios já ganhos continuam
        #                            valendo até a validade deles. Vazio = sem
        #                            prazo. Serve também para comunicar
        #                            "válido até…" ao motorista, que gera urgência.
        #   validade_premio_dias   — quantos dias o cliente tem para USAR um
        #                            prêmio ganho. Sem isso, um prêmio de hoje
        #                            poderia ser cobrado daqui a dois anos.
        #   validade_indicacao_dias— quantos dias um cadastro feito pelo link
        #                            tem para abastecer e virar ponto. Passou,
        #                            deixa de contar.
        f'''CREATE TABLE IF NOT EXISTS config_indicacoes (
            id {serial},
            meta_indicacoes INTEGER DEFAULT 3,
            valor_recompensa {real} DEFAULT 10.00,
            minimo_litros_combustivel {real} DEFAULT 20,
            minimo_litros_oleo {real} DEFAULT 1,
            data_fim_campanha TEXT,
            validade_premio_dias INTEGER DEFAULT 30,
            validade_indicacao_dias INTEGER DEFAULT 90,
            ativo INTEGER DEFAULT 1,
            atualizado_por TEXT,
            data_atualizacao TIMESTAMP DEFAULT {agora}
        )''',

        # Um prêmio ganho por um cliente-indicador, ao completar o número de
        # indicações positivas da configuração acima. `valor` fica congelado
        # no momento da concessão — se o Master mudar o valor padrão depois,
        # os prêmios já concedidos não mudam de tamanho.
        #
        # status: disponivel -> aplicado (congelado num cupom, ver
        # cupons.valor_recompensa_aplicada) -> ou de volta a disponivel, se o
        # cupom em que tinha sido aplicado for trocado ou não for usado.
        # Um prêmio disponível que passa da `validade` vira 'expirado'.
        f'''CREATE TABLE IF NOT EXISTS recompensas_indicacao (
            id {serial},
            cliente_id INTEGER NOT NULL,
            valor {real} NOT NULL,
            indicacoes_completas INTEGER NOT NULL,
            status TEXT DEFAULT 'disponivel',
            data_concessao TEXT NOT NULL,
            validade TEXT,
            cupom_id INTEGER,
            data_aplicacao TEXT
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
    'clientes': [
        # Consentimento LGPD (avisos do app: obrigatório / parceiros: opcional)
        ('aceita_promocoes', 'INTEGER DEFAULT 0', 'INTEGER DEFAULT 0'),
        ('data_consentimento', 'TEXT', 'TEXT'),
        ('aceita_parceiros', 'INTEGER DEFAULT 0', 'INTEGER DEFAULT 0'),
        ('data_consentimento_parceiros', 'TEXT', 'TEXT'),
        # Identificação — trava contra quem se diz taxista/motorista de app só
        # para pegar desconto.
        #
        # A placa é editável de propósito: motorista de aplicativo troca de
        # carro (aluguel, carro da frota), então ela vale para "hoje" e é
        # conferida pelo frentista na bomba. No táxi ela é estável, porque
        # acompanha a permissão quando o taxista troca de veículo.
        ('placa', 'TEXT', 'TEXT'),
        ('data_placa', 'TEXT', 'TEXT'),
        ('registro_tipo', 'TEXT', 'TEXT'),           # condutax | conduapp | convenio
        ('registro_numero', 'TEXT', 'TEXT'),         # opcional: a prova é a foto
        ('empresa_convenio', 'TEXT', 'TEXT'),
        # Comprovante da categoria, em base64 e já reduzido no celular:
        # licença de taxista, print do perfil no app de motorista ou convênio.
        ('foto_comprovante', 'TEXT', 'TEXT'),
        ('foto_comprovante_tipo', 'TEXT', 'TEXT'),
        ('data_foto_comprovante', 'TEXT', 'TEXT'),
        # Convênio de empresa: liga o cliente à empresa cadastrada pela
        # gerência (empresas_convenio.id). O campo antigo empresa_convenio
        # continua guardando o nome por escrito, para não perder histórico
        # de quem se cadastrou antes desta trava existir.
        ('empresa_convenio_id', 'INTEGER', 'INTEGER'),
        # Alçada de liberação. Cliente de convênio nasce 'pendente' e não
        # emite cupom até gerência ou master aprovar.
        ('aprovado_por', 'TEXT', 'TEXT'),
        ('data_aprovacao', 'TEXT', 'TEXT'),
        ('motivo_recusa', 'TEXT', 'TEXT'),
        # Recuperação de senha ("esqueci minha senha").
        #
        # Guardamos o HASH do token, nunca o token em si. Se um dia alguém
        # puser os olhos no banco — backup vazado, print de tela, consulta de
        # suporte — o que estiver lá não serve para entrar em conta nenhuma.
        # É a mesma razão de a senha ser hash: o banco não guarda segredo
        # utilizável.
        ('reset_token_hash', 'TEXT', 'TEXT'),
        ('reset_expira', 'TEXT', 'TEXT'),
        ('reset_pedido_em', 'TEXT', 'TEXT'),
        # Programa de indicação ("traga um amigo").
        #
        # codigo_indicacao: o código pessoal deste cliente, para ele
        # compartilhar (link tipo cajsky.com.br/?ref=CJ123). Gerado sozinho
        # no cadastro, a partir do próprio id — não precisa checar
        # duplicidade porque o id já é único.
        #
        # indicado_por_id: se este cliente entrou por um link de indicação,
        # aqui fica o id de quem indicou. Fica vazio para quem se cadastrou
        # sem link nenhum.
        #
        # indicacao_positiva_contada: fica 1 assim que este cliente usa o
        # primeiro cupom de verdade (gerou E abasteceu). É o que impede a
        # mesma indicação de ser contada de novo a cada abastecimento
        # seguinte — só a PRIMEIRA vez conta ponto para quem indicou.
        ('codigo_indicacao', 'TEXT', 'TEXT'),
        ('indicado_por_id', 'INTEGER', 'INTEGER'),
        ('indicacao_positiva_contada', 'INTEGER DEFAULT 0', 'INTEGER DEFAULT 0'),
    ],
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
        # Limite por categoria: um cupom de combustível por dia, um de óleo por
        # semana. A categoria fica gravada no cupom em vez de ser consultada no
        # produto na hora, porque o produto pode mudar de tipo depois — e aí o
        # histórico passaria a contar errado.
        ('categoria', 'TEXT', 'TEXT'),
        # Qual liberação extra do Master permitiu este cupom (quando foi o caso)
        ('liberacao_id', 'INTEGER', 'INTEGER'),
        # Quando o cliente troca de produto antes de usar, o cupom antigo é
        # cancelado e aponta para o novo. Guardar os dois lados deixa a troca
        # visível no histórico, em vez de o cupom simplesmente sumir.
        ('trocado_por', 'INTEGER', 'INTEGER'),
        ('data_cancelamento', 'TEXT', 'TEXT'),
        # Valor do prêmio de indicação (se algum) congelado neste cupom no
        # momento em que foi gerado — soma ao desconto normal na hora de
        # dar baixa. Ver recompensas_indicacao.
        ('valor_recompensa_aplicada', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
    ],
    'produtos': [
        ('desconto_valor', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('desconto_tipo', "TEXT DEFAULT 'fixo'", "TEXT DEFAULT 'fixo'"),
        ('limite_litros', 'DOUBLE PRECISION DEFAULT 50', 'REAL DEFAULT 50'),
        ('preco_custo', 'DOUBLE PRECISION DEFAULT 0', 'REAL DEFAULT 0'),
        ('margem_minima', 'DOUBLE PRECISION DEFAULT 10', 'REAL DEFAULT 10'),
    ],
    'abastecimentos': [
        # Quem liberou o abastecimento na pista. Sem isso, descobrir se um
        # motorista só abastece com um frentista específico dependeria de
        # cruzar a auditoria por data — frágil e sujeito a erro.
        ('registrado_por', 'TEXT', 'TEXT'),
        # Em qual fechamento de turno este abastecimento entrou. Enquanto for
        # NULO, ele pertence ao turno que está aberto. É o que define o turno
        # do frentista sem depender do relógio.
        ('fechamento_id', 'INTEGER', 'INTEGER'),
    ],
    'admin': [
        ('token', 'TEXT', 'TEXT'),
        ('token_expira', 'TEXT', 'TEXT'),
        ('nivel', "TEXT DEFAULT 'master'", "TEXT DEFAULT 'master'"),
        ('nome', 'TEXT', 'TEXT'),
        ('ativo', 'INTEGER DEFAULT 1', 'INTEGER DEFAULT 1'),
        # E-mail do usuário do painel. Obrigatório para quem for criado de
        # agora em diante; quem já existia entra sem e-mail e aparece com
        # aviso na aba Usuários até o Master preencher.
        #
        # Sem isto, um usuário do painel que esquecesse a senha só podia ser
        # destravado por outro Master — e havia um Master só. Esquecer a senha
        # significava perder o painel inteiro.
        ('email', 'TEXT', 'TEXT'),
        ('reset_token_hash', 'TEXT', 'TEXT'),
        ('reset_expira', 'TEXT', 'TEXT'),
        ('reset_pedido_em', 'TEXT', 'TEXT'),
    ],
    # Mínimos de litros do programa de indicação. Ficam aqui também (e não
    # só no CREATE TABLE) para o caso de a tabela já ter sido criada numa
    # publicação anterior sem estas colunas.
    'config_indicacoes': [
        ('minimo_litros_combustivel', 'DOUBLE PRECISION DEFAULT 20', 'REAL DEFAULT 20'),
        ('minimo_litros_oleo', 'DOUBLE PRECISION DEFAULT 1', 'REAL DEFAULT 1'),
        ('data_fim_campanha', 'TEXT', 'TEXT'),
        ('validade_premio_dias', 'INTEGER DEFAULT 30', 'INTEGER DEFAULT 30'),
        ('validade_indicacao_dias', 'INTEGER DEFAULT 90', 'INTEGER DEFAULT 90'),
    ],
    'recompensas_indicacao': [
        ('validade', 'TEXT', 'TEXT'),
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

    # Configuração padrão do programa de indicação: a cada 3 indicações que
    # viraram cliente de verdade, um prêmio de R$ 10,00. O Master ajusta os
    # dois números depois, pelo painel — isto aqui só garante que a linha
    # exista desde o primeiro dia (o resto do código sempre espera achar 1).
    if pg:
        cursor.execute('''
            INSERT INTO config_indicacoes
            (id, meta_indicacoes, valor_recompensa,
             minimo_litros_combustivel, minimo_litros_oleo,
             validade_premio_dias, validade_indicacao_dias, ativo)
            VALUES (1, 3, 10.00, 20, 1, 30, 90, 1)
            ON CONFLICT (id) DO NOTHING
        ''')
    else:
        cursor.execute('''
            INSERT OR IGNORE INTO config_indicacoes
            (id, meta_indicacoes, valor_recompensa,
             minimo_litros_combustivel, minimo_litros_oleo,
             validade_premio_dias, validade_indicacao_dias, ativo)
            VALUES (1, 3, 10.00, 20, 1, 30, 90, 1)
        ''')

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
