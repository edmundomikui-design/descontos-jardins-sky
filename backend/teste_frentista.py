"""Teste do fluxo da pista: consultar o cupom e registrar o abastecimento.

Roda contra um SQLite descartável — não encosta no banco de produção.
Uso:  python teste_frentista.py
"""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

os.environ.pop('DATABASE_URL', None)
os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')

# O arquivo tem hífen no nome, então precisa ser carregado na mão
spec = importlib.util.spec_from_file_location('appv2', 'app-v2.py')
appv2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appv2)

app = appv2.app
get_db = appv2.get_db

falhas = []


def checar(descricao, condicao, extra=''):
    marca = 'OK  ' if condicao else 'FALHOU'
    print(f'  [{marca}] {descricao}' + (f'  -> {extra}' if extra and not condicao else ''))
    if not condicao:
        falhas.append(descricao)


def json_de(resposta):
    try:
        return json.loads(resposta.data)
    except Exception:
        return {}


with app.test_client() as c:
    print('\n=== Preparando dados ===')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM abastecimentos")
    cur.execute("DELETE FROM cupons")
    cur.execute("DELETE FROM clientes WHERE cpf = '11144477735'")
    cur.execute("DELETE FROM admin WHERE usuario IN ('frentista_teste', 'master_teste')")
    cur.execute("UPDATE produtos SET preco_atual = 6.09, preco_custo = 5.20, "
                "margem_minima = 5, desconto_valor = 0.37, desconto_tipo = 'fixo', "
                "limite_litros = 40 WHERE id = 1")
    conn.commit()

    from werkzeug.security import generate_password_hash
    cur.execute("INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, ativo) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                ('frentista_teste', generate_password_hash('senha12345'), 'CAJ', 'caixa', 'Frentista Teste'))
    conn.commit()
    conn.close()

    # cliente
    r = c.post('/api/auth/cadastro', json={
        'cpf': '111.444.777-35', 'nome': 'Motorista Teste', 'ocupacao': 'motorista_app',
        'tel': '11999998888', 'endereco': 'Rua Estados Unidos, 1930',
        'email': f'teste_pista_{datetime.now().timestamp()}@teste.com',
        'senha': 'senha12345', 'aceita_promocoes': True
    })
    cliente_id = json_de(r).get('cliente_id') or json_de(r).get('id')
    checar('cliente de teste cadastrado', r.status_code in (200, 201), json_de(r))

    if not cliente_id:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM clientes WHERE cpf = '11144477735'")
        linha = cur.fetchone(); conn.close()
        cliente_id = linha['id'] if linha else None

    # login do frentista
    r = c.post('/api/admin/login', json={'usuario': 'frentista_teste', 'senha': 'senha12345'})
    token = json_de(r).get('token')
    checar('login do frentista devolve token', bool(token), json_de(r))
    cab = {'X-Admin-Token': token}

    # cupom
    r = c.post('/api/cupom/gerar', json={'cliente_id': cliente_id, 'produto_id': 1})
    cupom = json_de(r)
    qr = cupom.get('qrcode_data') or cupom.get('qrcode')
    checar('cupom gerado', bool(qr), cupom)

    print('\n=== 1. Sem login não passa ===')
    r = c.get(f'/api/cupom/consultar?qrcode={qr}')
    checar('consulta sem token é recusada (401)', r.status_code == 401, r.status_code)
    r = c.post('/api/cupom/usar', json={'qrcode': qr, 'produto_id': 1, 'quantidade': 10})
    checar('registro sem token é recusado (401)', r.status_code == 401, r.status_code)

    print('\n=== 2. Consulta do cupom válido ===')
    r = c.get(f'/api/cupom/consultar?qrcode={qr}', headers=cab)
    d = json_de(r)
    checar('consulta responde 200', r.status_code == 200, d)
    checar('cupom marcado como válido', d.get('valido') is True, d.get('motivo'))
    checar('traz o nome do motorista', d.get('cliente_nome') == 'Motorista Teste', d.get('cliente_nome'))
    checar('CPF vem mascarado', d.get('cliente_cpf', '').startswith('***.'), d.get('cliente_cpf'))
    checar('preço de bomba R$ 6,09', d.get('preco_bomba') == 6.09, d.get('preco_bomba'))
    checar('desconto R$ 0,37/L', d.get('desconto_por_unidade') == 0.37, d.get('desconto_por_unidade'))
    checar('preço com desconto R$ 5,72', d.get('preco_com_desconto') == 5.72, d.get('preco_com_desconto'))
    checar('saldo inicial 40 L', d.get('quantidade_restante') == 40.0, d.get('quantidade_restante'))

    print('\n=== 3. Código inexistente ===')
    r = c.get('/api/cupom/consultar?qrcode=nao-existe-99', headers=cab)
    checar('código desconhecido devolve 404', r.status_code == 404, r.status_code)

    print('\n=== 4. Abastecimento parcial (20 L) ===')
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': qr, 'produto_id': 1, 'quantidade': 20,
        'valor_sem_desconto': round(20 * 6.09, 2)
    })
    d = json_de(r)
    checar('registro responde 200', r.status_code == 200, d)
    checar('valor bruto R$ 121,80', d.get('valor_original') == 121.80, d.get('valor_original'))
    checar('desconto R$ 7,40', d.get('valor_desconto') == 7.40, d.get('valor_desconto'))
    checar('cobrar R$ 114,40', d.get('valor_final') == 114.40, d.get('valor_final'))
    checar("status vira 'parcial'", d.get('cupom_status') == 'parcial', d.get('cupom_status'))
    checar('restam 20 L', d.get('quantidade_restante') == 20.0, d.get('quantidade_restante'))
    checar('posto vem do usuário logado (CAJ)', d.get('posto') == 'CAJ', d.get('posto'))
    checar('grava quem registrou', d.get('registrado_por') == 'Frentista Teste', d.get('registrado_por'))

    print('\n=== 5. Saldo atualizado na consulta ===')
    r = c.get(f'/api/cupom/consultar?qrcode={qr}', headers=cab)
    d = json_de(r)
    checar('saldo caiu para 20 L', d.get('quantidade_restante') == 20.0, d.get('quantidade_restante'))
    checar('continua válido', d.get('valido') is True, d.get('motivo'))

    print('\n=== 6. Litros acima do saldo ===')
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': qr, 'produto_id': 1, 'quantidade': 25, 'valor_sem_desconto': 152.25
    })
    d = json_de(r)
    checar('recusa quantidade acima do saldo (400)', r.status_code == 400, r.status_code)
    checar('erro explica o saldo restante', 'restam' in (d.get('erro') or ''), d.get('erro'))

    print('\n=== 7. Produto diferente do cupom ===')
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': qr, 'produto_id': 3, 'quantidade': 5, 'valor_sem_desconto': 19.45
    })
    checar('recusa produto trocado (400)', r.status_code == 400, r.status_code)

    print('\n=== 8. Valor calculado pelo servidor quando a tela não manda ===')
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': qr, 'produto_id': 1, 'quantidade': 10
    })
    d = json_de(r)
    checar('servidor calcula R$ 60,90 de bruto', d.get('valor_original') == 60.90, d.get('valor_original'))
    checar('cobra R$ 57,20', d.get('valor_final') == 57.20, d.get('valor_final'))

    print('\n=== 9. Esgotar o cupom (10 L restantes) ===')
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': qr, 'produto_id': 1, 'quantidade': 10
    })
    d = json_de(r)
    checar("status vira 'completo'", d.get('cupom_status') == 'completo', d.get('cupom_status'))
    r = c.get(f'/api/cupom/consultar?qrcode={qr}', headers=cab)
    d = json_de(r)
    checar('consulta bloqueia cupom esgotado', d.get('valido') is False, d.get('motivo'))
    checar('motivo fala em uso completo', 'completo' in (d.get('motivo') or '').lower(), d.get('motivo'))

    print('\n=== 10. Cupom de ontem ===')
    conn = get_db(); cur = conn.cursor()
    ontem = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    cur.execute("INSERT INTO cupons (cliente_id, produto_id, qrcode, data_geracao, status, "
                "quantidade_permitida, quantidade_utilizada, preco_unitario, desconto_unitario) "
                "VALUES (?, 1, 'cupom-de-ontem', ?, 'pendente', 40, 0, 6.09, 0.37)",
                (cliente_id, ontem))
    conn.commit(); conn.close()

    r = c.get('/api/cupom/consultar?qrcode=cupom-de-ontem', headers=cab)
    d = json_de(r)
    checar('consulta marca cupom de ontem como inválido', d.get('valido') is False, d.get('motivo'))
    checar('motivo cita o dia da geração', 'dia' in (d.get('motivo') or '').lower(), d.get('motivo'))
    r = c.post('/api/cupom/usar', headers=cab, json={
        'qrcode': 'cupom-de-ontem', 'produto_id': 1, 'quantidade': 5
    })
    checar('registro de cupom de ontem é recusado', r.status_code == 400, r.status_code)

    print('\n=== 11. Rastro na auditoria ===')
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT admin_usuario, detalhe FROM auditoria WHERE acao = 'abastecimento' "
                "ORDER BY id DESC")
    linhas = cur.fetchall(); conn.close()
    checar('3 abastecimentos registrados na auditoria', len(linhas) == 3, len(linhas))
    if linhas:
        checar('auditoria guarda o usuário', linhas[0]['admin_usuario'] == 'frentista_teste',
               linhas[0]['admin_usuario'])
        checar('auditoria descreve o abastecimento', 'CAJ' in (linhas[0]['detalhe'] or ''),
               linhas[0]['detalhe'])

print('\n' + '=' * 55)
if falhas:
    print(f'{len(falhas)} verificação(ões) falharam:')
    for f in falhas:
        print('  - ' + f)
    sys.exit(1)
print('Todas as verificações passaram.')
