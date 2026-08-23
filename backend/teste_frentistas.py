"""
Teste de ponta a ponta da campanha de frentistas, rodando contra um SQLite
temporário — sem encostar no banco de produção.

Frentista é cliente diferenciado (decisão de 23/08): 1 cupom de combustível
a cada 7 dias corridos, ZERO cupom de óleo, e participa do programa de
indicação normalmente. A conta só nasce ou se converte pela mão do Master.
"""
import os
import re
import sys
import tempfile
import importlib.util
from datetime import datetime, timedelta

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
        '19100000000', '76887453043', '39053344705', '48151623733',
        '12345678909']
FOTO = 'data:image/jpeg;base64,' + ('A' * 3000)


def criar_admin_e_token(usuario='master', senha='senha123'):
    """O primeiro usuário criado (setup) sempre vira Master. Para Gerência,
    usa o endpoint de criar usuário, que só o Master pode chamar."""
    if usuario == 'master':
        cli.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senha123', 'poster_id': 'CAJ',
            'nome': 'Master', 'email': 'master@teste.com'
        })
    r = cli.post('/api/admin/login', json={'usuario': usuario, 'senha': senha})
    return r.get_json().get('token')


def cadastrar_comum(nome, i, tel, ref=None):
    r = cli.post('/api/auth/cadastro', json={
        'nome': nome, 'cpf': CPFS[i], 'ocupacao': 'Táxi',
        'tel': tel, 'endereco': 'Rua Estados Unidos, 1930, Jardins, Sao Paulo',
        'email': f'{nome.lower()}@teste.com', 'senha': 'senha123',
        'aceita_promocoes': True, 'placa': f'ABC{1000 + i}',
        'foto_comprovante': FOTO, 'indicado_por_codigo': ref
    })
    return r.get_json(), r.status_code


def criar_frentista(token, nome='Frentista Ze', i=0, placa='FRT0001', cpf=None):
    email_seguro = re.sub(r'[^a-z0-9]', '', nome.lower())
    r = cli.post('/api/admin/frentistas', json={
        'nome': nome, 'cpf': cpf or CPFS[i], 'tel': '11900000000',
        'email': f'{email_seguro}@equipe.com',
        'senha': 'senha123', 'placa': placa
    }, headers={'X-Admin-Token': token})
    return r.get_json(), r.status_code


def gerar(cliente_id, produto_id, confirmar_troca=False):
    return cli.post('/api/cupom/gerar',
                    json={'cliente_id': cliente_id, 'produto_id': produto_id,
                          'confirmar_troca': confirmar_troca}).get_json()


print('\n=== 1. Preparação ===')
token = criar_admin_e_token()
checar(bool(token), 'login do Master no painel')

cli.post('/api/admin/produtos/atualizar',
         json={'produtos': [{'id': 3, 'preco_atual': 4.00, 'preco_custo': 3.00,
                             'desconto_valor': 0.20, 'desconto_tipo': 'fixo',
                             'limite_litros': 50, 'margem_minima': 10}]},
         headers={'X-Admin-Token': token})


print('\n=== 2. Só o Master cria conta de frentista ===')
r_gerencia = cli.post('/api/admin/usuarios',
                      json={'usuario': 'gerente1', 'senha': 'senha12345',
                            'nivel': 'gerencia', 'email': 'gerente1@teste.com'},
                      headers={'X-Admin-Token': token})
checar(r_gerencia.status_code == 201, 'Master cria usuário de Gerência', r_gerencia.get_json())
token_gerencia = criar_admin_e_token('gerente1', senha='senha12345')
checar(bool(token_gerencia), 'login da Gerência no painel')

d, status = criar_frentista(token_gerencia, nome='Tentativa Gerencia', i=0)
checar(status == 403, 'Gerência NÃO consegue criar frentista', d)

d, status = criar_frentista(token, nome='Frentista Zé', i=0, placa='FRT0001')
checar(status == 201, 'Master cria a conta do frentista', d)
frentista_id = d.get('cliente_id')
checar(bool(frentista_id), 'conta veio com id', d)


print('\n=== 3. Frentista gera e USA o cupom de combustível da semana ===')
g1 = gerar(frentista_id, 3)  # Etanol Comum
checar('qrcode_data' in g1, 'primeiro cupom de combustível sai normalmente', g1)
u1 = cli.post('/api/cupom/usar',
              json={'qrcode': g1['qrcode_data'], 'produto_id': 3, 'quantidade': 30},
              headers={'X-Admin-Token': token}).get_json()
checar(u1.get('erro') is None, 'frentista abastece o próprio carro sem problema', u1)


print('\n=== 4. Frentista NÃO consegue um segundo combustível na mesma semana ===')
g2 = gerar(frentista_id, 1, confirmar_troca=True)  # Gasolina Comum, produto diferente
checar(g2.get('limite_atingido') is True,
       'segundo cupom de combustível na semana é recusado', g2)
checar('semana' in (g2.get('erro') or '').lower(),
       'mensagem fala em semana, não em dia', g2)


print('\n=== 5. Frentista NUNCA consegue cupom de óleo (mesmo pela primeira vez) ===')
g3 = gerar(frentista_id, 6)  # Óleo Sintético
checar(g3.get('limite_atingido') is True and g3.get('categoria') == 'oleo',
       'cupom de óleo é recusado de cara, sem nunca ter pego antes', g3)
checar('frentistas' in (g3.get('erro') or '').lower(),
       'mensagem explica que óleo não faz parte do programa de frentistas', g3)


print('\n=== 6. Master pode liberar uma exceção de óleo, se quiser ===')
cli.post('/api/admin/liberacoes',
         json={'cliente_id': frentista_id, 'categoria': 'oleo', 'motivo': 'caso especial'},
         headers={'X-Admin-Token': token})
g4 = gerar(frentista_id, 6)
checar('qrcode_data' in g4, 'com liberação do Master, o óleo sai desta vez', g4)

g5 = gerar(frentista_id, 7, confirmar_troca=True)
checar(g5.get('limite_atingido') is True,
       'liberação foi de uso único — segundo óleo volta a ser recusado', g5)


print('\n=== 7. Sete dias depois, o combustível libera de novo ===')
_c = get_db()
_cur = _c.cursor()
_data_passada = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d')
_cur.execute("UPDATE cupons SET data_geracao = ? WHERE cliente_id = ? "
             "AND COALESCE(categoria, 'combustivel') = 'combustivel'",
             (_data_passada, frentista_id))
_c.commit()
_c.close()
g6 = gerar(frentista_id, 3)
checar('qrcode_data' in g6, 'depois de 7 dias corridos, novo combustível libera', g6)


print('\n=== 8. Não dá para criar frentista duplicado com o mesmo CPF ===')
d, status = criar_frentista(token, nome='Frentista Zé de Novo', i=0)
checar(status == 400, 'CPF que já é frentista é recusado', d)


print('\n=== 9. Converter um cliente comum já existente em frentista ===')
comum, _ = cadastrar_comum('ClienteVirouFrentista', 1, '11955550000')
comum_id = comum.get('cliente_id')
d, status = criar_frentista(token, nome='ClienteVirouFrentista', i=1)
checar(status == 200, 'CPF já existente vira CONVERSÃO (200, não 201)', d)
checar(d.get('criado') is False, 'resposta indica conversão, não criação', d)

_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT tipo_cliente FROM clientes WHERE id = ?', (comum_id,))
_tipo = dict(_cur.fetchone())
_c.close()
checar(_tipo['tipo_cliente'] == 'frentista', 'conta convertida agora é frentista', _tipo)


print('\n=== 10. Reverter frentista para cliente comum ===')
r = cli.post(f'/api/admin/frentistas/{comum_id}/reverter',
             headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'Master reverte a conta para comum', r.get_json())

_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT tipo_cliente FROM clientes WHERE id = ?', (comum_id,))
_tipo2 = dict(_cur.fetchone())
_c.close()
checar(_tipo2['tipo_cliente'] == 'comum', 'voltou a ser cliente comum', _tipo2)

# Comum de volta: já pode gerar cupom de óleo (nunca usou) e o limite de
# combustível volta a ser por dia, não por semana.
g7 = gerar(comum_id, 6)
checar('qrcode_data' in g7, 'como cliente comum, óleo volta a ser permitido', g7)


print('\n=== 11. Validações da criação ===')
d, status = criar_frentista(token, nome='', i=2)
checar(status == 400, 'nome vazio é recusado', d)

r = cli.post('/api/admin/frentistas',
             json={'nome': 'Sem CPF Valido', 'cpf': '11111111111',
                   'email': 'x@teste.com', 'senha': 'senha123'},
             headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'CPF inválido é recusado', r.get_json())

r = cli.post('/api/admin/frentistas',
             json={'nome': 'Sem Email', 'cpf': CPFS[2], 'email': 'nao-e-email',
                   'senha': 'senha123'},
             headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'email inválido é recusado', r.get_json())

r = cli.post('/api/admin/frentistas',
             json={'nome': 'Senha Curta', 'cpf': CPFS[2], 'email': 'y@teste.com',
                   'senha': '123'},
             headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'senha curta é recusada', r.get_json())


print('\n=== 12. Frentista participa do programa de indicação normalmente ===')
d2, status2 = criar_frentista(token, nome='Frentista Indicador', i=3, placa='FRT0002')
checar(status2 == 201, 'segundo frentista criado', d2)
frentista2_id = d2.get('cliente_id')
codigo_frentista = d2.get('codigo_indicacao') if 'codigo_indicacao' in d2 else None

# O endpoint de criação de frentista não devolve codigo_indicacao hoje —
# busca direto no banco, do mesmo jeito que a tela faria.
_c = get_db()
_cur = _c.cursor()
_cur.execute('SELECT codigo_indicacao FROM clientes WHERE id = ?', (frentista2_id,))
codigo_frentista = dict(_cur.fetchone())['codigo_indicacao']
_c.close()
checar(bool(codigo_frentista), 'frentista também recebe código de indicação', codigo_frentista)

for i, nome in enumerate(['IndicadoPeloFrentista1', 'IndicadoPeloFrentista2',
                           'IndicadoPeloFrentista3']):
    d3, _ = cadastrar_comum(nome, 4 + i, f'1196600{i}000', ref=codigo_frentista)
    g = gerar(d3['cliente_id'], 3)
    cli.post('/api/cupom/usar',
             json={'qrcode': g['qrcode_data'], 'produto_id': 3, 'quantidade': 25},
             headers={'X-Admin-Token': token})

st = cli.get(f'/api/cliente/{frentista2_id}/indicacao').get_json()
checar(st['total_indicacoes_positivas'] == 3,
       'as 3 indicações do frentista positivaram normalmente', st)
checar(st['premios_disponiveis'] == 1,
       'frentista ganhou o prêmio de indicação igual qualquer cliente', st)
checar(st['valor_premios_disponiveis'] == 10.0, 'prêmio vale R$ 10,00', st)

# O frentista também gera o próprio cupom de combustível da semana — é o
# que a listagem do painel (seção 13) vai mostrar como "gerado".
g_f2 = gerar(frentista2_id, 3)
checar('qrcode_data' in g_f2, 'frentista2 também gera seu cupom da semana', g_f2)


print('\n=== 13. Painel lista os frentistas com o status do cupom da semana ===')
r = cli.get('/api/admin/frentistas', headers={'X-Admin-Token': token})
lista = r.get_json().get('frentistas', [])
checar(r.status_code == 200, 'listagem responde 200')
checar(any(f['id'] == frentista_id for f in lista), 'frentista original aparece na lista', lista)
checar(all('***' in (f['cpf'] or '') for f in lista), 'CPF aparece mascarado na lista', lista)
linha = next(f for f in lista if f['id'] == frentista2_id)
checar(linha['cupom_semana'] == 'gerado', 'frentista2 mostra cupom gerado nesta semana', linha)


print('\n' + '=' * 60)
if falhas:
    print(f'❌ {len(falhas)} FALHA(S): ' + '; '.join(falhas))
    sys.exit(1)
else:
    print('✅ TODOS OS TESTES PASSARAM')
