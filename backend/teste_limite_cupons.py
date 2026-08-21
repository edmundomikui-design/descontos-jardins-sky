"""
Testes do limite de cupons por categoria.

Regra: um cupom de combustível por dia, um de óleo a cada 7 dias, contando a
CATEGORIA e não o produto. Antes disso a trava era por produto, o que na
prática não limitava nada — cinco cupons de combustível no mesmo dia.

Duas saídas: trocar o cupom não usado, ou liberação extra do Master.

Rodar:  python teste_limite_cupons.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
os.environ.pop('DATABASE_URL', None)

_spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appv2)

# Ids dos produtos padrão (ver PRODUTOS_PADRAO em database.py)
GASOLINA, PREMIUM, ETANOL, DIESEL, ADITIVADA = 1, 2, 3, 4, 5
OLEO_SINT, OLEO_SEMI, OLEO_MIN = 6, 7, 8


def cliente_novo(cpf, email):
    return {
        'cpf': cpf, 'nome': 'Motorista Teste', 'ocupacao': 'taxi',
        'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
        'email': email, 'senha': 'senha123', 'placa': 'ABC1D23',
        'registro_numero': '123456', 'aceita_promocoes': True,
        'foto_comprovante': 'data:image/jpeg;base64,' + ('A' * 3000),
    }


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appv2.app.config['TESTING'] = True
        cls.c = appv2.app.test_client()
        cls.c.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senhaMaster1',
            'email': 'master@teste.com', 'nome': 'Master'})
        r = cls.c.post('/api/admin/login',
                       json={'usuario': 'master', 'senha': 'senhaMaster1'})
        cls.tok = r.get_json()['token']

        # Custo baixo para nenhum teste esbarrar na trava de margem.
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('UPDATE produtos SET preco_custo = 1.00, desconto_valor = 0.50')
        conn.commit()
        conn.close()

    def cab(self):
        return {'X-Admin-Token': self.tok}

    def criar_cliente(self, cpf, email):
        r = self.c.post('/api/auth/cadastro', json=cliente_novo(cpf, email))
        self.assertIn(r.status_code, (200, 201), r.get_json())
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT id FROM clientes WHERE email = ?', (email,))
        cid = cur.fetchone()['id']
        conn.close()
        return cid

    def gerar(self, cliente_id, produto_id, confirmar_troca=False):
        corpo = {'cliente_id': cliente_id, 'produto_id': produto_id}
        if confirmar_troca:
            corpo['confirmar_troca'] = True
        return self.c.post('/api/cupom/gerar', json=corpo)

    def marcar_usado(self, cliente_id, status='completo'):
        """Simula o frentista dando baixa no cupom mais recente."""
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('''UPDATE cupons SET status = ? WHERE id =
                       (SELECT MAX(id) FROM cupons WHERE cliente_id = ?)''',
                    (status, cliente_id))
        conn.commit()
        conn.close()

    def recuar_cupons(self, cliente_id, dias):
        """Empurra os cupons do cliente para trás no tempo."""
        nova = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('UPDATE cupons SET data_geracao = ? WHERE cliente_id = ?',
                    (nova, cliente_id))
        conn.commit()
        conn.close()


class TestCombustivel(Base):

    def test_01_o_buraco_antigo_esta_fechado(self):
        """Cinco combustíveis diferentes no mesmo dia: só o primeiro passa."""
        cid = self.criar_cliente('52998224725', 'comb1@teste.com')

        primeiro = self.gerar(cid, GASOLINA)
        self.assertEqual(primeiro.status_code, 200)

        # Os outros quatro esbarram — antes, todos passavam.
        for produto in (PREMIUM, ETANOL, DIESEL, ADITIVADA):
            r = self.gerar(cid, produto)
            self.assertNotEqual(r.status_code, 200,
                                f'produto {produto} passou e não devia')

    def test_02_mesmo_produto_avisa_que_ja_tem(self):
        cid = self.criar_cliente('11144477735', 'comb2@teste.com')
        self.gerar(cid, GASOLINA)
        r = self.gerar(cid, GASOLINA)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json().get('ja_tem'))

    def test_03_produto_diferente_oferece_troca(self):
        cid = self.criar_cliente('12345678909', 'comb3@teste.com')
        self.gerar(cid, GASOLINA)
        r = self.gerar(cid, ETANOL)
        self.assertEqual(r.status_code, 409)
        d = r.get_json()
        self.assertTrue(d.get('pode_trocar'))
        self.assertIn('Gasolina', d['cupom_atual_produto'])

    def test_04_troca_confirmada_cancela_o_antigo(self):
        cid = self.criar_cliente('39053344705', 'comb4@teste.com')
        antigo = self.gerar(cid, GASOLINA).get_json()['cupom_id']

        novo = self.gerar(cid, ETANOL, confirmar_troca=True)
        self.assertEqual(novo.status_code, 200)
        self.assertTrue(novo.get_json()['trocou'])

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT status, trocado_por FROM cupons WHERE id = ?', (antigo,))
        linha = cur.fetchone()
        conn.close()
        self.assertEqual(linha['status'], 'cancelado')
        self.assertEqual(linha['trocado_por'], novo.get_json()['cupom_id'])

    def test_05_troca_nao_gasta_o_direito_do_dia(self):
        """Trocar não é gerar dois: depois da troca continua valendo um só."""
        cid = self.criar_cliente('88663017120', 'comb5@teste.com')
        self.gerar(cid, GASOLINA)
        self.gerar(cid, ETANOL, confirmar_troca=True)
        # Um terceiro produto ainda deve oferecer troca, não liberar
        r = self.gerar(cid, DIESEL)
        self.assertEqual(r.status_code, 409)

    def test_06_cupom_cancelado_some_da_tela_do_cliente(self):
        cid = self.criar_cliente('29537995500', 'comb6@teste.com')
        self.gerar(cid, GASOLINA)
        self.gerar(cid, ETANOL, confirmar_troca=True)

        r = self.c.get(f'/api/cupom/ativos?cliente_id={cid}')
        cupons = r.get_json()['cupons']
        self.assertEqual(len(cupons), 1, 'o cupom trocado continuou aparecendo')
        self.assertIn('Etanol', cupons[0]['produto_nome'])

    def test_07_depois_de_usado_nao_troca_mais(self):
        cid = self.criar_cliente('19100000000', 'comb7@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)

        r = self.gerar(cid, ETANOL)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.get_json().get('limite_atingido'))

    def test_08_no_dia_seguinte_libera(self):
        cid = self.criar_cliente('44444444440', 'comb8@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.recuar_cupons(cid, 1)

        r = self.gerar(cid, GASOLINA)
        self.assertEqual(r.status_code, 200)

    def test_09_frentista_ve_que_o_cupom_foi_trocado(self):
        cid = self.criar_cliente('63929969000', 'comb9@teste.com')
        antigo_qr = self.gerar(cid, GASOLINA).get_json()['qrcode_data']
        self.gerar(cid, ETANOL, confirmar_troca=True)

        r = self.c.get(f'/api/cupom/consultar?qrcode={antigo_qr}', headers=self.cab())
        d = r.get_json()
        self.assertFalse(d['valido'])
        self.assertIn('trocado', d['motivo'].lower())

    def test_10_baixa_em_cupom_trocado_e_recusada(self):
        cid = self.criar_cliente('35524767000', 'comb10@teste.com')
        antigo_qr = self.gerar(cid, GASOLINA).get_json()['qrcode_data']
        self.gerar(cid, ETANOL, confirmar_troca=True)

        r = self.c.post('/api/cupom/usar', headers=self.cab(), json={
            'qrcode': antigo_qr, 'produto_id': GASOLINA,
            'quantidade': 10, 'valor_sem_desconto': 50})
        self.assertNotEqual(r.status_code, 200)


class TestOleo(Base):

    def test_20_oleos_diferentes_no_mesmo_dia_nao_passam(self):
        cid = self.criar_cliente('70410120000', 'oleo1@teste.com')
        self.assertEqual(self.gerar(cid, OLEO_SINT).status_code, 200)
        r = self.gerar(cid, OLEO_SEMI)
        self.assertNotEqual(r.status_code, 200, 'trocar de óleo burlou a regra')

    def test_21_oleo_nao_consome_o_direito_de_combustivel(self):
        """As duas categorias são independentes."""
        cid = self.criar_cliente('01234567890', 'oleo2@teste.com')
        self.assertEqual(self.gerar(cid, OLEO_SINT).status_code, 200)
        self.assertEqual(self.gerar(cid, GASOLINA).status_code, 200)

    def test_22_tres_dias_depois_ainda_bloqueia(self):
        cid = self.criar_cliente('98765432100', 'oleo3@teste.com')
        self.gerar(cid, OLEO_SINT)
        self.marcar_usado(cid)
        self.recuar_cupons(cid, 3)

        r = self.gerar(cid, OLEO_MIN)
        self.assertEqual(r.status_code, 403)
        self.assertIn('óleo', r.get_json()['erro'].lower())

    def test_23_sete_dias_depois_libera(self):
        cid = self.criar_cliente('15350946056', 'oleo4@teste.com')
        self.gerar(cid, OLEO_SINT)
        self.marcar_usado(cid)
        self.recuar_cupons(cid, 7)

        self.assertEqual(self.gerar(cid, OLEO_SINT).status_code, 200)

    def test_24_mensagem_diz_a_data_do_proximo(self):
        cid = self.criar_cliente('40289front', 'oleo5@teste.com') \
            if False else self.criar_cliente('12345678901', 'oleo5@teste.com')
        self.gerar(cid, OLEO_SINT)
        self.marcar_usado(cid)

        r = self.gerar(cid, OLEO_SEMI)
        erro = r.get_json()['erro']
        esperada = (datetime.now() + timedelta(days=7)).strftime('%d/%m/%Y')
        self.assertIn(esperada, erro, f'a mensagem não diz quando libera: {erro}')


class TestLiberacaoMaster(Base):

    def liberar(self, cliente_id, categoria='combustivel', motivo='Viagem longa'):
        return self.c.post('/api/admin/liberacoes', headers=self.cab(), json={
            'cliente_id': cliente_id, 'categoria': categoria, 'motivo': motivo})

    def test_30_liberacao_permite_um_cupom_a_mais(self):
        cid = self.criar_cliente('33333333330', 'lib1@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.assertEqual(self.gerar(cid, ETANOL).status_code, 403)

        self.assertEqual(self.liberar(cid).status_code, 201)

        r = self.gerar(cid, ETANOL)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['usou_liberacao'])

    def test_31_liberacao_vale_uma_vez_so(self):
        cid = self.criar_cliente('55555555550', 'lib2@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.liberar(cid)
        self.gerar(cid, ETANOL)          # consome
        self.marcar_usado(cid)

        r = self.gerar(cid, DIESEL)      # não há mais liberação
        self.assertEqual(r.status_code, 403)

    def test_32_motivo_e_obrigatorio(self):
        cid = self.criar_cliente('66666666660', 'lib3@teste.com')
        r = self.c.post('/api/admin/liberacoes', headers=self.cab(), json={
            'cliente_id': cid, 'categoria': 'combustivel', 'motivo': ''})
        self.assertEqual(r.status_code, 400)
        self.assertIn('motivo', r.get_json()['erro'].lower())

    def test_33_nao_acumula_duas_liberacoes_abertas(self):
        cid = self.criar_cliente('77777777770', 'lib4@teste.com')
        self.assertEqual(self.liberar(cid).status_code, 201)
        r = self.liberar(cid)
        self.assertEqual(r.status_code, 400)
        self.assertIn('em aberto', r.get_json()['erro'])

    def test_34_liberacao_de_oleo_nao_serve_para_combustivel(self):
        cid = self.criar_cliente('88888888880', 'lib5@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.liberar(cid, categoria='oleo', motivo='Troca de oleo cortesia')

        r = self.gerar(cid, ETANOL)
        self.assertEqual(r.status_code, 403, 'liberação de óleo liberou combustível')

    def test_35_qualquer_serve_para_as_duas(self):
        cid = self.criar_cliente('99999999990', 'lib6@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.liberar(cid, categoria='qualquer', motivo='Cliente antigo, cortesia')
        self.assertEqual(self.gerar(cid, ETANOL).status_code, 200)

    def test_36_liberacao_expirada_nao_vale(self):
        cid = self.criar_cliente('10101010100', 'lib7@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.liberar(cid)

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("UPDATE liberacoes_extras SET validade = '2020-01-01' "
                    "WHERE cliente_id = ?", (cid,))
        conn.commit()
        conn.close()

        self.assertEqual(self.gerar(cid, ETANOL).status_code, 403)

    def test_37_liberacao_cancelada_nao_vale(self):
        cid = self.criar_cliente('20202020200', 'lib8@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        criada = self.liberar(cid)
        self.assertEqual(criada.status_code, 201)

        r = self.c.get('/api/admin/liberacoes', headers=self.cab())
        aberta = [l for l in r.get_json()['abertas'] if l['cliente_id'] == cid][0]

        self.c.post(f"/api/admin/liberacoes/{aberta['id']}/cancelar", headers=self.cab())
        self.assertEqual(self.gerar(cid, ETANOL).status_code, 403)

    def test_38_gerencia_nao_pode_liberar(self):
        """Liberar cupom extra é dar desconto fora da regra: só Master."""
        self.c.post('/api/admin/usuarios', headers=self.cab(), json={
            'usuario': 'gerente', 'senha': 'senhaBoa123', 'nivel': 'gerencia',
            'email': 'gerente@teste.com', 'nome': 'Gerente'})
        login = self.c.post('/api/admin/login',
                            json={'usuario': 'gerente', 'senha': 'senhaBoa123'})
        tok_ger = login.get_json()['token']

        cid = self.criar_cliente('30303030300', 'lib9@teste.com')
        r = self.c.post('/api/admin/liberacoes',
                        headers={'X-Admin-Token': tok_ger},
                        json={'cliente_id': cid, 'categoria': 'combustivel',
                              'motivo': 'tentativa indevida'})
        self.assertIn(r.status_code, (401, 403))

    def test_39_liberacao_usada_nao_pode_ser_cancelada(self):
        cid = self.criar_cliente('40404040400', 'lib10@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)
        self.liberar(cid)
        r = self.c.get('/api/admin/liberacoes', headers=self.cab())
        aberta = [l for l in r.get_json()['abertas'] if l['cliente_id'] == cid][0]
        self.gerar(cid, ETANOL)   # consome

        cancelar = self.c.post(f"/api/admin/liberacoes/{aberta['id']}/cancelar",
                               headers=self.cab())
        self.assertEqual(cancelar.status_code, 400)

    def test_40_busca_mostra_o_consumo_do_cliente(self):
        cid = self.criar_cliente('50505050500', 'busca@teste.com')
        self.gerar(cid, GASOLINA)
        self.marcar_usado(cid)

        r = self.c.get('/api/admin/clientes/buscar?q=ABC1D23', headers=self.cab())
        self.assertEqual(r.status_code, 200)
        achados = [c for c in r.get_json()['clientes'] if c['id'] == cid]
        self.assertEqual(len(achados), 1)
        consumo = achados[0]['consumo']
        self.assertTrue(consumo['combustivel']['usado'])
        self.assertFalse(consumo['oleo']['tem_cupom'])

    def test_41_busca_nao_expoe_o_cpf_inteiro(self):
        r = self.c.get('/api/admin/clientes/buscar?q=Motorista', headers=self.cab())
        for c in r.get_json()['clientes']:
            self.assertIn('*', c['cpf'], 'CPF apareceu inteiro na busca')

    def test_42_liberacao_fica_na_auditoria(self):
        cid = self.criar_cliente('60606060600', 'aud@teste.com')
        self.liberar(cid, motivo='Motivo que precisa aparecer na auditoria')

        r = self.c.get('/api/admin/auditoria', headers=self.cab())
        texto = str(r.get_json())
        self.assertIn('Motivo que precisa aparecer', texto)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        os.unlink(_tmp.name)
