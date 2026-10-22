"""
Testes do BLOQUEIO DE VENDA POR POSTO (anti-trambique de caixa).

A regra: o posto de cada abastecimento vem do LOGIN de quem dá a baixa, e
ninguém escolhe. Frentista/caixa do Jardins só vende no Jardins; o do Sky só
no Sky. Quem está como AMBOS (ou sem posto) só consulta — não registra venda.
O 'poster_id' que a tela mandar é ignorado.

Rodar:  python teste_bloqueio_venda.py
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

GASOLINA = 1


def cliente_novo(cpf, email, placa='ABC1D23'):
    return {
        'cpf': cpf, 'nome': 'Motorista ' + cpf[:3], 'ocupacao': 'taxi',
        'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
        'email': email, 'senha': 'senha123', 'placa': placa,
        'registro_numero': '123456', 'aceita_promocoes': True,
        'foto_comprovante': 'data:image/jpeg;base64,' + ('A' * 3000),
    }


def _cpfs():
    """Gera CPFs válidos (com dígitos verificadores) em quantidade ilimitada."""
    base = 100000000
    while True:
        base += 7
        d = [int(x) for x in str(base)]
        for tamanho in (9, 10):
            soma = sum(v * (tamanho + 1 - i) for i, v in enumerate(d[:tamanho]))
            resto = (soma * 10) % 11
            d.append(0 if resto == 10 else resto)
        cpf = ''.join(str(x) for x in d)
        if len(set(cpf)) > 1:
            yield cpf


CPFS = _cpfs()


class BloqueioDeVenda(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appv2.app.config['TESTING'] = True
        cls.c = appv2.app.test_client()
        cls.c.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senhaMaster1',
            'email': 'master@teste.com', 'nome': 'Edmundo'})
        cls.h_master = {'X-Admin-Token': cls.login('master', 'senhaMaster1')}

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('UPDATE produtos SET preco_custo = 1.00, desconto_valor = 0.50')
        conn.commit()
        conn.close()
        cls.n = 0

    @classmethod
    def login(cls, usuario, senha):
        r = cls.c.post('/api/admin/login', json={'usuario': usuario, 'senha': senha})
        return r.get_json().get('token')

    def usuario(self, posto, nivel='caixa'):
        type(self).n += 1
        nome = f'op{self.n}'
        self.c.post('/api/admin/usuarios', headers=self.h_master, json={
            'usuario': nome, 'senha': 'senhaBoa123', 'nivel': nivel,
            'email': nome + '@teste.com', 'nome': nome, 'poster_id': posto})
        return nome, {'X-Admin-Token': self.login(nome, 'senhaBoa123')}

    def cupom(self):
        """Cria um cliente e gera um cupom. Devolve o código do QR."""
        cpf = next(CPFS)
        email = f'cli{cpf}@teste.com'
        self.c.post('/api/auth/cadastro', json=cliente_novo(cpf, email))
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT id FROM clientes WHERE email = ?', (email,))
        cid = cur.fetchone()['id']
        conn.close()
        g = self.c.post('/api/cupom/gerar', json={'cliente_id': cid, 'produto_id': GASOLINA})
        return g.get_json()['qrcode_data']

    def vender(self, headers, qr, litros=10, extra=None):
        corpo = {'qrcode': qr, 'produto_id': GASOLINA, 'quantidade': litros,
                 'valor_sem_desconto': litros * 5.89}
        corpo.update(extra or {})
        return self.c.post('/api/cupom/usar', headers=headers, json=corpo)

    def postos_gravados(self):
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT poster_id FROM abastecimentos ORDER BY id')
        postos = [r['poster_id'] for r in cur.fetchall()]
        conn.close()
        return postos

    # ---------- cada um vende só no seu posto ----------
    def test_frentista_do_caj_vende_no_caj(self):
        _, h = self.usuario('CAJ')
        r = self.vender(h, self.cupom())
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.postos_gravados()[-1], 'CAJ')

    def test_frentista_do_sky_vende_no_sky(self):
        _, h = self.usuario('SKY')
        r = self.vender(h, self.cupom())
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.postos_gravados()[-1], 'SKY')

    def test_mandar_outro_posto_na_baixa_nao_adianta(self):
        # tentativa de trambique: frentista do CAJ manda 'SKY' (ou 'AMBOS')
        _, h = self.usuario('CAJ')
        for tentativa in ('SKY', 'AMBOS', 'sky'):
            r = self.vender(h, self.cupom(), extra={'poster_id': tentativa})
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(self.postos_gravados()[-1], 'CAJ', tentativa)

    # ---------- quem não tem posto só consulta ----------
    def test_ambos_nao_registra_venda_e_nao_gasta_o_cupom(self):
        _, h_ambos = self.usuario('AMBOS', nivel='gerencia')
        _, h_caj = self.usuario('CAJ')
        qr = self.cupom()
        antes = len(self.postos_gravados())

        r = self.vender(h_ambos, qr)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.postos_gravados()), antes)   # nada foi gravado

        # o cupom continua intacto: um frentista do posto consegue usá-lo
        r = self.vender(h_caj, qr)
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_master_ambos_tambem_nao_vende(self):
        qr = self.cupom()
        antes = len(self.postos_gravados())
        r = self.vender(self.h_master, qr)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.postos_gravados()), antes)

    def test_usuario_sem_posto_nao_escolhe_posto_pela_tela(self):
        # a brecha antiga: login sem posto aceitava o 'poster_id' vindo da tela
        nome, h = self.usuario('CAJ')
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("UPDATE admin SET poster_id = '' WHERE usuario = ?", (nome,))
        conn.commit()
        conn.close()
        antes = len(self.postos_gravados())
        r = self.vender(h, self.cupom(), extra={'poster_id': 'SKY'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.postos_gravados()), antes)

    def test_posto_com_valor_estranho_nao_vende(self):
        nome, h = self.usuario('CAJ')
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("UPDATE admin SET poster_id = 'jardins' WHERE usuario = ?", (nome,))
        conn.commit()
        conn.close()
        r = self.vender(h, self.cupom())
        self.assertEqual(r.status_code, 403)

    def test_a_mensagem_de_recusa_explica_o_que_fazer(self):
        _, h = self.usuario('AMBOS')
        erro = self.vender(h, self.cupom()).get_json()['erro']
        self.assertIn('posto', erro.lower())
        self.assertIn('Master', erro)

    def test_sem_login_nao_vende(self):
        r = self.c.post('/api/cupom/usar', json={'qrcode': 'x', 'produto_id': 1, 'quantidade': 5})
        self.assertIn(r.status_code, (401, 403))

    # ---------- ponta a ponta: venda, caixa e fechamento de turno ----------
    def test_cada_posto_so_enxerga_o_proprio_caixa_e_fechamento(self):
        _, h_caj = self.usuario('CAJ')
        _, h_sky = self.usuario('SKY')
        self.vender(h_caj, self.cupom(), litros=10)
        self.vender(h_sky, self.cupom(), litros=30)

        hoje = appv2.agora().strftime('%Y-%m-%d')
        d_caj = self.c.get(f'/api/admin/caixa?data={hoje}&poster_id=SKY', headers=h_caj).get_json()
        self.assertTrue(d_caj['detalhes'])
        self.assertTrue(all(x['posto'] == 'CAJ' for x in d_caj['detalhes']))

        d_sky = self.c.get(f'/api/admin/caixa?data={hoje}', headers=h_sky).get_json()
        self.assertTrue(all(x['posto'] == 'SKY' for x in d_sky['detalhes']))

        d_todos = self.c.get(f'/api/admin/caixa?data={hoje}', headers=self.h_master).get_json()
        self.assertEqual({x['posto'] for x in d_todos['detalhes']}, {'CAJ', 'SKY'})

        # o caixa de turno do frentista leva o posto dele
        fechou = self.c.post('/api/frentista/fechar-turno', headers=h_sky).get_json()
        self.assertEqual(fechou['poster_id'], 'SKY')
        lista = self.c.get('/api/admin/fechamentos', headers=h_caj).get_json()['fechamentos']
        self.assertTrue(all(f['poster_id'] == 'CAJ' for f in lista))


if __name__ == '__main__':
    unittest.main(verbosity=2)
