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
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})
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
    qr_data = str(uuid.uuid4())[:12]
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
        quantidade_permitida = data.get('quantidade_permitida', 50)  # default 50L

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

        # Verifica produto
        cursor.execute('''
            SELECT id, nome, preco_atual, unidade
            FROM produtos
            WHERE id = ? AND ativo = 1
        ''', (produto_id,))
        produto = cursor.fetchone()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        # Verifica cupom ativo
        hoje = datetime.now().date()
        cursor.execute('''
            SELECT id FROM cupons
            WHERE cliente_id = ? AND produto_id = ? AND data_geracao = ?
        ''', (cliente_id, produto_id, hoje))

        if cursor.fetchone():
            return jsonify({'erro': f'Você já gerou um cupom para {produto["nome"]} hoje!'}), 400

        # Calcula desconto
        if cliente['desconto_tipo'] == 'percentual':
            desconto_por_unidade = produto['preco_atual'] * (cliente['desconto_valor'] / 100)
            economia_total = desconto_por_unidade * quantidade_permitida
        else:
            desconto_por_unidade = cliente['desconto_valor']
            economia_total = cliente['desconto_valor'] * quantidade_permitida

        preco_final_unitario = produto['preco_atual'] - desconto_por_unidade

        # Gera cupom
        qr_data, qr_image = gerar_qrcode()

        cursor.execute('''
            INSERT INTO cupons
            (cliente_id, produto_id, qrcode, data_geracao, quantidade_permitida,
             quantidade_utilizada, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            cliente_id,
            produto_id,
            qr_data,
            hoje,
            quantidade_permitida,
            0,
            'pendente'
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
            'preco_produto': round(produto['preco_atual'], 2),
            'preco_unitario_com_desconto': round(preco_final_unitario, 2),
            'desconto_tipo': cliente['desconto_tipo'],
            'desconto_valor': cliente['desconto_valor'],
            'desconto_por_unidade': round(desconto_por_unidade, 2),
            'quantidade_permitida': quantidade_permitida,
            'economia_total': round(economia_total, 2),
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

        # Calcula desconto desta compra
        if cliente['desconto_tipo'] == 'percentual':
            valor_desconto = valor_sem_desconto * (cliente['desconto_valor'] / 100)
        else:
            valor_desconto = cliente['desconto_valor'] * quantidade_agora

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
            agora.date(),
            agora.time(),
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
            agora.date(),
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

# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/descontos', methods=['GET'])
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
    app.run(debug=True, host='0.0.0.0', port=5000)
