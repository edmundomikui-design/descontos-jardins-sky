"""
Testes do turno do frentista e do fechamento.

A regra: o turno é tudo o que AQUELE frentista registrou desde o fechamento
anterior dele. Não é o relógio que manda — quem entra 5h e sai 13h30 tem um
relatório só. O que amarra isso é o `fechamento_id` no abastecimento.

Rodar:  python teste_turno_frentista.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
os.environ.pop('DATABASE_URL', None)

_spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appv2)

GASOLINA, ETANOL = 1, 3


def cliente_novo(cpf, email, placa='ABC1D23'):
    return {
        'cpf': cpf, 'nome': 'Motorista ' + cpf[:3], 'ocupacao': 'taxi',
        'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
        'email': email, 'senha': 'senha123', 'placa': placa,
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
            'email': 'master@teste.com', 'nome': 'Edmundo'})
        cls.tok_master = cls.login('master', 'senhaMaster1')

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('UPDATE produtos SET preco_custo = 1.00, desconto_valor = 0.50')
        conn.commit()
        conn.close()

    @classmethod
    def login(cls, usuario, senha):
        r = cls.c.post('/api/admin/login', json={'usuario': usuario, 'senha': senha})
        return r.get_json().get('token')

    def criar_frentista(self, usuario, posto='CAJ'):
        self.c.post('/api/admin/usuarios',
                    headers={'X-Admin-Token': self.tok_master},
                    json={'usuario': usuario, 'senha': 'senhaBoa123', 'nivel': 'caixa',
                          'email': usuario + '@teste.com', 'nome': usuario.title(),
                          'poster_id': posto})
        return self.login(usuario, 'senhaBoa123')

    def abastecer(self, token, cpf, email, produto=GASOLINA, litros=10, placa='ABC1D23'):
        """Cria cliente, gera cupom e dá baixa. Devolve a resposta da baixa."""
        self.c.post('/api/auth/cadastro', json=cliente_novo(cpf, email, placa))
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT id FROM clientes WHERE email = ?', (email,))
        cid = cur.fetchone()['id']
        conn.close()

        g = self.c.post('/api/cupom/gerar',
                        json={'cliente_id': cid, 'produto_id': produto})
        qr = g.get_json()['qrcode_data']
        return self.c.post('/api/cupom/usar', headers={'X-Admin-Token': token},
                           json={'qrcode': qr, 'produto_id': produto,
                                 'quantidade': litros,
                                 'valor_sem_desconto': litros * 5.89})


class TestTurno(Base):

    def test_01_turno_comeca_vazio(self):
        tok = self.criar_frentista('carlos')
        r = self.c.get('/api/frentista/turno', headers={'X-Admin-Token': tok})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d['totais']['abastecimentos'], 0)
        self.assertIsNone(d['turno_desde'])

    def test_02_abastecimento_entra_no_turno(self):
        tok = self.criar_frentista('lourival')
        self.abastecer(tok, '52998224725', 't2a@teste.com', litros=20)

        d = self.c.get('/api/frentista/turno',
                       headers={'X-Admin-Token': tok}).get_json()
        self.assertEqual(d['totais']['abastecimentos'], 1)
        self.assertEqual(d['totais']['litros'], 20.0)
        self.assertEqual(d['totais']['desconto'], 10.0)   # 20 L × R$ 0,50

    def test_03_cada_frentista_ve_so_o_seu(self):
        """Dois frentistas no mesmo posto não podem enxergar o turno do outro."""
        tok_a = self.criar_frentista('frenta')
        tok_b = self.criar_frentista('frentb')

        self.abastecer(tok_a, '11144477735', 't3a@teste.com', litros=10)
        self.abastecer(tok_b, '12345678909', 't3b@teste.com', litros=30)

        da = self.c.get('/api/frentista/turno',
                        headers={'X-Admin-Token': tok_a}).get_json()
        db = self.c.get('/api/frentista/turno',
                        headers={'X-Admin-Token': tok_b}).get_json()
        self.assertEqual(da['totais']['litros'], 10.0)
        self.assertEqual(db['totais']['litros'], 30.0)

    def test_04_resumo_por_produto_soma_certo(self):
        tok = self.criar_frentista('resumo')
        self.abastecer(tok, '39053344705', 't4a@teste.com', GASOLINA, 10)
        self.abastecer(tok, '88663017120', 't4b@teste.com', GASOLINA, 15)
        self.abastecer(tok, '29537995500', 't4c@teste.com', ETANOL, 20)

        d = self.c.get('/api/frentista/turno',
                       headers={'X-Admin-Token': tok}).get_json()
        por = {p['produto']: p for p in d['por_produto']}
        self.assertEqual(por['Gasolina Comum']['vezes'], 2)
        self.assertEqual(por['Gasolina Comum']['quantidade'], 25.0)
        self.assertEqual(por['Etanol Comum']['vezes'], 1)
        self.assertEqual(d['totais']['abastecimentos'], 3)
        self.assertEqual(d['totais']['litros'], 45.0)

    def test_05_itens_trazem_o_que_o_papel_precisa(self):
        tok = self.criar_frentista('papel')
        self.abastecer(tok, '19100000000', 't5@teste.com', litros=12, placa='XYZ9K88')

        d = self.c.get('/api/frentista/turno',
                       headers={'X-Admin-Token': tok}).get_json()
        item = d['itens'][0]
        for campo in ('hora', 'cliente', 'placa', 'produto', 'quantidade',
                      'bruto', 'desconto', 'liquido', 'cupom'):
            self.assertIn(campo, item)
        self.assertEqual(item['placa'], 'XYZ9K88')


class TestFechamento(Base):

    def test_20_fechar_sem_movimento_e_recusado(self):
        tok = self.criar_frentista('vazio')
        r = self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})
        self.assertEqual(r.status_code, 400)

    def test_21_fechamento_devolve_os_totais(self):
        tok = self.criar_frentista('fecha1')
        self.abastecer(tok, '44444444440', 'f1a@teste.com', litros=10)
        self.abastecer(tok, '63929969000', 'f1b@teste.com', litros=25)

        r = self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d['totais']['abastecimentos'], 2)
        self.assertEqual(d['totais']['litros'], 35.0)
        self.assertIn('fechamento_id', d)
        self.assertEqual(len(d['itens']), 2, 'o relatório precisa dos itens para imprimir')

    def test_22_fechar_derruba_a_sessao(self):
        """A trava: sem isso, um abastecimento feito após o fechamento entraria
        no relatório seguinte e a soma impressa deixaria de bater."""
        tok = self.criar_frentista('fecha2')
        self.abastecer(tok, '35524767000', 'f2@teste.com')
        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})

        r = self.c.get('/api/frentista/turno', headers={'X-Admin-Token': tok})
        self.assertEqual(r.status_code, 401, 'o token continuou valendo depois de fechar')

    def test_23_novo_turno_comeca_zerado(self):
        tok = self.criar_frentista('fecha3')
        self.abastecer(tok, '70410120000', 'f3a@teste.com', litros=40)
        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})

        tok2 = self.login('fecha3', 'senhaBoa123')
        d = self.c.get('/api/frentista/turno',
                       headers={'X-Admin-Token': tok2}).get_json()
        self.assertEqual(d['totais']['abastecimentos'], 0)
        self.assertIsNotNone(d['turno_desde'], 'o novo turno deve saber quando o anterior fechou')

    def test_24_o_que_vem_depois_entra_no_proximo(self):
        tok = self.criar_frentista('fecha4')
        self.abastecer(tok, '01234567890', 'f4a@teste.com', litros=10)
        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})

        tok2 = self.login('fecha4', 'senhaBoa123')
        self.abastecer(tok2, '98765432100', 'f4b@teste.com', litros=50)

        d = self.c.get('/api/frentista/turno',
                       headers={'X-Admin-Token': tok2}).get_json()
        self.assertEqual(d['totais']['abastecimentos'], 1)
        self.assertEqual(d['totais']['litros'], 50.0, 'o turno novo pegou o movimento do antigo')

    def test_25_abastecimentos_ficam_carimbados(self):
        tok = self.criar_frentista('carimbo')
        self.abastecer(tok, '15350946056', 'f5@teste.com')
        r = self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})
        fid = r.get_json()['fechamento_id']

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("SELECT fechamento_id FROM abastecimentos WHERE registrado_por = 'carimbo'")
        for linha in cur.fetchall():
            self.assertEqual(linha['fechamento_id'], fid)
        conn.close()

    def test_26_fechar_nao_mexe_no_turno_do_colega(self):
        tok_a = self.criar_frentista('colegaa')
        tok_b = self.criar_frentista('colegab')
        self.abastecer(tok_a, '12345678901', 'f6a@teste.com', litros=10)
        self.abastecer(tok_b, '33333333330', 'f6b@teste.com', litros=20)

        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok_a})

        db = self.c.get('/api/frentista/turno',
                        headers={'X-Admin-Token': tok_b}).get_json()
        self.assertEqual(db['totais']['abastecimentos'], 1,
                         'fechar o turno de um zerou o do outro')

    def test_27_fechamento_aparece_para_a_gerencia(self):
        tok = self.criar_frentista('visivel')
        self.abastecer(tok, '55555555550', 'f7@teste.com', litros=33)
        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})

        r = self.c.get('/api/admin/fechamentos',
                       headers={'X-Admin-Token': self.tok_master})
        self.assertEqual(r.status_code, 200)
        meus = [f for f in r.get_json()['fechamentos'] if f['usuario'] == 'visivel']
        self.assertEqual(len(meus), 1)
        self.assertEqual(meus[0]['litros'], 33.0)

    def test_28_fechamento_fica_na_auditoria(self):
        tok = self.criar_frentista('auditado')
        self.abastecer(tok, '66666666660', 'f8@teste.com')
        self.c.post('/api/frentista/fechar-turno', headers={'X-Admin-Token': tok})

        r = self.c.get('/api/admin/auditoria',
                       headers={'X-Admin-Token': self.tok_master})
        self.assertIn('auditado', str(r.get_json()))

    def test_29_baixa_devolve_placa_e_cupom_para_o_comprovante(self):
        tok = self.criar_frentista('compr')
        r = self.abastecer(tok, '77777777770', 'f9@teste.com', placa='QRS4T56')
        d = r.get_json()
        self.assertEqual(d['placa'], 'QRS4T56')
        self.assertTrue(d.get('cupom'), 'o comprovante precisa do código do cupom')

    def test_30_sem_login_nao_ve_turno_nenhum(self):
        r = self.c.get('/api/frentista/turno')
        self.assertIn(r.status_code, (401, 403))


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        os.unlink(_tmp.name)
