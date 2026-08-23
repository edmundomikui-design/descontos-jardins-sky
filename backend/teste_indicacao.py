"""
Teste de ponta a ponta do programa de indicação, rodando contra um SQLite
temporário — sem encostar no banco de produção.

Percorre o roteiro real: cadastra o indicador, pega o link dele, cadastra 3
indicados por esse link, faz cada um gerar e usar cupom (com volumes que
testam a regra dos litros), e confere se o prêmio nasce na hora certa e
entra no cupom seguinte do indicador.
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
        '19100000000', '76887453043', '39053344705', '48151623733']
FOTO = 'data:image/jpeg;base64,' + ('A' * 3000)


def cadastrar(nome, i, tel, ref=None):
    r = cli.post('/api/auth/cadastro', json={
        'nome': nome, 'cpf': CPFS[i], 'ocupacao': 'Táxi',
        'tel': tel, 'endereco': 'Rua Estados Unidos, 1930, Jardins, Sao Paulo',
        'email': f'{nome.lower()}@teste.com', 'senha': 'senha123',
        'aceita_promocoes': True, 'placa': f'ABC{1000 + i}',
        'foto_comprovante': FOTO, 'indicado_por_codigo': ref
    })
    return r.get_json(), r.status_code


def criar_admin_e_token():
    cli.post('/api/admin/setup', json={
        'usuario': 'master', 'senha': 'senha123', 'poster_id': 'CAJ',
        'nome': 'Master', 'email': 'master@teste.com'
    })
    r = cli.post('/api/admin/login', json={'usuario': 'master', 'senha': 'senha123'})
    return r.get_json().get('token')


def abastecer(cliente_id, litros, token, produto_id=3):
    """Gera cupom e dá baixa com a quantidade pedida. Devolve as duas respostas."""
    # confirmar_troca: no teste rodamos vários abastecimentos no mesmo dia,
    # então pode haver cupom de outro produto ainda em aberto. Em produção
    # isso é uma pergunta na tela; aqui já respondemos 'sim'.
    g = cli.post('/api/cupom/gerar',
                 json={'cliente_id': cliente_id, 'produto_id': produto_id,
                       'confirmar_troca': True}).get_json()
    if 'qrcode_data' not in g:
        return g, {'erro': 'cupom não gerado'}
    u = cli.post('/api/cupom/usar',
                 json={'qrcode': g['qrcode_data'], 'produto_id': produto_id,
                       'quantidade': litros},
                 headers={'X-Admin-Token': token}).get_json()
    return g, u


def liberar_cupom_extra(cliente_id, token, motivo='teste'):
    """O limite é 1 cupom de combustível por dia — para abastecer de novo no
    mesmo dia de teste, usa a liberação do Master, que já existe no sistema."""
    return cli.post('/api/admin/liberacoes',
                    json={'cliente_id': cliente_id, 'categoria': 'combustivel',
                          'motivo': motivo},
                    headers={'X-Admin-Token': token}).get_json()


print('\n=== 1. Preparação ===')
token = criar_admin_e_token()
checar(bool(token), 'login do Master no painel')

# preço/custo/desconto: etanol a R$ 4,00, custo R$ 3,00, desconto R$ 0,20/L
cli.post('/api/admin/produtos/atualizar',
         json={'produtos': [{'id': 3, 'preco_atual': 4.00, 'preco_custo': 3.00,
                             'desconto_valor': 0.20, 'desconto_tipo': 'fixo',
                             'limite_litros': 50, 'margem_minima': 10}]},
         headers={'X-Admin-Token': token})

cfg = cli.get('/api/admin/indicacoes/config',
              headers={'X-Admin-Token': token}).get_json()
checar(cfg.get('meta_indicacoes') == 3, 'meta padrão = 3 indicações', cfg)
checar(cfg.get('valor_recompensa') == 10.0, 'prêmio padrão = R$ 10,00', cfg)
checar(cfg.get('minimo_litros_combustivel') == 20, 'mínimo combustível = 20 L', cfg)
checar(cfg.get('minimo_litros_oleo') == 1, 'mínimo óleo = 1 L', cfg)
checar(cfg.get('validade_premio_dias') == 30, 'prêmio vence em 30 dias', cfg)
checar(cfg.get('validade_indicacao_dias') == 90, 'indicação vale 90 dias', cfg)
checar(not cfg.get('data_fim_campanha'), 'campanha sem prazo por padrão', cfg)
checar(cfg.get('campanha_encerrada') is False, 'campanha não está encerrada', cfg)


print('\n=== 2. Indicador se cadastra e recebe o código ===')
ind, _ = cadastrar('Indicador', 0, '11911110000')
codigo = ind.get('codigo_indicacao')
indicador_id = ind.get('cliente_id')
checar(bool(codigo), 'indicador recebeu um código de indicação', ind)
checar(codigo == f'CJ{indicador_id}', 'código é CJ + id do cliente', codigo)

v = cli.get(f'/api/indicacao/verificar?codigo={codigo}').get_json()
checar(v.get('valido') and v.get('nome') == 'Indicador',
       'código é reconhecido e devolve o primeiro nome', v)
checar(cli.get('/api/indicacao/verificar?codigo=CJ99999').get_json().get('valido') is False,
       'código inexistente devolve inválido')


print('\n=== 3. Indicado #1 abastece POUCO (5 L) — não pode contar ===')
a, _ = cadastrar('Amigo1', 1, '11922220000', ref=codigo)
amigo1 = a['cliente_id']
checar(a.get('veio_de_indicacao') is True, 'cadastro veio marcado como indicação', a)

abastecer(amigo1, 5, token)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['total_cadastros_indicados'] == 1, 'aparece 1 cadastro indicado', st)
checar(st['total_indicacoes_positivas'] == 0,
       'abastecimento de 5 L NÃO positivou a indicação', st)

print('\n=== 4. O MESMO indicado volta e abastece 30 L — agora conta ===')
liberar_cupom_extra(amigo1, token)
abastecer(amigo1, 30, token)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['total_indicacoes_positivas'] == 1,
       'indicação ficou aguardando e contou na volta (30 L)', st)
checar(st['faltam_para_o_proximo_premio'] == 2, 'faltam 2 para o prêmio', st)
checar(st['premios_disponiveis'] == 0, 'ainda não há prêmio', st)

print('\n=== 5. Indicado #1 abastece uma 3ª vez — não pode contar de novo ===')
liberar_cupom_extra(amigo1, token)
abastecer(amigo1, 40, token)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['total_indicacoes_positivas'] == 1,
       'o mesmo indicado não conta ponto duas vezes', st)

print('\n=== 6. Indicado #2 abastece 25 L ===')
b, _ = cadastrar('Amigo2', 2, '11933330000', ref=codigo)
abastecer(b['cliente_id'], 25, token)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['total_indicacoes_positivas'] == 2, '2ª indicação positivada', st)
checar(st['premios_disponiveis'] == 0, 'ainda sem prêmio (faltava a 3ª)', st)

print('\n=== 7. Indicado #3 abastece 20 L (exatamente o mínimo) → prêmio! ===')
c, _ = cadastrar('Amigo3', 3, '11944440000', ref=codigo)
abastecer(c['cliente_id'], 20, token)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['total_indicacoes_positivas'] == 3, '3ª indicação positivada (20 L conta)', st)
checar(st['premios_disponiveis'] == 1, 'prêmio nasceu ao completar 3', st)
checar(st['valor_premios_disponiveis'] == 10.0, 'prêmio vale R$ 10,00', st)

print('\n=== 8. Premio NAO entra em abastecimento pequeno (fica guardado) ===')
liberar_cupom_extra(indicador_id, token)
g, u = abastecer(indicador_id, 8, token)          # 8 L < minimo de 20 L
checar(g.get('bonus_indicacao') == 10.0, 'cupom nasce com o premio reservado', g)
checar(u.get('premio_adiado') is True, 'baixa de 8 L adia o premio', u)
checar(u.get('bonus_indicacao') == 0, 'premio NAO foi aplicado nesta baixa', u)
checar(u.get('valor_desconto') == 1.6, 'so o desconto normal (8 x R$ 0,20)', u)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['premios_disponiveis'] == 1, 'premio voltou para a prateleira', st)

print('\n=== 9. Premio entra em abastecimento que atinge o minimo ===')
liberar_cupom_extra(indicador_id, token)
g, u = abastecer(indicador_id, 20, token)
checar(g.get('bonus_indicacao') == 10.0, 'cupom novo nasce com o premio', g)
# 20 L x R$ 4,00 = R$ 80,00 | custo 20 x R$ 3,00 = R$ 60,00
# desconto normal 20 x R$ 0,20 = R$ 4,00 -> R$ 76,00 (acima do custo, passa na trava)
# premio R$ 10,00 entra por fora -> desconto R$ 14,00, cliente paga R$ 66,00
checar(u.get('erro') is None, 'trava de margem NAO recusa mais o premio', u)
checar(u.get('valor_original') == 80.0, 'valor cheio = R$ 80,00', u)
checar(u.get('valor_desconto') == 14.0, 'desconto = R$ 4,00 normal + R$ 10,00 premio', u)
checar(u.get('valor_final') == 66.0, 'cliente paga R$ 66,00', u)
checar(u.get('bonus_indicacao') == 10.0, 'a baixa registra o premio aplicado', u)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['premios_disponiveis'] == 0, 'premio foi consumido de vez', st)

print('\n=== 9b. Premio NAO se repete no cupom seguinte ===')
liberar_cupom_extra(indicador_id, token)
g2, u2 = abastecer(indicador_id, 20, token)
checar(g2.get('bonus_indicacao') == 0, 'cupom seguinte vem sem premio', g2)
checar(u2.get('valor_desconto') == 4.0, 'desconto volta ao normal (R$ 4,00)', u2)

print('\n=== 9c. Trava de margem continua pegando preco abaixo do custo ===')
# Cenario real documentado em 19/08: o cupom nasce certo e a Ipiranga sobe o
# custo DEPOIS. A terceira camada tem que recusar na pista.
cli.post('/api/admin/produtos/atualizar',
         json={'produtos': [{'id': 4, 'preco_atual': 6.00, 'preco_custo': 4.00,
                             'desconto_valor': 1.00, 'desconto_tipo': 'fixo',
                             'limite_litros': 50, 'margem_minima': 0}]},
         headers={'X-Admin-Token': token})
liberar_cupom_extra(indicador_id, token)
g3 = cli.post('/api/cupom/gerar',
              json={'cliente_id': indicador_id, 'produto_id': 4}).get_json()
checar('qrcode_data' in g3, 'cupom de diesel gerado normalmente', g3)

_c = get_db()
_cur = _c.cursor()
_cur.execute('UPDATE produtos SET preco_custo = 5.80 WHERE id = 4')
_c.commit()
_c.close()

u3 = cli.post('/api/cupom/usar',
              json={'qrcode': g3.get('qrcode_data'), 'produto_id': 4, 'quantidade': 20},
              headers={'X-Admin-Token': token}).get_json()
checar(u3.get('motivo') == 'desconto_abaixo_do_custo',
       'custo acima do preco final continua sendo RECUSADO na pista', u3)

print('\n=== 10. Trava de autoindicação (mesmo telefone) ===')
d, _ = cadastrar('Espertinho', 4, '11911110000', ref=codigo)   # telefone do indicador
checar(d.get('veio_de_indicacao') is False,
       'cadastro com o telefone do indicador NÃO vira indicação', d)
checar(d.get('cliente_id') is not None, 'mas o cadastro em si é aceito normalmente', d)

print('\n=== 11. Master muda o valor do prêmio ===')
r = cli.post('/api/admin/indicacoes/config',
             json={'meta_indicacoes': 2, 'valor_recompensa': 25.0,
                   'minimo_litros_combustivel': 15, 'minimo_litros_oleo': 1,
                   'validade_premio_dias': 30, 'validade_indicacao_dias': 90,
                   'data_fim_campanha': '', 'ativo': True},
             headers={'X-Admin-Token': token})
checar(r.status_code == 200, 'Master consegue salvar a configuração', r.get_json())
cfg = cli.get('/api/admin/indicacoes/config',
              headers={'X-Admin-Token': token}).get_json()
checar(cfg['valor_recompensa'] == 25.0 and cfg['meta_indicacoes'] == 2
       and cfg['minimo_litros_combustivel'] == 15,
       'configuração nova foi gravada', cfg)

r = cli.post('/api/admin/indicacoes/config',
             json={'meta_indicacoes': 0, 'valor_recompensa': 10,
                   'minimo_litros_combustivel': 20, 'minimo_litros_oleo': 1,
                   'validade_premio_dias': 30, 'validade_indicacao_dias': 90},
             headers={'X-Admin-Token': token})
checar(r.status_code == 400, 'meta 0 é recusada')

print('\n=== 12. Ranking no painel ===')
rk = cli.get('/api/admin/indicacoes', headers={'X-Admin-Token': token}).get_json()
linha = next((x for x in rk['ranking'] if x['indicador_id'] == indicador_id), None)
checar(linha is not None, 'indicador aparece no ranking', rk)
if linha:
    checar(linha['total_positivas'] == 3, 'ranking mostra 3 indicações positivas', linha)
checar(len(rk['premios']) == 1, 'histórico mostra 1 prêmio concedido', rk['premios'])

print('\n=== 13. Oleo: 1 litro positiva (reposicao avulsa na pista) ===')
# Nesta altura o Master ja mudou a config: meta 2, premio R$ 25, minimo oleo 1 L.
e, _ = cadastrar('Amigo4', 5, '11955550000', ref=codigo)
antes = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
g4, u4 = abastecer(e['cliente_id'], 1, token, produto_id=6)   # 6 = Oleo Sintetico
checar(u4.get('erro') is None, 'baixa de 1 L de oleo registrada', u4)
dep = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(dep['total_indicacoes_positivas'] == antes['total_indicacoes_positivas'] + 1,
       '1 litro de OLEO positiva a indicacao', dep)
checar(dep['premios_disponiveis'] == 1, 'novo premio nasceu (meta agora e 2)', dep)
checar(dep['valor_premios_disponiveis'] == 25.0,
       'premio novo vale R$ 25,00 (valor novo da config)', dep)

def salvar_config(**kw):
    base = {'meta_indicacoes': 2, 'valor_recompensa': 25.0,
            'minimo_litros_combustivel': 15, 'minimo_litros_oleo': 1,
            'validade_premio_dias': 30, 'validade_indicacao_dias': 90,
            'data_fim_campanha': '', 'ativo': True}
    base.update(kw)
    return cli.post('/api/admin/indicacoes/config', json=base,
                    headers={'X-Admin-Token': token})


print('\n=== 14. Premio ganho recebe data de validade ===')
rk = cli.get('/api/admin/indicacoes', headers={'X-Admin-Token': token}).get_json()
ultimo = rk['premios'][0]
checar(bool(ultimo.get('validade')), 'premio nasce com data de validade', ultimo)
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(bool(st.get('premio_vence_em')), 'cliente ve ate quando o premio vale', st)

print('\n=== 15. Premio que passou da validade expira sozinho ===')
_c = get_db(); _cur = _c.cursor()
_cur.execute("UPDATE recompensas_indicacao SET validade = '2020-01-01' "
             "WHERE cliente_id = ? AND status = 'disponivel'", (indicador_id,))
_c.commit(); _c.close()
st = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(st['premios_disponiveis'] == 0, 'premio vencido sai da prateleira', st)
liberar_cupom_extra(indicador_id, token)
g5, u5 = abastecer(indicador_id, 20, token)
checar(g5.get('bonus_indicacao') == 0, 'cupom novo nao pega premio vencido', g5)
rk = cli.get('/api/admin/indicacoes', headers={'X-Admin-Token': token}).get_json()
checar(any(p['status'] == 'expirado' for p in rk['premios']),
       'painel mostra o premio como vencido', rk['premios'][:2])

print('\n=== 16. Campanha encerrada para de gerar pontos ===')
r = salvar_config(data_fim_campanha='2020-12-31')
checar(r.status_code == 200, 'data de fim no passado e aceita', r.get_json())
cfg = cli.get('/api/admin/indicacoes/config',
              headers={'X-Admin-Token': token}).get_json()
checar(cfg['campanha_encerrada'] is True, 'config avisa que a campanha encerrou', cfg)

f, _ = cadastrar('Amigo5', 6, '11966660000', ref=codigo)
antes = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
abastecer(f['cliente_id'], 40, token)
dep = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(dep['total_indicacoes_positivas'] == antes['total_indicacoes_positivas'],
       'com a campanha encerrada, abastecer NAO positiva', dep)
checar(dep['programa_ativo'] is False, 'cartao do cliente mostra programa inativo', dep)

print('\n=== 17. Prorrogar a campanha faz a indicacao voltar a valer ===')
salvar_config(data_fim_campanha='2099-12-31')
liberar_cupom_extra(f['cliente_id'], token)
abastecer(f['cliente_id'], 40, token)
dep2 = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(dep2['total_indicacoes_positivas'] == antes['total_indicacoes_positivas'] + 1,
       'indicacao nao foi queimada: conta depois da prorrogacao', dep2)

print('\n=== 18. Indicacao velha demais nao conta mais ===')
salvar_config(data_fim_campanha='', validade_indicacao_dias=30)
g, _ = cadastrar('Amigo6', 7, '11977770000', ref=codigo)
_c = get_db(); _cur = _c.cursor()
_cur.execute("UPDATE clientes SET data_criacao = '2020-01-01 10:00:00' WHERE id = ?",
             (g['cliente_id'],))
_c.commit(); _c.close()
antes3 = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
abastecer(g['cliente_id'], 40, token)
dep3 = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(dep3['total_indicacoes_positivas'] == antes3['total_indicacoes_positivas'],
       'cadastro de 2020 com prazo de 30 dias NAO positiva', dep3)

print('\n=== 19. Prazo 0 = sem prazo ===')
salvar_config(validade_indicacao_dias=0)
liberar_cupom_extra(g['cliente_id'], token)
abastecer(g['cliente_id'], 40, token)
dep4 = cli.get(f'/api/cliente/{indicador_id}/indicacao').get_json()
checar(dep4['total_indicacoes_positivas'] == antes3['total_indicacoes_positivas'] + 1,
       'com prazo 0 a mesma indicacao antiga volta a contar', dep4)

r = salvar_config(data_fim_campanha='31/12/2026')
checar(r.status_code == 400, 'data em formato errado e recusada', r.get_json())

print('\n' + '=' * 60)
if falhas:
    print(f'❌ {len(falhas)} FALHA(S):')
    for f in falhas:
        print('   -', f)
    sys.exit(1)
print('✅ TODOS OS TESTES PASSARAM')
