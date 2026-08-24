"""
Verificação rápida (não faz parte da suíte oficial): confirma que um produto
com desconto_valor = 0 realmente gera cupom com desconto zero, e que a baixa
na bomba (usar_cupom) respeita esse zero em vez de recair no desconto do
cliente. Também confere que um produto com desconto normal (Gasolina Comum,
R$2,00) continua funcionando igual, sem regressão.

Rodar: python teste_desconto_zero.py
"""

import importlib.util
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
os.environ.pop('DATABASE_URL', None)

_spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appv2)

GASOLINA, PREMIUM = 1, 2

client = appv2.app.test_client()


_FOTO_FAKE = 'data:image/png;base64,' + ('A' * 2500)


def cadastrar(cpf, email, placa):
    return client.post('/api/auth/cadastro', json={
        'cpf': cpf, 'nome': 'Motorista Teste', 'ocupacao': 'taxi',
        'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
        'email': email, 'senha': 'Senha123!', 'placa': placa,
        'registro_tipo': 'condutax', 'registro_numero': 'ABC123',
        'foto_comprovante': _FOTO_FAKE,
        'aceita_promocoes': True, 'aceita_parceiros': True,
    })


def login_admin_master():
    # cria o primeiro admin (master) se ainda não existir
    r_setup = client.post('/api/admin/setup', json={
        'usuario': 'master', 'senha': 'Senha123!', 'nome': 'Master Teste',
        'poster_id': 'jardins', 'email': 'master@teste.com'
    })
    r = client.post('/api/admin/login', json={'usuario': 'master', 'senha': 'Senha123!'})
    d = r.get_json()
    if 'token' not in d:
        print('setup:', r_setup.status_code, r_setup.get_json())
        print('login:', r.status_code, d)
    return d['token']


def set_preco_custo_e_desconto(token, produto_id, custo, preco_bomba, desconto):
    return client.post('/api/admin/produtos/atualizar',
                        headers={'X-Admin-Token': token},
                        json={'produtos': [{
                            'id': produto_id, 'preco_atual': preco_bomba,
                            'preco_custo': custo, 'desconto_valor': desconto,
                            'desconto_tipo': 'fixo', 'limite_litros': 50, 'ativo': 1,
                            'confirma_variacao': True,
                        }]})


token = login_admin_master()

# Gasolina Comum: custo 5.81, bomba 8.49, desconto 2.00 (igual à produção)
r = set_preco_custo_e_desconto(token, GASOLINA, 5.81, 8.49, 2.00)
assert r.status_code == 200, r.get_json()

# Gasolina Premium: custo 8.07, bomba 9.99, desconto 0.00 (o que o Edmundo quer)
r = set_preco_custo_e_desconto(token, PREMIUM, 8.07, 9.99, 0.00)
assert r.status_code == 200, r.get_json()

# Cadastra um motorista — nasce com desconto_valor=1.00 hardcoded no cadastro()
r = cadastrar('11144477735', 'motorista@teste.com', 'ABC1234')
assert r.status_code == 201, r.get_json()

r = client.post('/api/auth/login', json={'email': 'motorista@teste.com', 'senha': 'Senha123!'})
cliente_id = r.get_json()['cliente_id']

# ---- Gasolina Premium: desconto deve nascer em 0.00, não em 1.00 ----
r = client.post('/api/cupom/gerar',
                 json={'cliente_id': cliente_id, 'produto_id': PREMIUM, 'quantidade': 50})
d = r.get_json()
assert r.status_code == 200, d
print('Premium — desconto_por_unidade na geração:', d['desconto_por_unidade'])
assert d['desconto_por_unidade'] == 0, f"ESPERADO 0, VEIO {d['desconto_por_unidade']}"

qrcode_premium = d['qrcode_data']
cupom_id_premium = d['cupom_id']

# ---- Dá baixa na Premium (frentista na pista) — desconto tem que continuar 0 ----
r = client.post('/api/cupom/usar', headers={'X-Admin-Token': token},
                 json={'qrcode': qrcode_premium, 'produto_id': PREMIUM,
                       'quantidade': 20, 'valor_sem_desconto': 0, 'poster_id': 'jardins'})
d = r.get_json()
assert r.status_code == 200, d
print('Premium — valor_desconto na baixa:', d['valor_desconto'])
assert d['valor_desconto'] == 0, f"ESPERADO 0, VEIO {d['valor_desconto']}"
print('Premium — valor_final na baixa:', d['valor_final'])
assert abs(d['valor_final'] - (9.99 * 20)) < 0.01

# ---- Gasolina Comum: desconto de R$2,00 continua funcionando igual (sem regressão) ----
# Cliente novo, porque o de cima já gastou o cupom de combustível do dia
# (regra de 1 por categoria por dia — ver limite-de-cupons.md).
r = cadastrar('52998224725', 'motorista2@teste.com', 'XYZ9999')
assert r.status_code == 201, r.get_json()
r = client.post('/api/auth/login', json={'email': 'motorista2@teste.com', 'senha': 'Senha123!'})
cliente_id_2 = r.get_json()['cliente_id']

r = client.post('/api/cupom/gerar',
                 json={'cliente_id': cliente_id_2, 'produto_id': GASOLINA, 'quantidade': 50})
d = r.get_json()
assert r.status_code == 200, d
print('Comum — desconto_por_unidade na geração:', d['desconto_por_unidade'])
assert d['desconto_por_unidade'] == 2.0, f"ESPERADO 2.0, VEIO {d['desconto_por_unidade']}"

qrcode_comum = d['qrcode_data']

r = client.post('/api/cupom/usar', headers={'X-Admin-Token': token},
                 json={'qrcode': qrcode_comum, 'produto_id': GASOLINA,
                       'quantidade': 20, 'valor_sem_desconto': 0, 'poster_id': 'jardins'})
d = r.get_json()
assert r.status_code == 200, d
print('Comum — valor_desconto na baixa:', d['valor_desconto'])
assert abs(d['valor_desconto'] - (2.0 * 20)) < 0.01

print('\nTUDO OK — zero fica zero, e o desconto normal continua igual.')
