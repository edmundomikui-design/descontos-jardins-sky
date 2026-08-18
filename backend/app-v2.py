from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import uuid
import qrcode
from io import BytesIO
import base64
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import re

from database import init_db, get_db

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Admin-Token"]
    }
})

# Em produção a chave vem de variável de ambiente
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or str(uuid.uuid4())

# Inicializa banco de dados
init_db()

# ==================== UTILIDADES ====================

def validar_cpf(cpf):
    """Valida CPF básico"""
    cpf = re.sub(r'\D', '', cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False
    return True

# ==================== IDENTIFICAÇÃO DA CATEGORIA ====================
# O desconto é para taxista e motorista de aplicativo. Sem nada que ligue a
# pessoa à categoria, qualquer um se declara motorista e leva o desconto.
#
# Duas travas diferentes, porque as duas categorias são diferentes:
#
#  - COMPROVANTE (foto, no cadastro): taxista manda a licença; motorista de
#    aplicativo manda o print do perfil dele no app de motorista. Não é prova
#    inviolável — é atrito e rastro com nome em cima.
#
#  - PLACA (editável, conferida na bomba): no táxi ela é estável, porque
#    acompanha a permissão quando o taxista troca de carro. No aplicativo o
#    carro muda, então a placa vale para o dia e o motorista atualiza quando
#    trocar. É a única conferência que não depende de sistema nenhum: ou bate
#    com o carro na frente do frentista, ou não bate.

# Formato antigo (ABC1234) e Mercosul (ABC1D23)
_PLACA_ANTIGA = re.compile(r'^[A-Z]{3}[0-9]{4}$')
_PLACA_MERCOSUL = re.compile(r'^[A-Z]{3}[0-9][A-Z][0-9]{2}$')

# O que cada ocupação precisa comprovar
# Cupom vale para UM abastecimento só. Se o motorista pôs 20 L num cupom de
# 50 L, o cupom fecha e os 30 L restantes não valem mais — ele deve aproveitar
# o limite de uma vez.
#
# Regra de negócio, não limitação técnica: evita o mesmo cliente voltando
# várias vezes no dia por pouco volume. Trocar para False devolve o uso em
# partes, sem mexer em mais nada.
USO_UNICO = True

PERFIL_OCUPACAO = {
    'táxi':       {'registro': 'condutax', 'comprovante': 'licenca_taxi'},
    'taxi':       {'registro': 'condutax', 'comprovante': 'licenca_taxi'},
    'uber':       {'registro': 'conduapp', 'comprovante': 'perfil_app'},
    'aplicativo': {'registro': 'conduapp', 'comprovante': 'perfil_app'},
}

DESCRICAO_COMPROVANTE = {
    'licenca_taxi': 'a foto da sua licença de taxista (alvará ou CONDUTAX)',
    'perfil_app': 'o print da tela de cadastro do seu aplicativo de motorista',
    'convenio': 'o comprovante de vínculo com a empresa conveniada',
}

# ~1,4 MB de base64 ≈ 1 MB de imagem. O app já reduz a foto no celular;
# o limite existe para ninguém entupir o banco mandando direto na API.
LIMITE_FOTO_BASE64 = 1_400_000


def normalizar_cnpj(cnpj):
    """Devolve o CNPJ só com números, ou None."""
    if not cnpj:
        return None
    limpo = re.sub(r'\D', '', str(cnpj))
    return limpo or None


def validar_cnpj(cnpj):
    """
    Confere os dois dígitos verificadores do CNPJ.

    Isso pega erro de digitação, não fraude: qualquer um acha o CNPJ real de
    qualquer empresa em segundos. A trava de verdade é a empresa precisar
    estar cadastrada aqui pela gerência (convênio assinado).
    """
    n = normalizar_cnpj(cnpj)
    if not n or len(n) != 14 or n == n[0] * 14:
        return False

    def digito(base, pesos):
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return '0' if resto < 2 else str(11 - resto)

    d1 = digito(n[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = digito(n[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return n[12:] == d1 + d2


def exige_alcada_master(email, dominio_empresa):
    """
    Diz se este cadastro só pode ser liberado pelo Master.

    Vale quando a empresa exige e-mail corporativo e a pessoa se cadastrou com
    outro (Gmail, por exemplo). Não é bloqueio: é exceção, e exceção sobe de
    nível. Calculado na hora, comparando o e-mail com o domínio atual da
    empresa — se o convênio mudar de domínio depois, a regra acompanha.
    """
    dominio = (dominio_empresa or '').strip().lower().lstrip('@')
    if not dominio:
        return False
    return not (email or '').strip().lower().endswith('@' + dominio)


def formatar_cnpj(cnpj):
    """00.000.000/0000-00 para exibição."""
    n = normalizar_cnpj(cnpj)
    if not n or len(n) != 14:
        return cnpj
    return f'{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}'


def normalizar_placa(placa):
    """Devolve a placa só com letras e números, em maiúsculas, ou None."""
    if not placa:
        return None
    limpa = re.sub(r'[^A-Za-z0-9]', '', str(placa)).upper()
    return limpa or None


def validar_placa(placa):
    """Aceita o formato antigo e o Mercosul. Devolve erro em texto ou None."""
    if not placa:
        return 'Informe a placa do carro que você está usando.'
    if len(placa) != 7:
        return 'A placa deve ter 7 caracteres (exemplos: ABC1D23 ou ABC1234).'
    if not (_PLACA_ANTIGA.match(placa) or _PLACA_MERCOSUL.match(placa)):
        return 'Placa em formato inválido. Use ABC1D23 (Mercosul) ou ABC1234 (antiga).'
    return None


def validar_foto(foto, tipo_comprovante):
    """Confere que veio uma imagem plausível, sem tentar adivinhar o conteúdo."""
    descricao = DESCRICAO_COMPROVANTE.get(tipo_comprovante, 'o comprovante')
    if not foto:
        return f'Envie {descricao}.'
    if not str(foto).startswith('data:image/'):
        return 'Arquivo inválido. Envie uma imagem.'
    if len(foto) > LIMITE_FOTO_BASE64:
        return 'A imagem ficou grande demais. Tente de novo pelo aplicativo.'
    if len(foto) < 2000:
        return 'A imagem não foi enviada por completo. Tente de novo.'
    return None


def _cpf_mascarado(cpf):
    """Mostra só o miolo do CPF (***.123.456-**) para o frentista conferir
    a identidade sem expor o documento inteiro na pista."""
    if not cpf:
        return ''
    d = re.sub(r'\D', '', cpf)
    if len(d) != 11:
        return cpf
    return f'***.{d[3:6]}.{d[6:9]}-**'


def validar_email(email):
    """Valida email básico"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def imagem_qrcode(qr_data):
    """Gera a imagem (base64) de um código já existente"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return base64.b64encode(img_bytes.getvalue()).decode()

def gerar_qrcode():
    """Gera QR code único"""
    qr_data = str(uuid.uuid4())[:12]
    return qr_data, imagem_qrcode(qr_data)

def obter_turno(hora=None):
    """Retorna o turno baseado na hora"""
    if hora is None:
        hora = datetime.now().hour

    if 6 <= hora < 14:
        return "Turno 1 (6h-14h)"
    elif 14 <= hora < 22:
        return "Turno 2 (14h-22h)"
    else:
        return "Turno 3 (22h-6h)"

# ==================== AUTENTICAÇÃO DE ADMIN ====================

def admin_do_token():
    """Retorna o admin dono do token enviado no header, ou None."""
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    if not token:
        return None

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, usuario, nome, nivel, poster_id, token_expira, ativo
        FROM admin WHERE token = ?
    ''', (token,))
    admin = cursor.fetchone()
    conn.close()

    if not admin or admin['ativo'] == 0:
        return None

    if admin['token_expira'] and admin['token_expira'] < datetime.now().strftime('%Y-%m-%d %H:%M:%S'):
        return None

    return admin


def exige_admin(f):
    """Qualquer usuário logado (Master ou Caixa)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def exige_master(f):
    """Somente o nível Master — usuários e configurações sensíveis."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        if admin['nivel'] != 'master':
            return jsonify({
                'erro': 'Permissão negada. Somente o administrador Master pode fazer isso.'
            }), 403
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def exige_gerencia(f):
    """Master ou Gerência — alterar preços e descontos (com trava de margem)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        if admin['nivel'] not in ('master', 'gerencia'):
            return jsonify({
                'erro': 'Permissão negada. Seu acesso é de consulta (Caixa) e não permite alterações.'
            }), 403
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def registrar_auditoria(cursor, admin, acao, produto_id=None, produto_nome=None,
                        campo=None, valor_anterior=None, valor_novo=None, detalhe=None):
    """Grava quem mudou o quê, quando e de qual valor para qual."""
    cursor.execute('''
        INSERT INTO auditoria
        (data_hora, admin_id, admin_usuario, admin_nivel, acao,
         produto_id, produto_nome, campo, valor_anterior, valor_novo, detalhe)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        admin['id'] if admin else None,
        admin['usuario'] if admin else None,
        admin['nivel'] if admin else None,
        acao, produto_id, produto_nome, campo,
        None if valor_anterior is None else str(valor_anterior),
        None if valor_novo is None else str(valor_novo),
        detalhe
    ))


def desconto_por_unidade(preco, valor, tipo):
    """Converte o desconto informado em reais por unidade."""
    return preco * (valor / 100) if tipo == 'percentual' else valor


def validar_margem(nivel, nome, preco, custo, margem_minima, desc_unidade):
    """Trava de margem. Devolve mensagem de erro ou None se estiver liberado.

    - Ninguém pode deixar o preço final abaixo do custo.
    - Gerência ainda precisa respeitar a margem mínima do produto.
    """
    # Trabalha em centavos arredondados: sem isso, 6.09 - 0.37 vira 5.719999...
    # e um preço que bate exatamente no piso seria recusado por engano.
    preco_final = round(preco - desc_unidade, 2)
    custo = round(custo or 0, 2)

    if preco_final < 0:
        return f'{nome}: o desconto deixa o preço negativo.'

    if custo <= 0:
        if nivel != 'master':
            return (f'{nome}: o preço de custo não está cadastrado. '
                    f'Peça ao administrador Master para informá-lo antes de dar desconto.')
        return None  # Master pode operar produto sem custo cadastrado

    if preco_final < custo:
        return (f'{nome}: o preço final R$ {preco_final:.2f} fica ABAIXO do custo '
                f'R$ {custo:.2f}. Prejuízo por unidade de R$ {custo - preco_final:.2f}.')

    if nivel != 'master':
        piso = round(custo * (1 + (margem_minima or 0) / 100), 2)
        if preco_final < piso:
            return (f'{nome}: o preço final R$ {preco_final:.2f} fica abaixo do mínimo '
                    f'permitido para o seu nível (R$ {piso:.2f} = custo + {margem_minima:.0f}%). '
                    f'Fale com o administrador Master.')

    return None


# ==================== ROTAS DE AUTENTICAÇÃO ====================

@app.route('/api/auth/cadastro', methods=['POST'])
def cadastro():
    """Cadastra novo cliente"""
    try:
        data = request.get_json()

        if not validar_cpf(data.get('cpf')):
            return jsonify({'erro': 'CPF inválido'}), 400

        if not validar_email(data.get('email')):
            return jsonify({'erro': 'Email inválido'}), 400

        if len(data.get('endereco', '')) < 10:
            return jsonify({'erro': 'Endereço incompleto'}), 400

        # Dois aceites, guardados com a data (prova exigida pela LGPD):
        #  - avisos do aplicativo (cupons e preços do dia): OBRIGATÓRIO, é a função do app
        #  - promoções de parceiros: OPCIONAL, marketing puro
        def marcado(v):
            return v in (True, 1, '1', 'true', 'on')

        aceita_promocoes = 1 if marcado(data.get('aceita_promocoes')) else 0
        if not aceita_promocoes:
            return jsonify({
                'erro': 'É preciso aceitar os avisos do aplicativo (cupons e preços do dia) para se cadastrar'
            }), 400

        aceita_parceiros = 1 if marcado(data.get('aceita_parceiros')) else 0
        agora_iso = datetime.now().isoformat()
        data_consentimento = agora_iso
        data_consentimento_parceiros = agora_iso if aceita_parceiros else None

        # ---- comprovação da categoria e carro em uso ----
        ocupacao = (data.get('ocupacao') or '').strip()
        perfil = PERFIL_OCUPACAO.get(ocupacao.lower())

        registro_tipo = perfil['registro'] if perfil else 'convenio'
        tipo_comprovante = perfil['comprovante'] if perfil else 'convenio'
        registro_numero = re.sub(r'[^A-Za-z0-9]', '', str(data.get('registro_numero') or '')).upper()
        empresa_convenio = (data.get('empresa_convenio') or '').strip() or None
        empresa_convenio_id = data.get('empresa_convenio_id')

        # A placa é a conferência da bomba, então vale para todo mundo.
        # Quem troca de carro atualiza depois, em dois toques.
        placa = normalizar_placa(data.get('placa'))
        erro_placa = validar_placa(placa)
        if erro_placa:
            return jsonify({'erro': erro_placa}), 400

        # A foto é a prova da categoria: licença de taxista ou print do perfil
        # no app de motorista. O número do registro fica opcional.
        foto_comprovante = data.get('foto_comprovante')
        erro_foto = validar_foto(foto_comprovante, tipo_comprovante)
        if erro_foto:
            return jsonify({'erro': erro_foto}), 400

        cpf = re.sub(r'\D', '', data.get('cpf'))

        conn = get_db()
        cursor = conn.cursor()

        # ---- convênio de empresa: só da lista, e sempre com análise ----
        #
        # Táxi e motorista de aplicativo continuam liberados na hora. Convênio
        # não: a empresa precisa ter contrato assinado (cadastrada pela
        # gerência) e o cadastro fica pendente até alguém de alçada aprovar.
        status_inicial = 'ativo'
        empresa = None
        sem_email_corporativo = False

        if registro_tipo == 'convenio':
            if not empresa_convenio_id:
                conn.close()
                return jsonify({
                    'erro': 'Escolha a empresa do convênio na lista. '
                            'Se a sua empresa não aparece, ela ainda não tem convênio '
                            'com os postos CAJ e SKY — fale com o RH dela.'
                }), 400

            cursor.execute(
                'SELECT id, nome, cnpj, dominio_email, limite_funcionarios, ativo '
                'FROM empresas_convenio WHERE id = ?',
                (empresa_convenio_id,)
            )
            empresa = cursor.fetchone()

            if not empresa or not empresa['ativo']:
                conn.close()
                return jsonify({
                    'erro': 'Essa empresa não tem convênio ativo com os postos CAJ e SKY.'
                }), 400

            # O domínio corporativo é a prova mais barata de vínculo, mas
            # barrar quem não tem seria injusto: muito funcionário de chão de
            # fábrica só tem Gmail. Então não bloqueia — deixa cadastrar,
            # marca o caso e joga a decisão para o Master (gerência não
            # resolve exceção).
            dominio = (empresa['dominio_email'] or '').strip().lower().lstrip('@')
            email_informado = (data.get('email') or '').strip().lower()
            sem_email_corporativo = bool(dominio) and not email_informado.endswith('@' + dominio)

            # Teto de funcionários combinado com o RH: mesmo que alguém burle
            # tudo, o estrago para no número contratado.
            limite = empresa['limite_funcionarios'] or 0
            if limite > 0:
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM clientes "
                    "WHERE empresa_convenio_id = ? AND status <> 'recusado'",
                    (empresa['id'],)
                )
                linha_lim = cursor.fetchone()
                if (linha_lim['n'] if linha_lim else 0) >= limite:
                    conn.close()
                    return jsonify({
                        'erro': f'O convênio da {empresa["nome"]} já atingiu o limite de '
                                f'{limite} funcionários. Fale com o RH da empresa.'
                    }), 400

            empresa_convenio = empresa['nome']
            empresa_convenio_id = empresa['id']
            status_inicial = 'pendente'
        else:
            # Táxi/Uber não carregam vínculo de empresa.
            empresa_convenio_id = None

        cursor.execute('SELECT id FROM clientes WHERE cpf = ?', (cpf,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'erro': 'CPF já cadastrado'}), 400

        cursor.execute('SELECT id FROM clientes WHERE email = ?', (data.get('email'),))
        if cursor.fetchone():
            conn.close()
            return jsonify({'erro': 'Email já cadastrado'}), 400

        # Placa repetida não bloqueia — dois motoristas podem dividir o mesmo
        # táxi por turno. Mas fica registrado e aparece no painel de suspeitas
        # e na tela do frentista.
        cursor.execute('SELECT COUNT(*) AS n FROM clientes WHERE placa = ?', (placa,))
        linha = cursor.fetchone()
        placa_repetida = (linha['n'] if linha else 0) > 0

        senha_hash = generate_password_hash(data.get('senha'))

        cursor.execute('''
            INSERT INTO clientes
            (cpf, nome, ocupacao, tel, endereco, email, senha_hash, desconto_tipo, desconto_valor,
             aceita_promocoes, data_consentimento, aceita_parceiros, data_consentimento_parceiros,
             placa, data_placa, registro_tipo, registro_numero, empresa_convenio,
             foto_comprovante, foto_comprovante_tipo, data_foto_comprovante,
             empresa_convenio_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cpf,
            data.get('nome'),
            ocupacao,
            data.get('tel'),
            data.get('endereco'),
            data.get('email'),
            senha_hash,
            'fixo',  # Tipo: valor fixo em reais
            1.00,    # Desconto padrão: R$ 1,00 por litro
            aceita_promocoes,
            data_consentimento,
            aceita_parceiros,
            data_consentimento_parceiros,
            placa,
            agora_iso,
            registro_tipo,
            registro_numero or None,
            empresa_convenio,
            foto_comprovante,
            tipo_comprovante,
            agora_iso,
            empresa_convenio_id,
            status_inicial
        ))

        conn.commit()
        cliente_id = cursor.lastrowid
        conn.close()

        if status_inicial == 'pendente' and sem_email_corporativo:
            mensagem = (f'Cadastro enviado para análise. Como você não usou o e-mail da '
                        f'{empresa_convenio}, a liberação precisa passar pelo responsável '
                        f'dos postos — pode demorar um pouco mais.')
        elif status_inicial == 'pendente':
            mensagem = (f'Cadastro enviado para análise. Assim que a gerência confirmar '
                        f'seu vínculo com a {empresa_convenio}, você poderá gerar cupons. '
                        f'Você receberá um aviso.')
        else:
            mensagem = 'Cadastro realizado! Confirme seu email.'

        return jsonify({
            'mensagem': mensagem,
            'cliente_id': cliente_id,
            'placa': placa,
            'placa_ja_cadastrada': placa_repetida,
            'status': status_inicial,
            'aguardando_aprovacao': status_inicial == 'pendente'
        }), 201

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login do cliente"""
    try:
        data = request.get_json()
        email = data.get('email')
        senha = data.get('senha')

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, nome, email, senha_hash, placa, ocupacao,
                   status, motivo_recusa, empresa_convenio
            FROM clientes WHERE email = ?
        ''', (email,))
        cliente = cursor.fetchone()
        conn.close()

        if not cliente or not check_password_hash(cliente['senha_hash'], senha):
            return jsonify({'erro': 'Email ou senha incorretos'}), 401

        # O login continua funcionando com cadastro pendente de propósito: a
        # pessoa precisa conseguir entrar para acompanhar a análise. O que não
        # sai é o cupom.
        situacao = (cliente['status'] or 'ativo').lower()

        return jsonify({
            'cliente_id': cliente['id'],
            'nome': cliente['nome'],
            'email': cliente['email'],
            'placa': cliente['placa'],
            'ocupacao': cliente['ocupacao'],
            'status': situacao,
            'empresa_convenio': cliente['empresa_convenio'],
            'motivo_recusa': cliente['motivo_recusa'],
            'mensagem': 'Login realizado com sucesso'
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE PRODUTOS ====================

@app.route('/api/cliente/placa', methods=['POST'])
def atualizar_placa():
    """Troca a placa do carro em uso.

    Motorista de aplicativo troca de carro com frequência — alugado, da frota,
    o do fim de semana. Se a placa fosse fixa no cadastro, a conferência na
    bomba falharia justamente para quem mais usa o app. Aqui ele atualiza em
    dois toques, e o cupom congela a placa no momento em que é gerado.
    """
    try:
        data = request.get_json()
        cliente_id = data.get('cliente_id')
        if not cliente_id:
            return jsonify({'erro': 'cliente_id é obrigatório'}), 400

        placa = normalizar_placa(data.get('placa'))
        erro = validar_placa(placa)
        if erro:
            return jsonify({'erro': erro}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT placa FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        anterior = cliente['placa']
        agora_iso = datetime.now().isoformat()

        cursor.execute('UPDATE clientes SET placa = ?, data_placa = ? WHERE id = ?',
                       (placa, agora_iso, cliente_id))

        # Troca de placa é exatamente o movimento que uma conta emprestada faria.
        # Não bloqueia, mas fica gravado para o painel de suspeitas.
        if anterior and anterior != placa:
            cursor.execute('''
                INSERT INTO auditoria
                (data_hora, admin_usuario, admin_nivel, acao, campo,
                 valor_anterior, valor_novo, detalhe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                f'cliente:{cliente_id}', 'cliente', 'troca_placa', 'placa',
                anterior, placa, f'Cliente {cliente_id} trocou a placa do carro em uso'
            ))

        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Placa atualizada.', 'placa': placa}), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/produtos', methods=['GET'])
def listar_produtos():
    """Lista todos os produtos com preços"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, nome, tipo, preco_atual, unidade, icone
            FROM produtos
            WHERE ativo = 1
            ORDER BY tipo DESC, id
        ''')
        produtos = cursor.fetchall()
        conn.close()

        return jsonify({
            'produtos': [
                {
                    'id': p['id'],
                    'nome': p['nome'],
                    'tipo': p['tipo'],
                    'preco': round(p['preco_atual'], 2),
                    'unidade': p['unidade'],
                    'icone': p['icone']
                }
                for p in produtos
            ]
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/produtos/<int:produto_id>/preco', methods=['GET'])
def get_preco_produto(produto_id):
    """Obtém preço atual de um produto"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, nome, preco_atual, data_atualizacao
            FROM produtos
            WHERE id = ? AND ativo = 1
        ''', (produto_id,))
        produto = cursor.fetchone()
        conn.close()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        return jsonify({
            'produto_id': produto['id'],
            'nome': produto['nome'],
            'preco_atual': round(produto['preco_atual'], 2),
            'data_atualizacao': produto['data_atualizacao']
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE CUPOM ====================

@app.route('/api/cupom/gerar', methods=['POST'])
def gerar_cupom():
    """Gera cupom/QR code para cliente em um produto específico"""
    try:
        data = request.get_json()
        cliente_id = data.get('cliente_id')
        produto_id = data.get('produto_id')

        if not produto_id:
            return jsonify({'erro': 'Produto_id é obrigatório'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Verifica cliente
        cursor.execute('''
            SELECT id, nome, desconto_tipo, desconto_valor, status, motivo_recusa
            FROM clientes
            WHERE id = ?
        ''', (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        # A alçada só vale se travar aqui: sem isto o cadastro de convênio
        # ficaria "pendente" no painel e mesmo assim saía abastecendo com
        # desconto — a aprovação viraria enfeite.
        situacao = (cliente['status'] or 'ativo').lower()
        if situacao == 'pendente':
            conn.close()
            return jsonify({
                'erro': 'Seu cadastro ainda está em análise pela gerência. '
                        'Assim que for aprovado você poderá gerar cupons.',
                'status': 'pendente'
            }), 403
        if situacao in ('recusado', 'bloqueado', 'inativo'):
            motivo = cliente['motivo_recusa'] or 'Fale com a gerência dos postos.'
            conn.close()
            return jsonify({
                'erro': f'Seu cadastro não está liberado. {motivo}',
                'status': situacao
            }), 403

        # Verifica produto (preço, desconto e limite vêm da tela de administrador)
        cursor.execute('''
            SELECT id, nome, preco_atual, unidade, desconto_valor, desconto_tipo, limite_litros
            FROM produtos
            WHERE id = ? AND ativo = 1
        ''', (produto_id,))
        produto = cursor.fetchone()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        # Verifica cupom ativo
        hoje = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT id FROM cupons
            WHERE cliente_id = ? AND produto_id = ? AND data_geracao = ?
        ''', (cliente_id, produto_id, hoje))

        if cursor.fetchone():
            return jsonify({'erro': f'Você já gerou um cupom para {produto["nome"]} hoje!'}), 400

        # Desconto do produto; se ainda não configurado, cai no desconto do cliente
        desconto_valor = produto['desconto_valor'] or 0
        desconto_tipo = produto['desconto_tipo'] or 'fixo'

        if desconto_valor <= 0:
            desconto_valor = cliente['desconto_valor'] or 0
            desconto_tipo = cliente['desconto_tipo'] or 'fixo'

        preco = produto['preco_atual'] or 0
        if desconto_tipo == 'percentual':
            desconto_por_unidade = preco * (desconto_valor / 100)
        else:
            desconto_por_unidade = desconto_valor

        desconto_por_unidade = min(desconto_por_unidade, preco)
        preco_final_unitario = preco - desconto_por_unidade

        quantidade_permitida = produto['limite_litros'] or 50
        economia_total = desconto_por_unidade * quantidade_permitida

        # Gera cupom (preço e desconto ficam congelados neste cupom)
        qr_data, qr_image = gerar_qrcode()

        cursor.execute('''
            INSERT INTO cupons
            (cliente_id, produto_id, qrcode, data_geracao, quantidade_permitida,
             quantidade_utilizada, status, preco_unitario, desconto_unitario,
             desconto_valor, desconto_tipo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cliente_id,
            produto_id,
            qr_data,
            hoje,
            quantidade_permitida,
            0,
            'pendente',
            preco,
            desconto_por_unidade,
            desconto_valor,
            desconto_tipo
        ))

        conn.commit()
        cupom_id = cursor.lastrowid
        conn.close()

        return jsonify({
            'cupom_id': cupom_id,
            'qrcode_data': qr_data,
            'qrcode_image': f'data:image/png;base64,{qr_image}',
            'cliente_nome': cliente['nome'],
            'produto_id': produto['id'],
            'produto_nome': produto['nome'],
            'unidade': produto['unidade'],
            'preco_produto': round(preco, 2),
            'preco_unitario_com_desconto': round(preco_final_unitario, 2),
            'desconto_tipo': desconto_tipo,
            'desconto_valor': desconto_valor,
            'desconto_por_unidade': round(desconto_por_unidade, 2),
            'quantidade_permitida': quantidade_permitida,
            'economia_total': round(economia_total, 2),
            'uso_unico': USO_UNICO,
            'mensagem': 'QR code gerado com sucesso!'
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/cupom/ativos', methods=['GET'])
def cupons_ativos():
    """Devolve os cupons do cliente gerados HOJE, com o QR code reconstruído.

    Permite ao motorista recuperar o cupom mesmo depois de fechar o app.
    """
    try:
        cliente_id = request.args.get('cliente_id')
        if not cliente_id:
            return jsonify({'erro': 'cliente_id é obrigatório'}), 400

        hoje = datetime.now().date()

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, desconto_tipo, desconto_valor FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        cursor.execute('''
            SELECT c.id, c.qrcode, c.data_geracao, c.status,
                   c.quantidade_permitida, c.quantidade_utilizada,
                   c.preco_unitario, c.desconto_unitario, c.desconto_valor, c.desconto_tipo,
                   p.id AS produto_id, p.nome AS produto_nome,
                   p.preco_atual, p.unidade, p.icone
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            WHERE c.cliente_id = ? AND c.data_geracao = ?
            ORDER BY c.id DESC
        ''', (cliente_id, hoje))
        linhas = cursor.fetchall()
        conn.close()

        cupons = []
        for c in linhas:
            permitida = c['quantidade_permitida'] or 0
            utilizada = c['quantidade_utilizada'] or 0

            # preço e desconto CONGELADOS na geração do cupom
            preco = c['preco_unitario'] or c['preco_atual'] or 0
            desconto_por_unidade = c['desconto_unitario'] or 0

            if desconto_por_unidade <= 0:
                tipo = c['desconto_tipo'] or cliente['desconto_tipo']
                valor = c['desconto_valor'] or cliente['desconto_valor'] or 0
                desconto_por_unidade = preco * (valor / 100) if tipo == 'percentual' else valor

            cupons.append({
                'cupom_id': c['id'],
                'qrcode_data': c['qrcode'],
                'qrcode_image': f"data:image/png;base64,{imagem_qrcode(c['qrcode'])}",
                'status': c['status'],
                'produto_id': c['produto_id'],
                'produto_nome': c['produto_nome'],
                'produto_icone': c['icone'],
                'unidade': c['unidade'],
                'preco_produto': round(preco, 2),
                'preco_unitario_com_desconto': round(preco - desconto_por_unidade, 2),
                'desconto_tipo': cliente['desconto_tipo'],
                'desconto_valor': cliente['desconto_valor'],
                'desconto_por_unidade': round(desconto_por_unidade, 2),
                'quantidade_permitida': permitida,
                'quantidade_utilizada': utilizada,
                'quantidade_restante': round(permitida - utilizada, 2),
                'economia_total': round(desconto_por_unidade * permitida, 2),
                'cliente_nome': cliente['nome']
            })

        return jsonify({'data': str(hoje), 'cupons': cupons}), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/cupom/consultar', methods=['GET'])
@exige_admin
def consultar_cupom():
    """Lê um QR code e devolve o cupom SEM registrar nada.

    É o que a tela do frentista chama assim que a câmera lê o código: mostra
    de quem é o cupom, qual combustível, o preço já com desconto e quantos
    litros ainda restam, para o frentista conferir antes de liberar a bomba.
    """
    try:
        qr = (request.args.get('qrcode') or '').strip()
        if not qr:
            return jsonify({'erro': 'Informe o código do cupom'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.*, p.nome AS produto_nome, p.unidade, p.icone,
                   p.preco_atual, cl.nome AS cliente_nome, cl.cpf AS cliente_cpf,
                   cl.status AS cliente_status, cl.placa AS cliente_placa,
                   cl.ocupacao AS cliente_ocupacao,
                   cl.desconto_tipo AS cli_desc_tipo, cl.desconto_valor AS cli_desc_valor
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            LEFT JOIN clientes cl ON cl.id = c.cliente_id
            WHERE c.qrcode = ?
        ''', (qr,))
        cupom = cursor.fetchone()

        # A mesma placa em vários cadastros pode ser táxi dividido por turno —
        # ou conta emprestada. O frentista precisa ver isso antes de liberar.
        placa = cupom['cliente_placa'] if cupom else None
        contagem_placa = 1
        if placa:
            cursor.execute('SELECT COUNT(*) AS n FROM clientes WHERE placa = ?', (placa,))
            linha = cursor.fetchone()
            contagem_placa = (linha['n'] if linha else 1) or 1

        conn.close()

        if not cupom:
            return jsonify({
                'erro': 'Cupom não encontrado. Confira o código ou peça ao '
                        'motorista para gerar um novo no aplicativo.'
            }), 404

        # Preço e desconto ficam CONGELADOS na geração — reajuste posterior
        # não muda um cupom já emitido.
        preco = cupom['preco_unitario'] or cupom['preco_atual'] or 0
        desc_unidade = cupom['desconto_unitario'] or 0
        if desc_unidade <= 0:
            tipo = cupom['desconto_tipo'] or cupom['cli_desc_tipo']
            valor = cupom['desconto_valor'] or cupom['cli_desc_valor'] or 0
            desc_unidade = desconto_por_unidade(preco, valor, tipo)

        permitida = cupom['quantidade_permitida'] or 0
        utilizada = cupom['quantidade_utilizada'] or 0
        restante = round(permitida - utilizada, 2)

        data_geracao = str(cupom['data_geracao'])[:10]
        hoje = str(datetime.now().date())

        # Um único lugar decide se pode abastecer — a tela só exibe o motivo.
        if data_geracao != hoje:
            valido, motivo = False, (
                f'Cupom de {data_geracao[8:10]}/{data_geracao[5:7]}. '
                f'Vale só no dia em que foi gerado — peça um novo no aplicativo.'
            )
        elif cupom['cliente_status'] and cupom['cliente_status'] != 'ativo':
            valido, motivo = False, 'Cadastro do motorista está inativo.'
        elif (cupom['status'] or '').lower() == 'completo':
            # Com uso único o cupom fecha mesmo sobrando saldo. Quem manda é o
            # status, não a conta de litros — senão a tela mostraria "válido,
            # restam 30 L" para um cupom que a baixa já encerrou.
            valido, motivo = False, ('Cupom já utilizado. Vale para um abastecimento só — '
                                     'o motorista deve gerar um novo amanhã.')
        elif restante <= 0:
            valido, motivo = False, 'Cupom já usado por completo hoje.'
        else:
            valido, motivo = True, None

        # Cupom encerrado não tem saldo a mostrar, mesmo que a subtração dê
        # um número positivo.
        if not valido and (cupom['status'] or '').lower() == 'completo':
            restante = 0

        return jsonify({
            'valido': valido,
            'motivo': motivo,
            'cupom_id': cupom['id'],
            'qrcode': qr,
            'status': cupom['status'],
            'data_geracao': data_geracao,
            'cliente_nome': cupom['cliente_nome'],
            'cliente_cpf': _cpf_mascarado(cupom['cliente_cpf']),
            'placa': placa,
            'ocupacao': cupom['cliente_ocupacao'],
            'placa_em_varios_cadastros': contagem_placa > 1,
            'placa_qtd_cadastros': contagem_placa,
            'produto_id': cupom['produto_id'],
            'produto_nome': cupom['produto_nome'],
            'produto_icone': cupom['icone'],
            'unidade': cupom['unidade'] or 'L',
            'preco_bomba': round(preco, 2),
            'desconto_por_unidade': round(desc_unidade, 2),
            'preco_com_desconto': round(preco - desc_unidade, 2),
            'quantidade_permitida': round(permitida, 2),
            'quantidade_utilizada': round(utilizada, 2),
            'quantidade_restante': restante,
            # Avisa a tela do caixa/frentista ANTES da baixa: o motorista
            # precisa saber que não volta depois com o que sobrar.
            'uso_unico': USO_UNICO
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/cupom/usar', methods=['POST'])
@exige_admin
def usar_cupom():
    """Registra o abastecimento. Chamado pela tela do frentista na pista."""
    try:
        data = request.get_json()
        qrcode = data.get('qrcode')
        produto_id = data.get('produto_id')
        quantidade_agora = float(data.get('quantidade', 0))
        valor_sem_desconto = float(data.get('valor_sem_desconto', 0))

        # O posto vem do usuário logado — o frentista não escolhe, para o
        # abastecimento não cair no caixa do posto errado.
        poster_id = request.admin['poster_id'] or data.get('poster_id')

        if not qrcode or not produto_id or quantidade_agora <= 0:
            return jsonify({'erro': 'Dados incompletos'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Busca cupom
        cursor.execute('''
            SELECT * FROM cupons
            WHERE qrcode = ? AND status IN ('pendente', 'parcial')
        ''', (qrcode,))
        cupom = cursor.fetchone()

        if not cupom:
            conn.close()
            return jsonify({'erro': 'Cupom não encontrado ou já utilizado por completo'}), 404

        # Valida validade: cupom vale apenas no dia em que foi gerado
        data_geracao = str(cupom['data_geracao'])[:10]
        if data_geracao != str(datetime.now().date()):
            conn.close()
            return jsonify({
                'erro': f'Cupom expirado (gerado em {data_geracao}). O cliente deve gerar um novo cupom hoje.'
            }), 400

        # Valida produto
        if cupom['produto_id'] != produto_id:
            conn.close()
            return jsonify({'erro': 'Produto não corresponde ao cupom'}), 400

        # Busca cliente e produto
        cursor.execute('''
            SELECT nome, desconto_tipo, desconto_valor
            FROM clientes
            WHERE id = ?
        ''', (cupom['cliente_id'],))
        cliente = cursor.fetchone()

        cursor.execute('''
            SELECT nome
            FROM produtos
            WHERE id = ?
        ''', (produto_id,))
        produto = cursor.fetchone()

        # Verifica saldo
        litros_restantes = cupom['quantidade_permitida'] - cupom['quantidade_utilizada']

        if litros_restantes <= 0:
            conn.close()
            return jsonify({'erro': 'Cupom já utilizado por completo hoje'}), 400

        if quantidade_agora > litros_restantes:
            conn.close()
            return jsonify({
                'erro': f'Excede o saldo do cupom: restam {litros_restantes:.2f} L '
                        f'e foram informados {quantidade_agora:.2f} L.',
                'limite': litros_restantes,
                'solicitado': quantidade_agora
            }), 400

        # Se a tela não mandou o valor, calcula pelo preço congelado no cupom.
        # Evita que um erro de digitação na pista vire um valor cobrado errado.
        if valor_sem_desconto <= 0:
            preco_congelado = cupom['preco_unitario'] or 0
            if preco_congelado <= 0:
                cursor.execute('SELECT preco_atual FROM produtos WHERE id = ?', (produto_id,))
                linha = cursor.fetchone()
                preco_congelado = (linha['preco_atual'] if linha else 0) or 0
            valor_sem_desconto = round(preco_congelado * quantidade_agora, 2)

        # Desconto: usa o valor CONGELADO no cupom (preço/desconto do momento da geração).
        # Se o cupom é antigo e não tem esse dado, cai no desconto do cliente.
        desconto_unitario = cupom['desconto_unitario'] or 0

        if desconto_unitario > 0:
            valor_desconto = desconto_unitario * quantidade_agora
        elif (cupom['desconto_tipo'] or cliente['desconto_tipo']) == 'percentual':
            perc = cupom['desconto_valor'] or cliente['desconto_valor'] or 0
            valor_desconto = valor_sem_desconto * (perc / 100)
        else:
            fixo = cupom['desconto_valor'] or cliente['desconto_valor'] or 0
            valor_desconto = fixo * quantidade_agora

        # nunca deixar o desconto passar do valor da compra
        valor_desconto = min(valor_desconto, valor_sem_desconto)
        valor_final = valor_sem_desconto - valor_desconto
        turno = obter_turno()

        # Registra abastecimento
        agora = datetime.now()
        cursor.execute('''
            INSERT INTO abastecimentos
            (cupom_id, cliente_id, produto_id, poster_id, data, hora, turno,
             quantidade, valor_original, valor_desconto, valor_final, registrado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cupom['id'],
            cupom['cliente_id'],
            produto_id,
            poster_id,
            agora.strftime('%Y-%m-%d'),
            agora.strftime('%H:%M:%S'),
            turno,
            quantidade_agora,
            valor_sem_desconto,
            valor_desconto,
            valor_final,
            request.admin['usuario']
        ))

        # Atualiza cupom
        nova_quantidade_utilizada = cupom['quantidade_utilizada'] + quantidade_agora

        # USO ÚNICO: o cupom fecha na primeira baixa, sobrando saldo ou não.
        # É decisão de negócio, não limitação técnica — o objetivo é que o
        # motorista encha o tanque de uma vez em vez de voltar três vezes no
        # dia por 10 litros. Para voltar a permitir uso em partes, basta
        # trocar USO_UNICO para False lá no começo do arquivo.
        if USO_UNICO:
            novo_status = 'completo'
        else:
            novo_status = ('completo' if nova_quantidade_utilizada >= cupom['quantidade_permitida']
                           else 'parcial')

        cursor.execute('''
            UPDATE cupons
            SET quantidade_utilizada = ?, status = ?, data_ultimo_uso = ?, turno_ultimo_uso = ?
            WHERE id = ?
        ''', (
            nova_quantidade_utilizada,
            novo_status,
            agora.strftime('%Y-%m-%d'),
            turno,
            cupom['id']
        ))

        # Deixa rastro de quem liberou o abastecimento na pista
        registrar_auditoria(
            cursor, request.admin, 'abastecimento',
            produto_id=produto_id,
            produto_nome=produto['nome'] if produto else None,
            detalhe=(f"{quantidade_agora:.2f} L para {cliente['nome']} no posto {poster_id} — "
                     f"cobrado R$ {valor_final:.2f} (desconto R$ {valor_desconto:.2f})")
        )

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': 'Abastecimento registrado!',
            'cliente': cliente['nome'],
            'produto': produto['nome'] if produto else 'N/A',
            'posto': poster_id,
            'registrado_por': request.admin['nome'] or request.admin['usuario'],
            'hora': agora.strftime('%H:%M'),
            'quantidade': quantidade_agora,
            'valor_original': round(valor_sem_desconto, 2),
            'valor_desconto': round(valor_desconto, 2),
            'valor_final': round(valor_final, 2),
            'cupom_status': novo_status,
            'quantidade_utilizada': nova_quantidade_utilizada,
            'quantidade_permitida': cupom['quantidade_permitida'],
            # Com uso único o cupom fecha aqui, sobrando saldo ou não — o
            # saldo deixa de existir e a tela não pode sugerir que resta algo.
            'quantidade_restante': (0 if USO_UNICO
                                    else litros_restantes - quantidade_agora),
            'uso_unico': USO_UNICO,
            'saldo_perdido': (round(litros_restantes - quantidade_agora, 2)
                              if USO_UNICO else 0)
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/relatorio', methods=['GET'])
@exige_admin
def relatorio_admin():
    """Retorna relatórios para admin"""
    try:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        turno = request.args.get('turno')
        poster_id = request.args.get('poster_id')
        produto_id = request.args.get('produto_id')

        conn = get_db()
        cursor = conn.cursor()

        query = 'SELECT * FROM abastecimentos WHERE 1=1'
        params = []

        if data_inicio:
            query += ' AND data >= ?'
            params.append(data_inicio)

        if data_fim:
            query += ' AND data <= ?'
            params.append(data_fim)

        if turno:
            query += ' AND turno = ?'
            params.append(turno)

        if poster_id:
            query += ' AND poster_id = ?'
            params.append(poster_id)

        if produto_id:
            query += ' AND produto_id = ?'
            params.append(produto_id)

        cursor.execute(query, params)
        abastecimentos = cursor.fetchall()

        # Agrupa dados
        total_quantidade = sum([row['quantidade'] for row in abastecimentos])
        total_original = sum([row['valor_original'] for row in abastecimentos])
        total_desconto = sum([row['valor_desconto'] for row in abastecimentos])
        total_final = sum([row['valor_final'] for row in abastecimentos])
        total_abastecimentos = len(abastecimentos)

        conn.close()

        return jsonify({
            'total_abastecimentos': total_abastecimentos,
            'total_quantidade': round(total_quantidade, 2),
            'total_valor_original': round(total_original, 2),
            'total_valor_desconto': round(total_desconto, 2),
            'total_valor_final': round(total_final, 2),
            'abastecimentos': [dict(row) for row in abastecimentos]
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/existe', methods=['GET'])
def admin_existe():
    """Informa se já há administrador cadastrado (para a tela decidir login x cadastro)."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) AS total FROM admin')
        total = cursor.fetchone()['total']
        conn.close()
        return jsonify({'existe': total > 0}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/setup', methods=['POST'])
def admin_setup():
    """Cria o PRIMEIRO administrador. Só funciona enquanto não existir nenhum."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''

        if len(usuario) < 3:
            return jsonify({'erro': 'Usuário deve ter ao menos 3 caracteres'}), 400
        if len(senha) < 8:
            return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) AS total FROM admin')
        if cursor.fetchone()['total'] > 0:
            conn.close()
            return jsonify({'erro': 'Já existe administrador cadastrado. Faça login.'}), 403

        cursor.execute(
            'INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, ativo) VALUES (?, ?, ?, ?, ?, ?)',
            (usuario, generate_password_hash(senha), data.get('poster_id') or 'AMBOS',
             'master', data.get('nome') or usuario, 1)
        )
        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Administrador Master criado! Faça login.'}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Login do administrador. Devolve um token válido por 12 horas."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, usuario, nome, senha_hash, poster_id, nivel, ativo
            FROM admin WHERE usuario = ?
        ''', (usuario,))
        admin = cursor.fetchone()

        if not admin or not check_password_hash(admin['senha_hash'], senha):
            conn.close()
            return jsonify({'erro': 'Usuário ou senha incorretos'}), 401

        if admin['ativo'] == 0:
            conn.close()
            return jsonify({'erro': 'Usuário desativado. Fale com o administrador Master.'}), 403

        token = str(uuid.uuid4())
        expira = (datetime.now() + timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('UPDATE admin SET token = ?, token_expira = ? WHERE id = ?',
                       (token, expira, admin['id']))
        conn.commit()
        conn.close()

        return jsonify({
            'token': token,
            'usuario': admin['usuario'],
            'nome': admin['nome'] or admin['usuario'],
            'nivel': admin['nivel'] or 'master',
            'poster_id': admin['poster_id'],
            'expira_em': expira
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/senha', methods=['POST'])
@exige_admin
def admin_trocar_senha():
    """Troca a senha do administrador logado."""
    try:
        data = request.get_json()
        senha_atual = data.get('senha_atual') or ''
        senha_nova = data.get('senha_nova') or ''

        if len(senha_nova) < 8:
            return jsonify({'erro': 'A nova senha deve ter ao menos 8 caracteres'}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT senha_hash FROM admin WHERE id = ?', (request.admin['id'],))
        atual = cursor.fetchone()

        if not check_password_hash(atual['senha_hash'], senha_atual):
            conn.close()
            return jsonify({'erro': 'Senha atual incorreta'}), 401

        cursor.execute('UPDATE admin SET senha_hash = ?, token = NULL WHERE id = ?',
                       (generate_password_hash(senha_nova), request.admin['id']))
        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Senha alterada. Faça login novamente.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== USUÁRIOS DO PAINEL (SÓ MASTER) ====================

@app.route('/api/admin/usuarios', methods=['GET'])
@exige_master
def admin_listar_usuarios():
    """Lista os usuários do painel."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, usuario, nome, nivel, poster_id, ativo, data_criacao
            FROM admin ORDER BY nivel, usuario
        ''')
        usuarios = cursor.fetchall()
        conn.close()

        return jsonify({'usuarios': [{
            'id': u['id'],
            'usuario': u['usuario'],
            'nome': u['nome'] or u['usuario'],
            'nivel': u['nivel'] or 'master',
            'poster_id': u['poster_id'],
            'ativo': u['ativo'] if u['ativo'] is not None else 1
        } for u in usuarios]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/usuarios', methods=['POST'])
@exige_master
def admin_criar_usuario():
    """Cria um usuário Master ou Caixa."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''
        nivel = data.get('nivel') or 'caixa'

        if len(usuario) < 3:
            return jsonify({'erro': 'Usuário deve ter ao menos 3 caracteres'}), 400
        if len(senha) < 8:
            return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400
        if nivel not in ('master', 'gerencia', 'caixa'):
            return jsonify({'erro': "Nível deve ser 'master', 'gerencia' ou 'caixa'"}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM admin WHERE usuario = ?', (usuario,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'erro': 'Esse usuário já existe'}), 400

        cursor.execute('''
            INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, ativo)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (usuario, generate_password_hash(senha), data.get('poster_id') or 'AMBOS',
              nivel, data.get('nome') or usuario, 1))

        conn.commit()
        conn.close()

        rotulos = {'master': 'Master (acesso total)',
                   'gerencia': 'Gerência (altera preços respeitando a margem mínima)',
                   'caixa': 'Caixa (somente consulta)'}
        rotulo = rotulos[nivel]
        return jsonify({'mensagem': f'Usuário {usuario} criado como {rotulo}'}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/usuarios/<int:usuario_id>', methods=['POST'])
@exige_master
def admin_alterar_usuario(usuario_id):
    """Ativa/desativa, troca nível ou redefine a senha de um usuário."""
    try:
        data = request.get_json() or {}

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, usuario, nivel FROM admin WHERE id = ?', (usuario_id,))
        alvo = cursor.fetchone()

        if not alvo:
            conn.close()
            return jsonify({'erro': 'Usuário não encontrado'}), 404

        # Não deixar o Master remover a si mesmo e ficar sem acesso
        if alvo['id'] == request.admin['id'] and (data.get('ativo') == 0 or data.get('nivel') in ('caixa', 'gerencia')):
            conn.close()
            return jsonify({'erro': 'Você não pode remover o próprio acesso Master'}), 400

        if 'nivel' in data:
            if data['nivel'] not in ('master', 'gerencia', 'caixa'):
                conn.close()
                return jsonify({'erro': "Nível deve ser 'master', 'gerencia' ou 'caixa'"}), 400
            cursor.execute('UPDATE admin SET nivel = ? WHERE id = ?', (data['nivel'], usuario_id))

        if 'ativo' in data:
            cursor.execute('UPDATE admin SET ativo = ?, token = NULL WHERE id = ?',
                           (int(data['ativo']), usuario_id))

        if data.get('senha_nova'):
            if len(data['senha_nova']) < 8:
                conn.close()
                return jsonify({'erro': 'A senha deve ter ao menos 8 caracteres'}), 400
            cursor.execute('UPDATE admin SET senha_hash = ?, token = NULL WHERE id = ?',
                           (generate_password_hash(data['senha_nova']), usuario_id))

        conn.commit()
        conn.close()

        return jsonify({'mensagem': f"Usuário {alvo['usuario']} atualizado"}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== PREÇOS E DESCONTOS (ADMIN) ====================

@app.route('/api/admin/produtos', methods=['GET'])
@exige_admin
def admin_listar_produtos():
    """Lista todos os produtos com preço, desconto e limite — para a tela de administrador."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, tipo, preco_atual, preco_custo, margem_minima,
                   unidade, icone, ativo,
                   desconto_valor, desconto_tipo, limite_litros, data_atualizacao
            FROM produtos
            ORDER BY tipo, id
        ''')
        produtos = cursor.fetchall()
        conn.close()

        lista = []
        for p in produtos:
            preco = p['preco_atual'] or 0
            custo = p['preco_custo'] or 0
            margem_min = p['margem_minima'] if p['margem_minima'] is not None else 10
            desconto = p['desconto_valor'] or 0
            por_unidade = desconto_por_unidade(preco, desconto, p['desconto_tipo'])
            preco_final = preco - por_unidade

            lista.append({
                'id': p['id'],
                'nome': p['nome'],
                'tipo': p['tipo'],
                'icone': p['icone'],
                'unidade': p['unidade'],
                'ativo': p['ativo'],
                'preco_custo': round(custo, 2),
                'margem_minima': margem_min,
                'preco_atual': round(preco, 2),
                'desconto_valor': desconto,
                'desconto_tipo': p['desconto_tipo'] or 'fixo',
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco_final, 2),
                'margem_reais': round(preco_final - custo, 2) if custo else None,
                'margem_percentual': round(((preco_final - custo) / custo) * 100, 1) if custo else None,
                # piso que a Gerência precisa respeitar
                'preco_minimo_gerencia': round(custo * (1 + margem_min / 100), 2) if custo else None,
                'limite_litros': p['limite_litros'] or 0,
                'data_atualizacao': p['data_atualizacao']
            })

        return jsonify({'produtos': lista, 'meu_nivel': request.admin['nivel']}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/produtos/atualizar', methods=['POST'])
@exige_gerencia
def admin_atualizar_produtos():
    """Atualiza preço, desconto e limite de um ou vários produtos de uma vez.

    Espera: {"produtos": [{"id": 1, "preco_atual": 5.89, "desconto_valor": 0.30,
                           "desconto_tipo": "fixo", "limite_litros": 50, "ativo": 1}, ...]}
    """
    try:
        data = request.get_json()
        produtos = data.get('produtos') or []

        if not produtos:
            return jsonify({'erro': 'Nenhum produto enviado'}), 400

        conn = get_db()
        cursor = conn.cursor()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        atualizados = []

        nivel = request.admin['nivel']
        mudancas = []   # guarda o que mudou para gravar na auditoria depois do commit

        for p in produtos:
            produto_id = p.get('id')
            if not produto_id:
                continue

            cursor.execute('''
                SELECT id, nome, preco_atual, preco_custo, margem_minima,
                       desconto_valor, desconto_tipo, limite_litros, ativo
                FROM produtos WHERE id = ?
            ''', (produto_id,))
            atual = cursor.fetchone()
            if not atual:
                continue

            nome = (p.get('nome') or atual['nome']).strip()
            if len(nome) < 2:
                conn.close()
                return jsonify({'erro': 'O nome do produto não pode ficar vazio'}), 400

            preco = float(p.get('preco_atual', atual['preco_atual']))
            desconto = float(p.get('desconto_valor', atual['desconto_valor'] or 0))
            tipo = p.get('desconto_tipo', atual['desconto_tipo'] or 'fixo')
            limite = float(p.get('limite_litros', atual['limite_litros'] or 50))
            ativo = int(p.get('ativo', atual['ativo'] if atual['ativo'] is not None else 1))

            # custo e margem mínima: somente o Master altera
            custo_atual = atual['preco_custo'] or 0
            margem_atual = atual['margem_minima'] if atual['margem_minima'] is not None else 10

            if nivel == 'master':
                custo = float(p.get('preco_custo', custo_atual))
                margem = float(p.get('margem_minima', margem_atual))
            else:
                custo, margem = custo_atual, margem_atual
                if ('preco_custo' in p and float(p['preco_custo']) != custo_atual) or \
                   ('margem_minima' in p and float(p['margem_minima']) != margem_atual):
                    conn.close()
                    return jsonify({
                        'erro': 'Somente o administrador Master pode alterar preço de custo e margem mínima.'
                    }), 403

            if preco < 0 or desconto < 0 or limite < 0 or custo < 0 or margem < 0:
                conn.close()
                return jsonify({'erro': f'Valores negativos não são permitidos ({atual["nome"]})'}), 400

            if tipo not in ('fixo', 'percentual'):
                conn.close()
                return jsonify({'erro': "Tipo de desconto deve ser 'fixo' ou 'percentual'"}), 400

            if tipo == 'percentual' and desconto > 100:
                conn.close()
                return jsonify({'erro': f'{nome}: desconto percentual não pode passar de 100%'}), 400

            por_unidade = desconto_por_unidade(preco, desconto, tipo)

            # ===== TRAVA DE MARGEM =====
            erro = validar_margem(nivel, nome, preco, custo, margem, por_unidade)
            if erro:
                registrar_auditoria(cursor, request.admin, 'BLOQUEIO', produto_id, nome,
                                    'desconto', atual['desconto_valor'], desconto, erro)
                conn.commit()
                conn.close()
                return jsonify({'erro': erro, 'bloqueado': True}), 400

            # o que mudou de fato
            for campo, antes, depois in [
                ('nome', atual['nome'], nome),
                ('preco_atual', atual['preco_atual'], preco),
                ('preco_custo', custo_atual, custo),
                ('margem_minima', margem_atual, margem),
                ('desconto_valor', atual['desconto_valor'] or 0, desconto),
                ('desconto_tipo', atual['desconto_tipo'] or 'fixo', tipo),
                ('limite_litros', atual['limite_litros'] or 0, limite),
                ('ativo', atual['ativo'], ativo),
            ]:
                if str(antes) != str(depois):
                    mudancas.append((produto_id, nome, campo, antes, depois))

            cursor.execute('''
                UPDATE produtos
                SET nome = ?, preco_atual = ?, preco_custo = ?, margem_minima = ?,
                    desconto_valor = ?, desconto_tipo = ?, limite_litros = ?,
                    ativo = ?, data_atualizacao = ?
                WHERE id = ?
            ''', (nome, preco, custo, margem, desconto, tipo, limite, ativo, agora, produto_id))

            preco_final = preco - por_unidade
            atualizados.append({
                'id': produto_id,
                'nome': nome,
                'preco_atual': round(preco, 2),
                'preco_custo': round(custo, 2),
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco_final, 2),
                'margem_reais': round(preco_final - custo, 2) if custo else None,
                'margem_percentual': round(((preco_final - custo) / custo) * 100, 1) if custo else None
            })

        for produto_id, nome, campo, antes, depois in mudancas:
            registrar_auditoria(cursor, request.admin, 'ALTERACAO', produto_id, nome,
                                campo, antes, depois)

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': f'{len(atualizados)} produto(s) atualizado(s)',
            'produtos': atualizados,
            'alteracoes_registradas': len(mudancas)
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== AUDITORIA ====================

@app.route('/api/admin/auditoria', methods=['GET'])
@exige_admin
def admin_auditoria():
    """Histórico de alterações de preço e desconto.

    Parâmetros: data_inicio, data_fim, usuario, acao (ALTERACAO/BLOQUEIO), limite
    """
    try:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        usuario = request.args.get('usuario')
        acao = request.args.get('acao')
        limite = min(int(request.args.get('limite', 200)), 1000)

        conn = get_db()
        cursor = conn.cursor()

        query = 'SELECT * FROM auditoria WHERE 1=1'
        params = []

        if data_inicio:
            query += ' AND data_hora >= ?'
            params.append(f'{data_inicio} 00:00:00')

        if data_fim:
            query += ' AND data_hora <= ?'
            params.append(f'{data_fim} 23:59:59')

        if usuario:
            query += ' AND admin_usuario = ?'
            params.append(usuario)

        if acao:
            query += ' AND acao = ?'
            params.append(acao)

        query += ' ORDER BY id DESC'
        cursor.execute(query, params)
        registros = cursor.fetchall()[:limite]
        conn.close()

        rotulos = {
            'nome': 'Nome',
            'preco_atual': 'Preço de venda',
            'preco_custo': 'Preço de custo',
            'margem_minima': 'Margem mínima (%)',
            'desconto_valor': 'Desconto',
            'desconto_tipo': 'Tipo de desconto',
            'limite_litros': 'Limite',
            'ativo': 'Situação'
        }

        return jsonify({'registros': [{
            'id': r['id'],
            'data_hora': r['data_hora'],
            'usuario': r['admin_usuario'],
            'nivel': r['admin_nivel'],
            'acao': r['acao'],
            'produto': r['produto_nome'],
            'campo': r['campo'],
            'campo_rotulo': rotulos.get(r['campo'], r['campo']),
            'valor_anterior': r['valor_anterior'],
            'valor_novo': r['valor_novo'],
            'detalhe': r['detalhe']
        } for r in registros]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== FECHAMENTO DE CAIXA ====================

@app.route('/api/admin/caixa', methods=['GET'])
@exige_admin
def admin_fechamento_caixa():
    """Fechamento de caixa: abastecimentos, litros e R$ por turno.

    Parâmetros: data (YYYY-MM-DD, padrão hoje), turno (opcional), poster_id (opcional)
    """
    try:
        data_ref = request.args.get('data') or datetime.now().strftime('%Y-%m-%d')
        turno_filtro = request.args.get('turno')
        poster_id = request.args.get('poster_id')
        hora_inicio = request.args.get('hora_inicio')   # ex: 14:00
        hora_fim = request.args.get('hora_fim')         # ex: 18:00
        produto_filtro = request.args.get('produto_id')

        def normaliza_hora(h):
            if not h:
                return None
            h = h.strip()
            return h if len(h) == 8 else f'{h}:00'

        hora_inicio = normaliza_hora(hora_inicio)
        hora_fim = normaliza_hora(hora_fim)

        conn = get_db()
        cursor = conn.cursor()

        query = '''
            SELECT a.*, p.nome AS produto_nome, p.unidade, p.icone, c.nome AS cliente_nome
            FROM abastecimentos a
            LEFT JOIN produtos p ON p.id = a.produto_id
            LEFT JOIN clientes c ON c.id = a.cliente_id
            WHERE a.data = ?
        '''
        params = [data_ref]

        if turno_filtro:
            query += ' AND a.turno = ?'
            params.append(turno_filtro)

        if poster_id:
            query += ' AND a.poster_id = ?'
            params.append(poster_id)

        if produto_filtro:
            query += ' AND a.produto_id = ?'
            params.append(int(produto_filtro))

        if hora_inicio:
            query += ' AND a.hora >= ?'
            params.append(hora_inicio)

        if hora_fim:
            query += ' AND a.hora <= ?'
            params.append(hora_fim)

        query += ' ORDER BY a.hora'
        cursor.execute(query, params)
        registros = cursor.fetchall()
        conn.close()

        turnos = {}
        for r in registros:
            turno = r['turno'] or 'Sem turno'
            t = turnos.setdefault(turno, {
                'turno': turno,
                'abastecimentos': 0,
                'litros': 0.0,
                'valor_bruto': 0.0,
                'desconto_concedido': 0.0,
                'valor_recebido': 0.0,
                'por_produto': {},
                'por_posto': {}
            })

            t['abastecimentos'] += 1
            t['litros'] += r['quantidade'] or 0
            t['valor_bruto'] += r['valor_original'] or 0
            t['desconto_concedido'] += r['valor_desconto'] or 0
            t['valor_recebido'] += r['valor_final'] or 0

            prod = r['produto_nome'] or f"Produto {r['produto_id']}"
            pp = t['por_produto'].setdefault(prod, {
                'produto': prod,
                'icone': r['icone'],
                'unidade': r['unidade'] or 'L',
                'abastecimentos': 0,
                'litros': 0.0,
                'valor_recebido': 0.0
            })
            pp['abastecimentos'] += 1
            pp['litros'] += r['quantidade'] or 0
            pp['valor_recebido'] += r['valor_final'] or 0

            posto = r['poster_id'] or 'N/A'
            ps = t['por_posto'].setdefault(posto, {
                'posto': posto, 'abastecimentos': 0, 'litros': 0.0, 'valor_recebido': 0.0
            })
            ps['abastecimentos'] += 1
            ps['litros'] += r['quantidade'] or 0
            ps['valor_recebido'] += r['valor_final'] or 0

        def arredonda(d, campos):
            for c in campos:
                d[c] = round(d[c], 2)
            return d

        lista_turnos = []
        for t in turnos.values():
            t['por_produto'] = [arredonda(x, ['litros', 'valor_recebido']) for x in t['por_produto'].values()]
            t['por_posto'] = [arredonda(x, ['litros', 'valor_recebido']) for x in t['por_posto'].values()]
            lista_turnos.append(arredonda(t, ['litros', 'valor_bruto', 'desconto_concedido', 'valor_recebido']))

        lista_turnos.sort(key=lambda x: x['turno'])

        total = {
            'abastecimentos': sum(t['abastecimentos'] for t in lista_turnos),
            'litros': round(sum(t['litros'] for t in lista_turnos), 2),
            'valor_bruto': round(sum(t['valor_bruto'] for t in lista_turnos), 2),
            'desconto_concedido': round(sum(t['desconto_concedido'] for t in lista_turnos), 2),
            'valor_recebido': round(sum(t['valor_recebido'] for t in lista_turnos), 2),
        }

        return jsonify({
            'data': data_ref,
            'turno_atual': obter_turno(),
            'filtros': {
                'hora_inicio': hora_inicio,
                'hora_fim': hora_fim,
                'poster_id': poster_id,
                'turno': turno_filtro,
                'produto_id': produto_filtro
            },
            'total': total,
            'turnos': lista_turnos,
            'detalhes': [{
                'hora': r['hora'],
                'turno': r['turno'],
                'posto': r['poster_id'],
                'cliente': r['cliente_nome'],
                'produto': r['produto_nome'],
                'quantidade': r['quantidade'],
                'valor_original': r['valor_original'],
                'valor_desconto': r['valor_desconto'],
                'valor_final': r['valor_final']
            } for r in registros]
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/descontos', methods=['GET'])
@exige_admin
def get_descontos_ocupacoes():
    """Obtém descontos por ocupação"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT ocupacao, desconto_valor
            FROM clientes
            WHERE ocupacao IS NOT NULL
            ORDER BY ocupacao
        ''')
        descontos = cursor.fetchall()
        conn.close()

        return jsonify({
            'descontos_por_ocupacao': [dict(row) for row in descontos]
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/descontos/atualizar', methods=['POST'])
@exige_master
def atualizar_descontos():
    """Atualiza desconto para todos os clientes de uma ocupação"""
    try:
        data = request.get_json()
        ocupacao = data.get('ocupacao')  # 'Táxi', 'Uber', 'Outro'
        novo_valor = float(data.get('valor'))  # R$ 1.00

        if not ocupacao or novo_valor < 0:
            return jsonify({'erro': 'Ocupação e valor inválidos'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE clientes
            SET desconto_valor = ?, desconto_tipo = 'fixo'
            WHERE ocupacao = ?
        ''', (novo_valor, ocupacao))

        conn.commit()
        clientes_atualizados = cursor.rowcount
        conn.close()

        return jsonify({
            'mensagem': f'Desconto atualizado para {clientes_atualizados} clientes de {ocupacao}',
            'ocupacao': ocupacao,
            'novo_valor': novo_valor,
            'clientes_atualizados': clientes_atualizados
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/suspeitas', methods=['GET'])
@exige_gerencia
def admin_suspeitas():
    """Padrões que merecem um olhar — não são acusações, são pistas.

    A fraude que a foto do comprovante não pega é a de dentro: frentista que
    cadastra amigos e libera desconto para eles. Isso não aparece num
    abastecimento isolado, só no padrão ao longo dos dias.
    """
    try:
        dias = int(request.args.get('dias', 30))
        limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

        conn = get_db()
        cursor = conn.cursor()
        achados = {}

        # 1. Mesma placa em vários cadastros.
        # Táxi dividido por turno é legítimo; três contas no mesmo carro, nem tanto.
        cursor.execute('''
            SELECT placa, COUNT(*) AS qtd
            FROM clientes
            WHERE placa IS NOT NULL AND placa <> ''
            GROUP BY placa
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        ''')
        placas = []
        for linha in cursor.fetchall():
            cursor.execute('''
                SELECT id, nome, ocupacao, data_criacao
                FROM clientes WHERE placa = ? ORDER BY id
            ''', (linha['placa'],))
            placas.append({
                'placa': linha['placa'],
                'quantidade': linha['qtd'],
                'clientes': [
                    {'id': c['id'], 'nome': c['nome'], 'ocupacao': c['ocupacao'],
                     'cadastrado_em': str(c['data_criacao'])[:10]}
                    for c in cursor.fetchall()
                ]
            })
        achados['placas_repetidas'] = placas

        # 2. Motorista que só abastece com um frentista específico.
        # Quem abastece de verdade pega turnos diferentes; quem tem combinado, não.
        cursor.execute('''
            SELECT a.cliente_id, cl.nome AS cliente_nome, cl.placa,
                   COUNT(*) AS total,
                   COUNT(DISTINCT a.registrado_por) AS operadores,
                   MIN(a.registrado_por) AS operador
            FROM abastecimentos a
            JOIN clientes cl ON cl.id = a.cliente_id
            WHERE a.data >= ? AND a.registrado_por IS NOT NULL
            GROUP BY a.cliente_id, cl.nome, cl.placa
            HAVING COUNT(*) >= 5 AND COUNT(DISTINCT a.registrado_por) = 1
            ORDER BY COUNT(*) DESC
        ''', (limite,))
        achados['sempre_mesmo_frentista'] = [
            {'cliente_id': l['cliente_id'], 'cliente_nome': l['cliente_nome'],
             'placa': l['placa'], 'abastecimentos': l['total'], 'frentista': l['operador']}
            for l in cursor.fetchall()
        ]

        # 3. Cadastros em rajada — vários no mesmo dia costuma ser mutirão de amigos
        cursor.execute('''
            SELECT substr(CAST(data_criacao AS VARCHAR), 1, 10) AS dia, COUNT(*) AS qtd
            FROM clientes
            WHERE substr(CAST(data_criacao AS VARCHAR), 1, 10) >= ?
            GROUP BY substr(CAST(data_criacao AS VARCHAR), 1, 10)
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC
        ''', (limite,))
        achados['cadastros_em_rajada'] = [
            {'dia': l['dia'], 'quantidade': l['qtd']} for l in cursor.fetchall()
        ]

        # 4. Quem abastece com desconto quase todo dia
        cursor.execute('''
            SELECT a.cliente_id, cl.nome AS cliente_nome, cl.placa, cl.ocupacao,
                   COUNT(DISTINCT a.data) AS dias,
                   SUM(a.quantidade) AS litros,
                   SUM(a.valor_desconto) AS desconto
            FROM abastecimentos a
            JOIN clientes cl ON cl.id = a.cliente_id
            WHERE a.data >= ?
            GROUP BY a.cliente_id, cl.nome, cl.placa, cl.ocupacao
            ORDER BY SUM(a.valor_desconto) DESC
        ''', (limite,))
        campeoes = []
        for l in cursor.fetchall()[:15]:
            campeoes.append({
                'cliente_id': l['cliente_id'], 'cliente_nome': l['cliente_nome'],
                'placa': l['placa'], 'ocupacao': l['ocupacao'],
                'dias_com_abastecimento': l['dias'],
                'litros': round(l['litros'] or 0, 2),
                'desconto_total': round(l['desconto'] or 0, 2)
            })
        achados['maiores_beneficiados'] = campeoes

        # 5. Trocas de placa — o movimento típico de conta emprestada
        cursor.execute('''
            SELECT data_hora, admin_usuario, valor_anterior, valor_novo
            FROM auditoria
            WHERE acao = 'troca_placa' AND data_hora >= ?
            ORDER BY data_hora DESC
        ''', (limite,))
        trocas = [
            {'quando': l['data_hora'], 'cliente': l['admin_usuario'],
             'de': l['valor_anterior'], 'para': l['valor_novo']}
            for l in cursor.fetchall()
        ]
        achados['trocas_de_placa'] = trocas[:50]

        conn.close()

        achados['periodo_dias'] = dias
        achados['total_alertas'] = (
            len(achados['placas_repetidas'])
            + len(achados['sempre_mesmo_frentista'])
            + len(achados['cadastros_em_rajada'])
        )
        return jsonify(achados), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cliente/<int:cliente_id>/comprovante', methods=['GET'])
@exige_gerencia
def admin_comprovante(cliente_id):
    """Devolve o comprovante enviado no cadastro, para conferência manual."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT nome, ocupacao, placa, registro_tipo, registro_numero,
                   empresa_convenio, foto_comprovante, foto_comprovante_tipo,
                   data_foto_comprovante
            FROM clientes WHERE id = ?
        ''', (cliente_id,))
        c = cursor.fetchone()
        conn.close()

        if not c:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        return jsonify({
            'nome': c['nome'],
            'ocupacao': c['ocupacao'],
            'placa': c['placa'],
            'registro_tipo': c['registro_tipo'],
            'registro_numero': c['registro_numero'],
            'empresa_convenio': c['empresa_convenio'],
            'tipo_comprovante': c['foto_comprovante_tipo'],
            'enviado_em': c['data_foto_comprovante'],
            'imagem': c['foto_comprovante']
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cupons-do-dia', methods=['GET'])
@exige_admin
def admin_cupons_do_dia():
    """
    Todo o movimento de cupons do dia, para a tela que fica aberta no caixa.

    Nível caixa também enxerga: quem opera a bomba precisa ver o que está
    valendo agora. A trava de reuso não está aqui — está no saldo gravado no
    banco, que /api/cupom/usar confere a cada baixa. Esta tela é para
    enxergar o movimento e achar um código depressa.
    """
    try:
        data_ref = (request.args.get('data') or '').strip() or datetime.now().strftime('%Y-%m-%d')

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.id, c.qrcode, c.status, c.data_geracao,
                   c.quantidade_permitida, c.quantidade_utilizada,
                   c.data_ultimo_uso, c.turno_ultimo_uso,
                   c.preco_unitario, c.desconto_unitario,
                   p.nome AS produto_nome, p.icone AS produto_icone, p.unidade,
                   cl.id AS cliente_id, cl.nome AS cliente_nome, cl.placa,
                   cl.ocupacao, cl.empresa_convenio
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            LEFT JOIN clientes cl ON cl.id = c.cliente_id
            WHERE c.data_geracao = ?
            ORDER BY c.id DESC
        ''', (data_ref,))
        linhas = cursor.fetchall()

        # Onde cada cupom foi abastecido. Um cupom pode ser usado em partes,
        # inclusive nos dois postos — por isso a lista de postos, não um só.
        cursor.execute('''
            SELECT cupom_id, poster_id, COUNT(*) AS vezes,
                   MAX(hora) AS ultima_hora, MAX(registrado_por) AS ultimo_frentista
            FROM abastecimentos
            WHERE data = ? AND cupom_id IS NOT NULL
            GROUP BY cupom_id, poster_id
        ''', (data_ref,))
        por_cupom = {}
        for a in cursor.fetchall():
            registro = por_cupom.setdefault(a['cupom_id'], {'postos': [], 'vezes': 0,
                                                            'ultima_hora': None,
                                                            'ultimo_frentista': None})
            registro['postos'].append(a['poster_id'])
            registro['vezes'] += a['vezes'] or 0
            if not registro['ultima_hora'] or (a['ultima_hora'] or '') > registro['ultima_hora']:
                registro['ultima_hora'] = a['ultima_hora']
                registro['ultimo_frentista'] = a['ultimo_frentista']
        conn.close()

        cupons = []
        total_emitidos = total_parciais = total_esgotados = 0
        litros_abastecidos = desconto_concedido = 0.0

        for c in linhas:
            permitida = c['quantidade_permitida'] or 0
            utilizada = c['quantidade_utilizada'] or 0
            restante = round(permitida - utilizada, 2)
            desconto_unit = c['desconto_unitario'] or 0
            uso = por_cupom.get(c['id'], {})

            situacao = (c['status'] or 'pendente').lower()
            if situacao == 'completo' or restante <= 0:
                rotulo, total_esgotados = 'esgotado', total_esgotados + 1
                # Com uso único o cupom fecha sobrando litros. Mostrar "saldo
                # 30 L" num cupom encerrado faria o caixa tentar uma baixa que
                # o servidor vai recusar.
                restante = 0
            elif utilizada > 0:
                rotulo, total_parciais = 'parcial', total_parciais + 1
            else:
                rotulo, total_emitidos = 'emitido', total_emitidos + 1

            litros_abastecidos += utilizada
            desconto_concedido += utilizada * desconto_unit

            cupons.append({
                'cupom_id': c['id'],
                'codigo': c['qrcode'],
                'situacao': rotulo,
                'cliente_id': c['cliente_id'],
                'cliente_nome': c['cliente_nome'],
                'placa': c['placa'],
                'ocupacao': c['ocupacao'],
                'empresa_convenio': c['empresa_convenio'],
                'produto_nome': c['produto_nome'],
                'produto_icone': c['produto_icone'],
                'unidade': c['unidade'] or 'L',
                'quantidade_permitida': round(permitida, 2),
                'quantidade_utilizada': round(utilizada, 2),
                'quantidade_restante': restante,
                'preco_unitario': round(c['preco_unitario'] or 0, 2),
                'desconto_por_unidade': round(desconto_unit, 2),
                'economia_ate_agora': round(utilizada * desconto_unit, 2),
                'postos': sorted(set(p for p in uso.get('postos', []) if p)),
                'vezes_abastecido': uso.get('vezes', 0),
                'ultima_hora': uso.get('ultima_hora'),
                'ultimo_frentista': uso.get('ultimo_frentista')
            })

        return jsonify({
            'data': data_ref,
            'cupons': cupons,
            'resumo': {
                'total': len(cupons),
                'emitidos': total_emitidos,
                'parciais': total_parciais,
                'esgotados': total_esgotados,
                'litros_abastecidos': round(litros_abastecidos, 2),
                'desconto_concedido': round(desconto_concedido, 2)
            }
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==========================================================================
# CONVÊNIO COM EMPRESAS — lista fechada + alçada de liberação
# ==========================================================================

@app.route('/api/empresas-convenio', methods=['GET'])
def listar_empresas_convenio_publico():
    """
    Lista para o menu do cadastro. Público de propósito — quem vai se
    cadastrar ainda não tem login.

    Devolve só id e nome: CNPJ, domínio e limite são informação interna e não
    servem de nada para quem está preenchendo o formulário.
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, nome FROM empresas_convenio WHERE ativo = 1 ORDER BY nome'
        )
        empresas = [{'id': e['id'], 'nome': e['nome']} for e in cursor.fetchall()]
        conn.close()
        return jsonify({'empresas': empresas}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio', methods=['GET'])
@exige_gerencia
def admin_listar_empresas_convenio():
    """Empresas conveniadas, com quantos funcionários já se cadastraram."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT e.id, e.nome, e.cnpj, e.dominio_email, e.limite_funcionarios,
                   e.ativo, e.observacao, e.criado_por, e.data_criacao,
                   (SELECT COUNT(*) FROM clientes c
                     WHERE c.empresa_convenio_id = e.id AND c.status = 'ativo') AS aprovados,
                   (SELECT COUNT(*) FROM clientes c
                     WHERE c.empresa_convenio_id = e.id AND c.status = 'pendente') AS pendentes
            FROM empresas_convenio e
            ORDER BY e.ativo DESC, e.nome
        ''')
        empresas = []
        for e in cursor.fetchall():
            empresas.append({
                'id': e['id'],
                'nome': e['nome'],
                'cnpj': formatar_cnpj(e['cnpj']),
                'dominio_email': e['dominio_email'],
                'limite_funcionarios': e['limite_funcionarios'] or 0,
                'ativo': bool(e['ativo']),
                'observacao': e['observacao'],
                'criado_por': e['criado_por'],
                'data_criacao': e['data_criacao'],
                'aprovados': e['aprovados'] or 0,
                'pendentes': e['pendentes'] or 0
            })
        conn.close()
        return jsonify({'empresas': empresas}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio', methods=['POST'])
@exige_gerencia
def admin_criar_empresa_convenio():
    """Cadastra uma empresa que assinou convênio."""
    try:
        data = request.get_json() or {}
        nome = (data.get('nome') or '').strip()
        cnpj = normalizar_cnpj(data.get('cnpj'))
        dominio = (data.get('dominio_email') or '').strip().lower().lstrip('@') or None

        try:
            limite = int(data.get('limite_funcionarios') or 0)
        except (TypeError, ValueError):
            limite = 0
        if limite < 0:
            limite = 0

        if len(nome) < 3:
            return jsonify({'erro': 'Informe o nome da empresa.'}), 400
        if not validar_cnpj(cnpj):
            return jsonify({'erro': 'CNPJ inválido. Confira os números.'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome FROM empresas_convenio WHERE cnpj = ?', (cnpj,))
        ja = cursor.fetchone()
        if ja:
            conn.close()
            return jsonify({'erro': f'Esse CNPJ já está cadastrado como "{ja["nome"]}".'}), 400

        cursor.execute('''
            INSERT INTO empresas_convenio
            (nome, cnpj, dominio_email, limite_funcionarios, ativo, observacao, criado_por)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (nome, cnpj, dominio, limite,
              (data.get('observacao') or '').strip() or None,
              request.admin['usuario']))

        empresa_id = cursor.lastrowid
        registrar_auditoria(
            cursor, request.admin, 'convenio_empresa_criada',
            campo='empresa', valor_novo=nome,
            detalhe=f'CNPJ {formatar_cnpj(cnpj)} | limite {limite or "sem limite"}'
        )
        conn.commit()
        conn.close()

        return jsonify({'mensagem': f'Convênio da {nome} cadastrado.',
                        'empresa_id': empresa_id}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio/<int:empresa_id>', methods=['POST'])
@exige_gerencia
def admin_alterar_empresa_convenio(empresa_id):
    """Ativa/desativa o convênio ou ajusta limite e domínio."""
    try:
        data = request.get_json() or {}
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, ativo FROM empresas_convenio WHERE id = ?',
                       (empresa_id,))
        empresa = cursor.fetchone()
        if not empresa:
            conn.close()
            return jsonify({'erro': 'Empresa não encontrada'}), 404

        if 'ativo' in data:
            novo = 1 if data['ativo'] in (True, 1, '1', 'true') else 0
            cursor.execute('UPDATE empresas_convenio SET ativo = ? WHERE id = ?',
                           (novo, empresa_id))
            registrar_auditoria(
                cursor, request.admin,
                'convenio_ativado' if novo else 'convenio_encerrado',
                campo='empresa', valor_anterior=empresa['nome'],
                detalhe='Convênio ' + ('reativado' if novo else 'encerrado')
            )

        if 'limite_funcionarios' in data:
            try:
                limite = max(0, int(data['limite_funcionarios'] or 0))
            except (TypeError, ValueError):
                limite = 0
            cursor.execute(
                'UPDATE empresas_convenio SET limite_funcionarios = ? WHERE id = ?',
                (limite, empresa_id))

        if 'dominio_email' in data:
            dom = (data['dominio_email'] or '').strip().lower().lstrip('@') or None
            cursor.execute('UPDATE empresas_convenio SET dominio_email = ? WHERE id = ?',
                           (dom, empresa_id))

        conn.commit()
        conn.close()
        return jsonify({'mensagem': 'Convênio atualizado.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cadastros-pendentes', methods=['GET'])
@exige_gerencia
def admin_cadastros_pendentes():
    """Fila de cadastros de convênio aguardando liberação."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.id, c.nome, c.cpf, c.email, c.tel, c.ocupacao, c.placa,
                   c.empresa_convenio, c.empresa_convenio_id, c.data_criacao,
                   c.foto_comprovante_tipo,
                   e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
                   e.dominio_email AS empresa_dominio
            FROM clientes c
            LEFT JOIN empresas_convenio e ON e.id = c.empresa_convenio_id
            WHERE c.status = 'pendente'
            ORDER BY c.data_criacao
        ''')

        pendentes = []
        total_so_master = 0
        for c in cursor.fetchall():
            email = (c['email'] or '').lower()
            dominio = (c['empresa_dominio'] or '').strip().lower().lstrip('@')
            so_master = exige_alcada_master(c['email'], c['empresa_dominio'])
            if so_master:
                total_so_master += 1
            pendentes.append({
                'exige_master': so_master,
                'empresa_dominio': dominio or None,
                'id': c['id'],
                'nome': c['nome'],
                'cpf': c['cpf'],
                'email': c['email'],
                'tel': c['tel'],
                'placa': c['placa'],
                'empresa': c['empresa_nome'] or c['empresa_convenio'],
                'empresa_cnpj': formatar_cnpj(c['empresa_cnpj']) if c['empresa_cnpj'] else None,
                'cadastrado_em': c['data_criacao'],
                'tem_comprovante': bool(c['foto_comprovante_tipo']),
                # Sinal de apoio para a decisão, não veredito automático.
                'email_corporativo': bool(dominio and email.endswith('@' + dominio))
            })
        conn.close()
        return jsonify({
            'pendentes': pendentes,
            'total': len(pendentes),
            'total_exige_master': total_so_master,
            'meu_nivel': request.admin['nivel']
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cadastros/<int:cliente_id>/decidir', methods=['POST'])
@exige_gerencia
def admin_decidir_cadastro(cliente_id):
    """
    Aprova ou recusa um cadastro pendente. Fica tudo na auditoria: quem
    liberou, quando e por quê.
    """
    try:
        data = request.get_json() or {}
        decisao = (data.get('decisao') or '').strip().lower()
        motivo = (data.get('motivo') or '').strip() or None

        if decisao not in ('aprovar', 'recusar'):
            return jsonify({'erro': "Decisão deve ser 'aprovar' ou 'recusar'."}), 400
        if decisao == 'recusar' and not motivo:
            return jsonify({'erro': 'Escreva o motivo da recusa.'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.id, c.nome, c.status, c.email, c.empresa_convenio,
                   e.dominio_email AS empresa_dominio
            FROM clientes c
            LEFT JOIN empresas_convenio e ON e.id = c.empresa_convenio_id
            WHERE c.id = ?
        ''', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        if (cliente['status'] or '').lower() != 'pendente':
            conn.close()
            return jsonify({
                'erro': f'Esse cadastro já foi analisado (situação atual: {cliente["status"]}).'
            }), 400

        # Exceção ao e-mail corporativo é alçada do Master. A gerência aprova o
        # caso normal; abrir mão da única prova objetiva de vínculo não é
        # decisão de quem está no balcão.
        if (decisao == 'aprovar'
                and exige_alcada_master(cliente['email'], cliente['empresa_dominio'])
                and request.admin['nivel'] != 'master'):
            conn.close()
            return jsonify({
                'erro': f'{cliente["nome"]} não usou o e-mail corporativo '
                        f'(@{(cliente["empresa_dominio"] or "").lstrip("@")}). '
                        f'Só o administrador Master pode liberar essa exceção.'
            }), 403

        novo_status = 'ativo' if decisao == 'aprovar' else 'recusado'
        agora_iso = datetime.now().isoformat()

        cursor.execute('''
            UPDATE clientes
            SET status = ?, aprovado_por = ?, data_aprovacao = ?, motivo_recusa = ?
            WHERE id = ?
        ''', (novo_status, request.admin['usuario'], agora_iso, motivo, cliente_id))

        registrar_auditoria(
            cursor, request.admin,
            'cadastro_aprovado' if decisao == 'aprovar' else 'cadastro_recusado',
            campo='cliente', valor_anterior='pendente', valor_novo=novo_status,
            detalhe=f'{cliente["nome"]} — convênio {cliente["empresa_convenio"] or "—"}'
                    + (f' | motivo: {motivo}' if motivo else '')
        )

        conn.commit()
        conn.close()

        if decisao == 'aprovar':
            return jsonify({'mensagem': f'{cliente["nome"]} liberado. Já pode gerar cupons.',
                            'status': novo_status}), 200
        return jsonify({'mensagem': f'Cadastro de {cliente["nome"]} recusado.',
                        'status': novo_status}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'OK',
        'timestamp': datetime.now().isoformat(),
        'versao': '2.1'
    }), 200

if __name__ == '__main__':
    # Debug só na sua máquina. Em produção (Render) fica desligado,
    # senão o Flask expõe um console que executa código no servidor.
    em_producao = bool(os.environ.get('DATABASE_URL') or os.environ.get('RENDER'))
    porta = int(os.environ.get('PORT', 5000))

    app.run(debug=not em_producao, host='0.0.0.0', port=porta)
