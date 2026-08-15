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
    """Somente o nível Master — alterar preços, descontos e usuários."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        if admin['nivel'] != 'master':
            return jsonify({
                'erro': 'Permissão negada. Seu acesso é de consulta (Caixa) e não permite alterações.'
            }), 403
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


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

        cpf = re.sub(r'\D', '', data.get('cpf'))

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM clientes WHERE cpf = ?', (cpf,))
        if cursor.fetchone():
            return jsonify({'erro': 'CPF já cadastrado'}), 400

        cursor.execute('SELECT id FROM clientes WHERE email = ?', (data.get('email'),))
        if cursor.fetchone():
            return jsonify({'erro': 'Email já cadastrado'}), 400

        senha_hash = generate_password_hash(data.get('senha'))

        cursor.execute('''
            INSERT INTO clientes
            (cpf, nome, ocupacao, tel, endereco, email, senha_hash, desconto_tipo, desconto_valor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cpf,
            data.get('nome'),
            data.get('ocupacao'),
            data.get('tel'),
            data.get('endereco'),
            data.get('email'),
            senha_hash,
            'fixo',  # Tipo: valor fixo em reais
            1.00     # Desconto padrão: R$ 1,00 por litro
        ))

        conn.commit()
        cliente_id = cursor.lastrowid
        conn.close()

        return jsonify({
            'mensagem': 'Cadastro realizado! Confirme seu email.',
            'cliente_id': cliente_id
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

        cursor.execute('SELECT id, nome, email, senha_hash FROM clientes WHERE email = ?', (email,))
        cliente = cursor.fetchone()
        conn.close()

        if not cliente or not check_password_hash(cliente['senha_hash'], senha):
            return jsonify({'erro': 'Email ou senha incorretos'}), 401

        return jsonify({
            'cliente_id': cliente['id'],
            'nome': cliente['nome'],
            'email': cliente['email'],
            'mensagem': 'Login realizado com sucesso'
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE PRODUTOS ====================

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
            SELECT id, nome, desconto_tipo, desconto_valor
            FROM clientes
            WHERE id = ?
        ''', (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

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

@app.route('/api/cupom/usar', methods=['POST'])
def usar_cupom():
    """Registra uso do cupom (chamado pelo caixa)"""
    try:
        data = request.get_json()
        qrcode = data.get('qrcode')
        poster_id = data.get('poster_id')  # CAJ ou SKY
        produto_id = data.get('produto_id')
        quantidade_agora = float(data.get('quantidade', 0))
        valor_sem_desconto = float(data.get('valor_sem_desconto', 0))

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
            return jsonify({'erro': 'Cupom não encontrado ou expirado'}), 404

        # Valida validade: cupom vale apenas no dia em que foi gerado
        data_geracao = str(cupom['data_geracao'])[:10]
        if data_geracao != str(datetime.now().date()):
            return jsonify({
                'erro': f'Cupom expirado (gerado em {data_geracao}). O cliente deve gerar um novo cupom hoje.'
            }), 400

        # Valida produto
        if cupom['produto_id'] != produto_id:
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
            return jsonify({'erro': 'Cupom expirado (limite atingido)'}), 400

        if quantidade_agora > litros_restantes:
            return jsonify({
                'erro': f'Quantidade excede limite',
                'limite': litros_restantes,
                'solicitado': quantidade_agora
            }), 400

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
             quantidade, valor_original, valor_desconto, valor_final)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            valor_final
        ))

        # Atualiza cupom
        nova_quantidade_utilizada = cupom['quantidade_utilizada'] + quantidade_agora
        novo_status = 'completo' if nova_quantidade_utilizada >= cupom['quantidade_permitida'] else 'parcial'

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

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': 'Cupom utilizado com sucesso!',
            'cliente': cliente['nome'],
            'produto': produto['nome'] if produto else 'N/A',
            'quantidade': quantidade_agora,
            'valor_original': round(valor_sem_desconto, 2),
            'valor_desconto': round(valor_desconto, 2),
            'valor_final': round(valor_final, 2),
            'cupom_status': novo_status,
            'quantidade_utilizada': nova_quantidade_utilizada,
            'quantidade_permitida': cupom['quantidade_permitida'],
            'quantidade_restante': litros_restantes - quantidade_agora
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
        if nivel not in ('master', 'caixa'):
            return jsonify({'erro': "Nível deve ser 'master' ou 'caixa'"}), 400

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

        rotulo = 'Master' if nivel == 'master' else 'Caixa (somente consulta)'
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
        if alvo['id'] == request.admin['id'] and (data.get('ativo') == 0 or data.get('nivel') == 'caixa'):
            conn.close()
            return jsonify({'erro': 'Você não pode remover o próprio acesso Master'}), 400

        if 'nivel' in data:
            if data['nivel'] not in ('master', 'caixa'):
                conn.close()
                return jsonify({'erro': "Nível deve ser 'master' ou 'caixa'"}), 400
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
            SELECT id, nome, tipo, preco_atual, unidade, icone, ativo,
                   desconto_valor, desconto_tipo, limite_litros, data_atualizacao
            FROM produtos
            ORDER BY tipo, id
        ''')
        produtos = cursor.fetchall()
        conn.close()

        lista = []
        for p in produtos:
            preco = p['preco_atual'] or 0
            desconto = p['desconto_valor'] or 0
            por_unidade = preco * (desconto / 100) if p['desconto_tipo'] == 'percentual' else desconto

            lista.append({
                'id': p['id'],
                'nome': p['nome'],
                'tipo': p['tipo'],
                'icone': p['icone'],
                'unidade': p['unidade'],
                'ativo': p['ativo'],
                'preco_atual': round(preco, 2),
                'desconto_valor': desconto,
                'desconto_tipo': p['desconto_tipo'] or 'fixo',
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco - por_unidade, 2),
                'limite_litros': p['limite_litros'] or 0,
                'data_atualizacao': p['data_atualizacao']
            })

        return jsonify({'produtos': lista}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/produtos/atualizar', methods=['POST'])
@exige_master
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

        for p in produtos:
            produto_id = p.get('id')
            if not produto_id:
                continue

            cursor.execute('SELECT id, nome, preco_atual, desconto_valor, desconto_tipo, limite_litros, ativo FROM produtos WHERE id = ?', (produto_id,))
            atual = cursor.fetchone()
            if not atual:
                continue

            preco = float(p.get('preco_atual', atual['preco_atual']))
            desconto = float(p.get('desconto_valor', atual['desconto_valor'] or 0))
            tipo = p.get('desconto_tipo', atual['desconto_tipo'] or 'fixo')
            limite = float(p.get('limite_litros', atual['limite_litros'] or 50))
            ativo = int(p.get('ativo', atual['ativo'] if atual['ativo'] is not None else 1))

            if preco < 0 or desconto < 0 or limite < 0:
                conn.close()
                return jsonify({'erro': f'Valores negativos não são permitidos ({atual["nome"]})'}), 400

            if tipo not in ('fixo', 'percentual'):
                conn.close()
                return jsonify({'erro': "Tipo de desconto deve ser 'fixo' ou 'percentual'"}), 400

            por_unidade = preco * (desconto / 100) if tipo == 'percentual' else desconto
            if por_unidade > preco:
                conn.close()
                return jsonify({'erro': f'O desconto de {atual["nome"]} é maior que o preço do produto'}), 400

            cursor.execute('''
                UPDATE produtos
                SET preco_atual = ?, desconto_valor = ?, desconto_tipo = ?,
                    limite_litros = ?, ativo = ?, data_atualizacao = ?
                WHERE id = ?
            ''', (preco, desconto, tipo, limite, ativo, agora, produto_id))

            atualizados.append({
                'id': produto_id,
                'nome': atual['nome'],
                'preco_atual': round(preco, 2),
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco - por_unidade, 2)
            })

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': f'{len(atualizados)} produto(s) atualizado(s)',
            'produtos': atualizados
        }), 200
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
