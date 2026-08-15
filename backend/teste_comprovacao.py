"""Teste da comprovação de categoria: placa, comprovante e padrões suspeitos.

Roda contra um SQLite descartável — não encosta no banco de produção.
Uso:  python teste_comprovacao.py
"""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

os.environ.pop('DATABASE_URL', None)
os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')

spec = importlib.util.spec_from_file_location('appv2', 'app-v2.py')
appv2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appv2)

app = appv2.app
get_db = appv2.get_db

falhas = []

# Imagem mínima válida, só para passar pelas checagens de formato e tamanho
FOTO_OK = 'data:image/jpeg;base64,' + ('QUJD' * 800)


def checar(descricao, condicao, extra=''):
    print(f'  [{"OK  " if condicao else "FALHOU"}] {descricao}' +
          (f'  -> {extra}' if extra and not condicao else ''))
    if not condicao:
        falhas.append(descricao)


def corpo(r):
    try:
        return json.loads(r.data)
    except Exception:
        return {}


def base_cadastro(**extra):
    marca = datetime.now().timestamp()
    dados = {
        'cpf': extra.pop('cpf', '111.444.777-35'),
        'nome': 'Motorista Teste',
        'ocupacao': 'Táxi',
        'tel': '11999998888',
        'endereco': 'Rua Estados Unidos, 1930, Jardins, São Paulo',
        'email': f'teste_{marca}_{extra.pop("sufixo", "a")}@teste.com',
        'senha': 'senha12345',
        'aceita_promocoes': True,
        'placa': 'ABC1D23',
        'foto_comprovante': FOTO_OK,
    }
    dados.update(extra)
    return dados


with app.test_client() as c:
    print('\n=== Limpando ===')
    conn = get_db(); cur = conn.cursor()
    for t in ('abastecimentos', 'cupons', 'auditoria'):
        cur.execute(f'DELETE FROM {t}')
    cur.execute("DELETE FROM clientes")
    cur.execute("DELETE FROM admin WHERE usuario LIKE 'frt_%'")
    cur.execute("UPDATE produtos SET preco_atual = 6.09, preco_custo = 5.00, "
                "desconto_valor = 0.37, desconto_tipo = 'fixo', limite_litros = 40 WHERE id = 1")
    conn.commit(); conn.close()

    print('\n=== 1. Placa: formatos aceitos e recusados ===')
    for placa, deveria_passar, rotulo in [
        ('ABC1D23', True,  'Mercosul'),
        ('abc1d23', True,  'Mercosul em minúsculas'),
        ('ABC-1D23', True, 'Mercosul com hífen'),
        ('ABC1234', True,  'formato antigo'),
        ('ABC12',   False, 'curta demais'),
        ('1BC1D23', False, 'começa com número'),
        ('ABCD123', False, 'letra onde vai número'),
        ('',        False, 'vazia'),
    ]:
        r = c.post('/api/auth/cadastro', json=base_cadastro(placa=placa, sufixo=rotulo[:4]))
        passou = r.status_code == 201
        checar(f'placa {rotulo} ({placa or "vazio"}) -> {"aceita" if deveria_passar else "recusada"}',
               passou == deveria_passar, corpo(r).get('erro'))
        if passou:
            conn = get_db(); cur = conn.cursor()
            cur.execute("DELETE FROM clientes"); conn.commit(); conn.close()

    print('\n=== 2. Placa é normalizada antes de gravar ===')
    r = c.post('/api/auth/cadastro', json=base_cadastro(placa='abc-1d23', sufixo='norm'))
    checar('placa gravada em maiúsculas e sem hífen', corpo(r).get('placa') == 'ABC1D23',
           corpo(r).get('placa'))
    conn = get_db(); cur = conn.cursor(); cur.execute("DELETE FROM clientes")
    conn.commit(); conn.close()

    print('\n=== 3. Comprovante é obrigatório ===')
    r = c.post('/api/auth/cadastro', json=base_cadastro(foto_comprovante=None, sufixo='sf'))
    checar('sem comprovante o cadastro é recusado', r.status_code == 400, r.status_code)
    checar('erro cita a licença para taxista',
           'licença' in (corpo(r).get('erro') or '').lower(), corpo(r).get('erro'))

    r = c.post('/api/auth/cadastro',
               json=base_cadastro(ocupacao='Uber', foto_comprovante=None, sufixo='sf2'))
    checar('erro cita o print do app para motorista de aplicativo',
           'print' in (corpo(r).get('erro') or '').lower(), corpo(r).get('erro'))

    r = c.post('/api/auth/cadastro',
               json=base_cadastro(foto_comprovante='nao-e-imagem', sufixo='sf3'))
    checar('arquivo que não é imagem é recusado', r.status_code == 400, corpo(r).get('erro'))

    r = c.post('/api/auth/cadastro',
               json=base_cadastro(foto_comprovante='data:image/jpeg;base64,' + 'A' * 2_000_000,
                                  sufixo='sf4'))
    checar('imagem grande demais é recusada', r.status_code == 400, corpo(r).get('erro'))

    print('\n=== 4. Convênio exige o nome da empresa ===')
    r = c.post('/api/auth/cadastro', json=base_cadastro(ocupacao='Outro', sufixo='cv1'))
    checar('sem empresa o convênio é recusado', r.status_code == 400, corpo(r).get('erro'))

    r = c.post('/api/auth/cadastro',
               json=base_cadastro(ocupacao='Outro', empresa_convenio='Padaria do Zé', sufixo='cv2'))
    checar('com empresa o convênio é aceito', r.status_code == 201, corpo(r).get('erro'))
    conn = get_db(); cur = conn.cursor(); cur.execute("DELETE FROM clientes")
    conn.commit(); conn.close()

    print('\n=== 5. Cada ocupação grava o registro e o comprovante certos ===')
    ids = {}
    # O sufixo vai dentro do e-mail, então precisa ser ASCII — "Táxi" com
    # acento seria recusado pelo próprio validador de e-mail.
    for ocupacao, reg, comp, cpf, sufixo in [
        ('Táxi', 'condutax', 'licenca_taxi', '111.444.777-35', 'taxi'),
        ('Uber', 'conduapp', 'perfil_app', '529.982.247-25', 'uber'),
    ]:
        r = c.post('/api/auth/cadastro',
                   json=base_cadastro(ocupacao=ocupacao, cpf=cpf, sufixo=sufixo))
        ids[ocupacao] = corpo(r).get('cliente_id')
        checar(f'{ocupacao}: cadastro criado', bool(ids[ocupacao]), corpo(r).get('erro'))
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT registro_tipo, foto_comprovante_tipo FROM clientes WHERE id = ?',
                    (ids[ocupacao],))
        linha = cur.fetchone(); conn.close()
        checar(f'{ocupacao}: registro_tipo = {reg}', linha and linha['registro_tipo'] == reg,
               linha and linha['registro_tipo'])
        checar(f'{ocupacao}: comprovante = {comp}',
               linha and linha['foto_comprovante_tipo'] == comp,
               linha and linha['foto_comprovante_tipo'])

    print('\n=== 6. Placa repetida não bloqueia, mas é sinalizada ===')
    r = c.post('/api/auth/cadastro',
               json=base_cadastro(cpf='390.533.447-05', placa='ABC1D23', sufixo='rep'))
    checar('segundo cadastro com a mesma placa é aceito', r.status_code == 201, corpo(r).get('erro'))
    checar('resposta avisa que a placa já existia', corpo(r).get('placa_ja_cadastrada') is True,
           corpo(r))

    print('\n=== 7. Troca de placa pelo motorista ===')
    taxi_id = ids['Táxi']
    r = c.post('/api/cliente/placa', json={'cliente_id': taxi_id, 'placa': 'XYZ9K88'})
    checar('placa atualizada', r.status_code == 200 and corpo(r).get('placa') == 'XYZ9K88', corpo(r))

    r = c.post('/api/cliente/placa', json={'cliente_id': taxi_id, 'placa': 'invalida'})
    checar('placa inválida é recusada na troca', r.status_code == 400, corpo(r).get('erro'))

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT valor_anterior, valor_novo FROM auditoria WHERE acao = 'troca_placa'")
    trocas = cur.fetchall(); conn.close()
    checar('troca fica registrada na auditoria', len(trocas) == 1, len(trocas))
    if trocas:
        checar('auditoria guarda de qual placa para qual',
               trocas[0]['valor_anterior'] == 'ABC1D23' and trocas[0]['valor_novo'] == 'XYZ9K88',
               dict(trocas[0]) if trocas else None)

    print('\n=== 8. Placa aparece para o frentista ===')
    from werkzeug.security import generate_password_hash
    conn = get_db(); cur = conn.cursor()
    for usuario, posto in [('frt_a', 'CAJ'), ('frt_b', 'SKY')]:
        cur.execute("INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, ativo) "
                    "VALUES (?, ?, ?, 'caixa', ?, 1)",
                    (usuario, generate_password_hash('senha12345'), posto, usuario))
    conn.commit(); conn.close()

    r = c.post('/api/admin/login', json={'usuario': 'frt_a', 'senha': 'senha12345'})
    cab_a = {'X-Admin-Token': corpo(r)['token']}
    r = c.post('/api/admin/login', json={'usuario': 'frt_b', 'senha': 'senha12345'})
    cab_b = {'X-Admin-Token': corpo(r)['token']}

    r = c.post('/api/cupom/gerar', json={'cliente_id': taxi_id, 'produto_id': 1})
    qr = corpo(r).get('qrcode_data') or corpo(r).get('qrcode')

    r = c.get(f'/api/cupom/consultar?qrcode={qr}', headers=cab_a)
    d = corpo(r)
    checar('consulta traz a placa atual do motorista', d.get('placa') == 'XYZ9K88', d.get('placa'))
    checar('consulta traz a ocupação', d.get('ocupacao') == 'Táxi', d.get('ocupacao'))
    checar('placa única não dispara alerta', d.get('placa_em_varios_cadastros') is False,
           d.get('placa_qtd_cadastros'))

    print('\n=== 9. Alerta de placa em vários cadastros ===')
    uber_id = ids['Uber']
    r = c.post('/api/cupom/gerar', json={'cliente_id': uber_id, 'produto_id': 1})
    qr_uber = corpo(r).get('qrcode_data') or corpo(r).get('qrcode')
    r = c.get(f'/api/cupom/consultar?qrcode={qr_uber}', headers=cab_a)
    d = corpo(r)
    checar('placa repetida dispara alerta', d.get('placa_em_varios_cadastros') is True, d)
    checar('alerta informa quantos cadastros', d.get('placa_qtd_cadastros') == 2,
           d.get('placa_qtd_cadastros'))

    print('\n=== 10. Abastecimento grava quem registrou ===')
    r = c.post('/api/cupom/usar', headers=cab_a,
               json={'qrcode': qr, 'produto_id': 1, 'quantidade': 10})
    checar('abastecimento aceito', r.status_code == 200, corpo(r).get('erro'))

    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT registrado_por FROM abastecimentos ORDER BY id DESC')
    linha = cur.fetchone(); conn.close()
    checar('coluna registrado_por preenchida', linha and linha['registrado_por'] == 'frt_a',
           linha and linha['registrado_por'])

    print('\n=== 11. Padrão: sempre o mesmo frentista ===')
    hoje = datetime.now().strftime('%Y-%m-%d')
    conn = get_db(); cur = conn.cursor()
    # 6 abastecimentos do taxista, todos com frt_a
    for i in range(6):
        cur.execute('''INSERT INTO abastecimentos
            (cupom_id, cliente_id, produto_id, poster_id, data, hora, turno,
             quantidade, valor_original, valor_desconto, valor_final, registrado_por)
            VALUES (NULL, ?, 1, 'CAJ', ?, '10:00:00', 'Turno 1 (6h-14h)', 10, 60.9, 3.7, 57.2, 'frt_a')''',
            (taxi_id, hoje))
    # 6 do outro, espalhados entre dois frentistas
    for i in range(6):
        cur.execute('''INSERT INTO abastecimentos
            (cupom_id, cliente_id, produto_id, poster_id, data, hora, turno,
             quantidade, valor_original, valor_desconto, valor_final, registrado_por)
            VALUES (NULL, ?, 1, 'CAJ', ?, '10:00:00', 'Turno 1 (6h-14h)', 10, 60.9, 3.7, 57.2, ?)''',
            (uber_id, hoje, 'frt_a' if i % 2 else 'frt_b'))
    conn.commit(); conn.close()

    r = c.get('/api/admin/suspeitas?dias=30', headers=cab_a)
    checar('nível Caixa não vê o relatório', r.status_code == 403, r.status_code)

    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE admin SET nivel = 'gerencia' WHERE usuario = 'frt_a'")
    conn.commit(); conn.close()

    r = c.get('/api/admin/suspeitas?dias=30', headers=cab_a)
    d = corpo(r)
    checar('gerência vê o relatório', r.status_code == 200, d.get('erro'))

    sempre = {x['cliente_id'] for x in d.get('sempre_mesmo_frentista', [])}
    checar('taxista (só frt_a) é sinalizado', taxi_id in sempre, sempre)
    checar('quem alterna frentista não é sinalizado', uber_id not in sempre, sempre)

    print('\n=== 12. Padrão: placas repetidas ===')
    placas = d.get('placas_repetidas', [])
    checar('placa repetida aparece no relatório', len(placas) == 1, len(placas))
    if placas:
        checar('mostra os 2 cadastros da placa', placas[0]['quantidade'] == 2, placas[0])

    print('\n=== 13. Padrão: trocas de placa ===')
    checar('troca de placa aparece no relatório', len(d.get('trocas_de_placa', [])) == 1,
           d.get('trocas_de_placa'))

    print('\n=== 14. Comprovante visível para a gerência ===')
    r = c.get(f'/api/admin/cliente/{taxi_id}/comprovante', headers=cab_a)
    dc = corpo(r)
    checar('comprovante devolvido', r.status_code == 200, dc.get('erro'))
    checar('traz a imagem', (dc.get('imagem') or '').startswith('data:image/'), 'sem imagem')
    checar('traz o tipo do comprovante', dc.get('tipo_comprovante') == 'licenca_taxi',
           dc.get('tipo_comprovante'))

    r = c.get(f'/api/admin/cliente/{taxi_id}/comprovante', headers=cab_b)
    checar('nível Caixa não vê comprovante', r.status_code == 403, r.status_code)

print('\n' + '=' * 55)
if falhas:
    print(f'{len(falhas)} verificação(ões) falharam:')
    for f in falhas:
        print('  - ' + f)
    sys.exit(1)
print('Todas as verificações passaram.')
