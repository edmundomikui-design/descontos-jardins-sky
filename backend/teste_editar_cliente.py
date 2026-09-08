"""
Teste de ponta a ponta da correção de cadastro pelo Master, rodando contra
um SQLite temporário — sem encostar no banco de produção.

Motivação: o cliente se cadastra sozinho, sem ninguém conferindo em tempo
real. Se ele errar o e-mail, o CPF ou o telefone na hora, não havia como
corrigir — o cadastro ficava errado para sempre. Agora só o Master pode
abrir um cadastro existente e corrigir nome, CPF, e-mail, telefone,
endereço ou placa.
"""
import os
import sys
import tempfile
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))
os.environ['DATABASE_PATH'] = tempfile.mktemp(suffix='.db')
os.environ.pop('DATABASE_URL', None)
sys.path.insert(0, BASE)

spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appv2)

app = appv2.app
app.config['TESTING'] = True
cli = app.test_client()

from database import get_db  # noqa: E402

falhas = []


def checar(condicao, descricao, extra=''):
    marca = 'OK  ' if condicao else 'FALHA'
    print(f'  [{marca}] {descricao}{(" — " + str(extra)) if extra and not condicao else ""}')
    if not condicao:
        falhas.append(descricao)


# CPFs válidos (dígitos verificadores corretos) para o teste
CPFS = ['11144477735', '52998224725', '87748248800', '15350946056',
        '19100000000', '76887453043']
FOTO = 'data:image/jpeg;base64,' + ('A' * 3000)


def criar_admin_e_token(usuario='master', senha='senha123'):
    if usuario == 'master':
        cli.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senha123', 'poster_id': 'CAJ',
            'nome': 'Master', 'email': 'master@teste.com'
        })
    r = cli.post('/api/admin/login', json={'usuario': usuario, 'senha': senha})
    return r.get_json().get('token')


def cadastrar_comum(nome, i, email=None, tel='11955550000'):
    r = cli.post('/api/auth/cadastro', json={
        'nome': nome, 'cpf': CPFS[i], 'ocupacao': 'Táxi',
        'tel': tel, 'endereco': 'Rua Estados Unidos, 1930, Jardins, Sao Paulo',
        'email': email or f'{nome.lower()}@teste.com', 'senha': 'senha123',
        'aceita_promocoes': True, 'placa': f'ABC{1000 + i}',
        'foto_comprovante': FOTO
    })
    return r.get_json(), r.status_code


print('\n=== 1. Preparação ===')
token = criar_admin_e_token()
checar(bool(token), 'login do Master no painel')

r_gerencia = cli.post('/api/admin/usuarios',
                      json={'usuario': 'gerente1', 'senha': 'senha12345',
                            'nivel': 'gerencia', 'email': 'gerente1@teste.com'},
                      headers={'X-Admin-Token': token})
checar(r_gerencia.status_code == 201, 'Master cria usuário de Gerência', r_gerencia.get_json())
token_gerencia_r = cli.post('/api/admin/login', json={'usuario': 'gerente1', 'senha': 'senha12345'})
token_gerencia = token_gerencia_r.get_json().get('token')

comum, status = cadastrar_comum('ClienteComErro', 0, email='errado@teste.com')
checar(status == 201, 'cliente se cadastra sozinho (com um e-mail que vai precisar corrigir)', comum)
cliente_id = comum.get('cliente_id')

outro, status2 = cadastrar_comum('OutroCliente', 1, email='outro@teste.com')
checar(status2 == 201, 'segundo cliente cadastrado (para testar colisão)', outro)
outro_id = outro.get('cliente_id')


print('\n=== 2. Só o Master edita cadastro (Gerência não pode) ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}',
            json={'email': 'certo@teste.com'},
            headers={'X-Admin-Token': token_gerencia})
checar(r.status_code == 403, 'Gerência NÃO consegue editar cadastro de cliente', r.get_json())

r = cli.get(f'/api/admin/clientes/{cliente_id}', headers={'X-Admin-Token': token_gerencia})
checar(r.status_code == 403, 'Gerência também não consegue ver o detalhe completo', r.get_json())


print('\n=== 3. Master vê o cadastro completo, sem máscara no CPF ===')
r = cli.get(f'/api/admin/clientes/{cliente_id}', headers={'X-Admin-Token': token})
d = r.get_json()
checar(r.status_code == 200, 'Master consegue ver o detalhe do cliente', d)
checar(d.get('cliente', {}).get('cpf') == CPFS[0], 'CPF vem completo, sem máscara, para o Master editar', d)
checar(d.get('cliente', {}).get('email') == 'errado@teste.com', 'e-mail atual confere', d)

r404 = cli.get('/api/admin/clientes/999999', headers={'X-Admin-Token': token})
checar(r404.status_code == 404, 'cliente inexistente dá 404', r404.get_json())


print('\n=== 4. Master corrige o e-mail errado ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}',
            json={'email': 'certo@teste.com'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'correção de e-mail aceita', r.get_json())

_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT email FROM clientes WHERE id = ?', (cliente_id,))
_email = dict(_cur.fetchone())['email']
_c.close()
checar(_email == 'certo@teste.com', 'e-mail realmente mudou no banco', _email)


print('\n=== 5. Validações recusam dado inválido, sem mudar nada ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'email': 'nao-e-email'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'e-mail inválido é recusado', r.get_json())

r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'cpf': '11111111111'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'CPF inválido é recusado', r.get_json())

r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'tel': '  '},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'telefone vazio é recusado (campo obrigatório)', r.get_json())

r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'placa': 'XX'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'placa em formato inválido é recusada', r.get_json())


print('\n=== 6. Unicidade: não deixa colidir com outro cliente já existente ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'email': 'outro@teste.com'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'e-mail de OUTRO cliente é recusado', r.get_json())

r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'cpf': CPFS[1]},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'CPF de OUTRO cliente é recusado', r.get_json())

# Mas reenviar o PRÓPRIO valor atual (sem mudar nada) não deve disparar a
# trava de unicidade contra si mesmo.
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'email': 'certo@teste.com'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'reenviar o PRÓPRIO e-mail atual não é bloqueado como duplicado', r.get_json())

r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'cpf': CPFS[0]},
            headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'reenviar o PRÓPRIO CPF atual não é bloqueado como duplicado', r.get_json())


print('\n=== 7. Corrige vários campos de uma vez (CPF, telefone, placa, nome, endereço) ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={
    'nome': 'Cliente Corrigido',
    'cpf': CPFS[2],
    'tel': '11988887777',
    'endereco': 'Rua Nova, 100, Curitiba',
    'placa': 'xyz9z99',  # minúsculo, sem hífen — normalizar_placa deve tratar
}, headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'edição em lote de vários campos aceita', r.get_json())

_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT nome, cpf, tel, endereco, placa FROM clientes WHERE id = ?', (cliente_id,))
_linha = dict(_cur.fetchone())
_c.close()
checar(_linha['nome'] == 'Cliente Corrigido', 'nome atualizado', _linha)
checar(_linha['cpf'] == CPFS[2], 'CPF atualizado', _linha)
checar(_linha['tel'] == '11988887777', 'telefone atualizado', _linha)
checar(_linha['endereco'] == 'Rua Nova, 100, Curitiba', 'endereço atualizado', _linha)
checar(_linha['placa'] == 'XYZ9Z99', 'placa normalizada em maiúsculas', _linha)


print('\n=== 8. Placa pode ser limpa (é o único campo opcional) ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={'placa': ''},
            headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'limpar a placa é aceito', r.get_json())
_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT placa FROM clientes WHERE id = ?', (cliente_id,))
_placa = dict(_cur.fetchone())['placa']
_c.close()
checar(_placa is None, 'placa ficou vazia (NULL) no banco', _placa)


print('\n=== 9. Nada para atualizar / cliente inexistente ===')
r = cli.put(f'/api/admin/clientes/{cliente_id}', json={},
            headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'corpo vazio é recusado com mensagem clara', r.get_json())

r = cli.put('/api/admin/clientes/999999', json={'nome': 'Fantasma'},
            headers={'X-Admin-Token': token})
checar(r.status_code == 404, 'editar cliente inexistente dá 404', r.get_json())


print('\n=== 10. Toda correção fica registrada na auditoria ===')
_c = get_db()
_cur = _c.cursor()
_cur.execute("SELECT acao, campo, valor_anterior, valor_novo, admin_usuario "
             "FROM auditoria WHERE acao = 'cliente_editado' AND produto_id = ? "
             "ORDER BY id", (cliente_id,))
_linhas = [dict(x) for x in _cur.fetchall()]
_c.close()
checar(len(_linhas) >= 5, 'cada campo corrigido gerou uma linha de auditoria', _linhas)
checar(all(l['admin_usuario'] == 'master' for l in _linhas), 'auditoria identifica o Master como autor', _linhas)
_cpf_linha = next((l for l in _linhas if l['campo'] == 'cpf'), None)
checar(bool(_cpf_linha) and '***' in (_cpf_linha.get('valor_novo') or ''),
       'CPF aparece MASCARADO na auditoria, nunca por inteiro', _cpf_linha)


print('\n=== 11. Listagem completa de clientes (aba "Clientes", só Master) ===')
r = cli.get('/api/admin/clientes', headers={'X-Admin-Token': token_gerencia})
checar(r.status_code == 403, 'Gerência NÃO consegue listar todos os clientes', r.get_json())

r = cli.get('/api/admin/clientes', headers={'X-Admin-Token': token})
d = r.get_json()
checar(r.status_code == 200, 'Master consegue listar todos os clientes', d)
checar(d.get('total') == 2, 'total bate com os 2 clientes cadastrados no teste', d)
checar(len(d.get('clientes', [])) == 2, 'lista traz os 2 clientes', d)
_por_id = {c['id']: c for c in d['clientes']}
checar('***' in (_por_id.get(cliente_id, {}).get('cpf') or ''),
       'CPF vem MASCARADO na listagem (edição abre o detalhe completo à parte)', d)
checar(_por_id.get(cliente_id, {}).get('nome') == 'Cliente Corrigido',
       'a listagem já reflete a correção feita na seção 7', d)

r = cli.get('/api/admin/clientes?q=corrigido', headers={'X-Admin-Token': token})
d = r.get_json()
checar(d.get('total') == 1 and d['clientes'][0]['id'] == cliente_id,
       'busca por nome dentro da listagem filtra corretamente', d)

r = cli.get('/api/admin/clientes?por_pagina=1&pagina=1', headers={'X-Admin-Token': token})
d = r.get_json()
checar(d.get('total') == 2 and len(d.get('clientes', [])) == 1 and d.get('total_paginas') == 2,
       'paginação corta a página em 1 item mas mantém o total geral certo', d)

r = cli.get('/api/admin/clientes?por_pagina=1&pagina=2', headers={'X-Admin-Token': token})
d2 = r.get_json()
checar(len(d2.get('clientes', [])) == 1 and d2['clientes'][0]['id'] != d['clientes'][0]['id'],
       'segunda página traz o outro cliente, não repete o da primeira', d2)


print('\n' + '=' * 60)
if falhas:
    print(f'❌ {len(falhas)} FALHA(S): ' + '; '.join(falhas))
    sys.exit(1)
else:
    print('✅ TODOS OS TESTES PASSARAM')
