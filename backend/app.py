from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import uuid
import qrcode
from io import BytesIO
import base64
from werkzeug.security import generate_password_hash, check_password_hash
import re

from database import init_db, get_db

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'sua-chave-secreta-aqui-mude-em-producao'

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

def gerar_qrcode():
    """Gera QR code único"""
    qr_data = str(uuid.uuid4())
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    img_base64 = base64.b64encode(img_bytes.getvalue()).decode()

    return qr_data, img_base64

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

# ==================== ROTAS DE AUTENTICAÇÃO ====================

@app.route('/api/auth/cadastro', methods=['POST'])
def cadastro():
    """Cadastra novo cliente"""
    try:
        data = request.get_json()

        # Validações
        if not validar_cpf(data.get('cpf')):
            return jsonify({'erro': 'CPF inválido'}), 400

        if not validar_email(data.get('email')):
            return jsonify({'erro': 'Email inválido'}), 400

        if len(data.get('endereco', '')) < 10:
            return jsonify({'erro': 'Endereço incompleto'}), 400

        # Tira formatação do CPF
        cpf = re.sub(r'\D', '', data.get('cpf'))

        conn = get_db()
        cursor = conn.cursor()

        # Verifica se CPF já existe
        cursor.execute('SELECT id FROM clientes WHERE cpf = ?', (cpf,))
        if cursor.fetchone():
            return jsonify({'erro': 'CPF já cadastrado'}), 400

        # Verifica se email já existe
        cursor.execute('SELECT id FROM clientes WHERE email = ?', (data.get('email'),))
        if cursor.fetchone():
            return jsonify({'erro': 'Email já cadastrado'}), 400

        # Insere novo cliente
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
            data.get('desconto_tipo', 'percentual'),
            float(data.get('desconto_valor', 5))
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

# ==================== ROTAS DE CUPOM ====================

@app.route('/api/produtos', methods=['GET'])
def listar_produtos():
    """Lista todos os produtos com preços"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, tipo, preco_atual, unidade, icone FROM produtos WHERE ativo = 1 ORDER BY tipo, id')
        produtos = cursor.fetchall()
        conn.close()

        return jsonify({
            'produtos': [
                {
                    'id': p['id'],
                    'nome': p['nome'],
                    'tipo': p['tipo'],
                    'preco': p['preco_atual'],
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

        cursor.execute('SELECT id, nome, preco_atual, data_atualizacao FROM produtos WHERE id = ? AND ativo = 1', (produto_id,))
        produto = cursor.fetchone()
        conn.close()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        return jsonify({
            'produto_id': produto['id'],
            'nome': produto['nome'],
            'preco_atual': produto['preco_atual'],
            'data_atualizacao': produto['data_atualizacao']
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

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

        # Verifica se cliente existe
        cursor.execute('SELECT id, nome, desconto_tipo, desconto_valor FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        # Verifica se produto existe
        cursor.execute('SELECT id, nome, preco_atual, unidade FROM produtos WHERE id = ? AND ativo = 1', (produto_id,))
        produto = cursor.fetchone()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        # Verifica se já gerou cupom neste produto hoje
        hoje = datetime.now().date()
        cursor.execute('''
            SELECT id FROM cupons
            WHERE cliente_id = ? AND produto_id = ? AND data_geracao = ?
        ''', (cliente_id, produto_id, hoje))

        if cursor.fetchone():
            return jsonify({'erro': f'Você já gerou um cupom para {produto["nome"]} hoje!'}), 400

        # Calcula desconto
        if cliente['desconto_tipo'] == 'percentual':
            desconto_aplicado = produto['preco_atual'] * (cliente['desconto_valor'] / 100)
        else:
            desconto_aplicado = cliente['desconto_valor']

        preco_final = produto['preco_atual'] - desconto_aplicado

        # Gera novo cupom
        qr_data, qr_image = gerar_qrcode()

        cursor.execute('''
            INSERT INTO cupons (cliente_id, produto_id, qrcode, data_geracao, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (cliente_id, produto_id, qr_data, hoje, 'pendente'))

        conn.commit()
        cupom_id = cursor.lastrowid
        conn.close()

        return jsonify({
            'cupom_id': cupom_id,
            'qrcode_data': qr_data,
            'qrcode_image': f'data:image/png;base64,{qr_image}',
            'cliente_nome': cliente['nome'],
            'produto_nome': produto['nome'],
            'preco_produto': round(produto['preco_atual'], 2),
            'desconto_tipo': cliente['desconto_tipo'],
            'desconto_valor': cliente['desconto_valor'],
            'desconto_aplicado': round(desconto_aplicado, 2),
            'preco_final': round(preco_final, 2),
            'mensagem': 'QR code gerado com sucesso!'
        }), 200

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
        quantidade = float(data.get('quantidade', 0))  # litros ou unidades
        valor_original = float(data.get('valor_original', 0))

        conn = get_db()
        cursor = conn.cursor()

        # Busca cupom
        cursor.execute('SELECT * FROM cupons WHERE qrcode = ? AND status = "pendente"', (qrcode,))
        cupom = cursor.fetchone()

        if not cupom:
            return jsonify({'erro': 'Cupom não encontrado ou já utilizado'}), 404

        # Verifica se produto do cupom bate com produto sendo abastecido
        if cupom['produto_id'] and cupom['produto_id'] != produto_id:
            return jsonify({'erro': 'Produto do cupom não coincide com abastecimento'}), 400

        # Busca cliente para desconto
        cursor.execute('SELECT nome, desconto_tipo, desconto_valor FROM clientes WHERE id = ?', (cupom['cliente_id'],))
        cliente = cursor.fetchone()

        # Busca produto para nome
        cursor.execute('SELECT nome FROM produtos WHERE id = ?', (produto_id,))
        produto = cursor.fetchone()

        # Calcula desconto
        if cliente['desconto_tipo'] == 'percentual':
            valor_desconto = valor_original * (cliente['desconto_valor'] / 100)
        else:
            valor_desconto = cliente['desconto_valor'] * quantidade

        valor_final = valor_original - valor_desconto
        turno = obter_turno()

        # Registra abastecimento
        agora = datetime.now()
        cursor.execute('''
            INSERT INTO abastecimentos
            (cupom_id, cliente_id, produto_id, poster_id, data, hora, turno, quantidade, valor_original, valor_desconto, valor_final)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cupom['id'],
            cupom['cliente_id'],
            produto_id,
            poster_id,
            agora.date(),
            agora.time(),
            turno,
            quantidade,
            valor_original,
            valor_desconto,
            valor_final
        ))

        # Marca cupom como usado
        cursor.execute('UPDATE cupons SET status = ?, data_uso = ?, timestamp_uso = ?, turno = ?, poster_id = ? WHERE id = ?',
                      ('usado', agora.date(), agora, turno, poster_id, cupom['id']))

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': 'Cupom utilizado com sucesso!',
            'cliente': cliente['nome'],
            'produto': produto['nome'] if produto else 'N/A',
            'quantidade': quantidade,
            'valor_original': round(valor_original, 2),
            'valor_desconto': round(valor_desconto, 2),
            'valor_final': round(valor_final, 2)
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/relatorio', methods=['GET'])
def relatorio_admin():
    """Retorna relatórios para admin"""
    try:
        # Parâmetros de filtro
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        turno = request.args.get('turno')
        poster_id = request.args.get('poster_id')

        conn = get_db()
        cursor = conn.cursor()

        # Query base
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

        cursor.execute(query, params)
        abastecimentos = cursor.fetchall()

        # Agrupa dados
        total_litros = sum([row['litros'] for row in abastecimentos])
        total_original = sum([row['valor_original'] for row in abastecimentos])
        total_desconto = sum([row['valor_desconto'] for row in abastecimentos])
        total_final = sum([row['valor_final'] for row in abastecimentos])
        total_cupons = len(abastecimentos)

        conn.close()

        return jsonify({
            'total_abastecimentos': total_cupons,
            'total_litros': round(total_litros, 2),
            'total_valor_original': round(total_original, 2),
            'total_valor_desconto': round(total_desconto, 2),
            'total_valor_final': round(total_final, 2),
            'abastecimentos': [dict(row) for row in abastecimentos]
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({'status': 'OK', 'timestamp': datetime.now().isoformat()}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)
