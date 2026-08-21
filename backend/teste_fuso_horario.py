"""
Testes do fuso horário.

O servidor do Render roda em UTC, três horas à frente de Brasília. Sem
corrigir, quatro coisas saem erradas: a hora no comprovante, o turno do
abastecimento, o "dia" do cupom (que passaria a virar às 21h) e a data dos
relatórios de fechamento.

Estes testes existem para que isso não volte a quebrar em silêncio. Eles
FORÇAM o processo a rodar em UTC — simulando o Render — e conferem que as
datas gravadas continuam saindo em horário de Brasília.

Rodar:  python teste_fuso_horario.py
"""

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# Simula o servidor do Render: processo rodando em UTC.
os.environ['TZ'] = 'UTC'
try:
    time.tzset()
except AttributeError:
    pass   # Windows não tem tzset; o teste ainda vale pelo ZoneInfo

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
os.environ.pop('DATABASE_URL', None)

_spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appv2)

GASOLINA = 1


class TestFuso(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        appv2.app.config['TESTING'] = True
        cls.c = appv2.app.test_client()
        cls.c.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senhaMaster1',
            'email': 'm@teste.com', 'nome': 'Edmundo'})
        r = cls.c.post('/api/admin/login',
                       json={'usuario': 'master', 'senha': 'senhaMaster1'})
        cls.tok = r.get_json()['token']

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('UPDATE produtos SET preco_custo = 1.00, desconto_valor = 0.50')
        conn.commit()
        conn.close()

    def test_01_o_processo_esta_mesmo_em_utc(self):
        """Se este falhar, o teste não está simulando o Render e não prova nada."""
        diferenca = abs((datetime.now() - datetime.utcnow()).total_seconds())
        self.assertLess(diferenca, 60,
                        'o processo não está em UTC — o teste perderia o sentido')

    def test_02_agora_devolve_brasilia_e_nao_utc(self):
        brasilia = datetime.now(timezone(timedelta(hours=-3))).replace(tzinfo=None)
        diferenca = abs((appv2.agora() - brasilia).total_seconds())
        self.assertLess(diferenca, 60,
                        f'agora() devolveu {appv2.agora()}, esperado ~{brasilia}')

    def test_03_agora_esta_3h_atras_do_utc(self):
        diferenca = (datetime.utcnow() - appv2.agora()).total_seconds()
        self.assertAlmostEqual(diferenca, 3 * 3600, delta=90,
                               msg='a diferença para o UTC não é de 3 horas')

    def test_04_agora_nao_tem_fuso_embutido(self):
        """Datas com fuso quebrariam as subtrações feitas no resto do código."""
        self.assertIsNone(appv2.agora().tzinfo)

    def test_05_turno_usa_a_hora_de_brasilia(self):
        """
        O caso que mais dói: às 12h de Brasília o UTC marca 15h. Sem correção,
        o abastecimento do almoço cairia no Turno 2 em vez do Turno 1.
        """
        hora_br = appv2.agora().hour
        self.assertEqual(appv2.obter_turno(), appv2.obter_turno(hora_br))
        # E a classificação por hora continua correta
        self.assertIn('Turno 1', appv2.obter_turno(9))
        self.assertIn('Turno 2', appv2.obter_turno(16))
        self.assertIn('Turno 3', appv2.obter_turno(23))

    def test_06_data_do_cupom_e_a_de_brasilia(self):
        """
        O mais perigoso: em UTC o dia virava às 21h de Brasília. Quem gerasse
        cupom às 21h30 de segunda estaria gastando o cupom de terça — e o
        limite de um por dia seguiria esse dia deslocado.
        """
        cli = {'cpf': '52998224725', 'nome': 'Joao', 'ocupacao': 'taxi',
               'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
               'email': 'fuso1@teste.com', 'senha': 'senha123', 'placa': 'ABC1D23',
               'registro_numero': '1', 'aceita_promocoes': True,
               'foto_comprovante': 'data:image/jpeg;base64,' + ('A' * 3000)}
        self.c.post('/api/auth/cadastro', json=cli)

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM clientes WHERE email = 'fuso1@teste.com'")
        cid = cur.fetchone()['id']
        conn.close()

        self.c.post('/api/cupom/gerar', json={'cliente_id': cid, 'produto_id': GASOLINA})

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT data_geracao FROM cupons WHERE cliente_id = ?', (cid,))
        gravada = cur.fetchone()['data_geracao']
        conn.close()

        self.assertEqual(gravada, appv2.agora().strftime('%Y-%m-%d'))

    def test_07_hora_do_abastecimento_e_a_de_brasilia(self):
        """É o que o motorista lê no comprovante impresso."""
        cli = {'cpf': '11144477735', 'nome': 'Maria', 'ocupacao': 'taxi',
               'tel': '11999999999', 'endereco': 'Rua dos Testes, 200 - Sao Paulo',
               'email': 'fuso2@teste.com', 'senha': 'senha123', 'placa': 'XYZ9K88',
               'registro_numero': '2', 'aceita_promocoes': True,
               'foto_comprovante': 'data:image/jpeg;base64,' + ('A' * 3000)}
        self.c.post('/api/auth/cadastro', json=cli)

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM clientes WHERE email = 'fuso2@teste.com'")
        cid = cur.fetchone()['id']
        conn.close()

        g = self.c.post('/api/cupom/gerar',
                        json={'cliente_id': cid, 'produto_id': GASOLINA})
        qr = g.get_json()['qrcode_data']

        r = self.c.post('/api/cupom/usar', headers={'X-Admin-Token': self.tok},
                        json={'qrcode': qr, 'produto_id': GASOLINA,
                              'quantidade': 10, 'valor_sem_desconto': 58.90})
        self.assertEqual(r.status_code, 200, r.get_json())

        hora_resposta = r.get_json()['hora']
        self.assertEqual(hora_resposta, appv2.agora().strftime('%H:%M'))

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT data, hora FROM abastecimentos WHERE cliente_id = ?', (cid,))
        linha = cur.fetchone()
        conn.close()

        self.assertEqual(linha['data'], appv2.agora().strftime('%Y-%m-%d'))
        self.assertEqual(linha['hora'][:5], appv2.agora().strftime('%H:%M'))

    def test_08_fechamento_de_turno_grava_hora_de_brasilia(self):
        r = self.c.post('/api/frentista/fechar-turno',
                        headers={'X-Admin-Token': self.tok})
        self.assertEqual(r.status_code, 200, r.get_json())
        fechado = r.get_json()['fechado_em']
        self.assertEqual(fechado[:10], appv2.agora().strftime('%Y-%m-%d'))
        self.assertEqual(fechado[11:16], appv2.agora().strftime('%H:%M'))

    def test_09_auditoria_registra_hora_de_brasilia(self):
        r = self.c.get('/api/admin/auditoria', headers={'X-Admin-Token': self.tok})
        registros = r.get_json().get('registros') or r.get_json().get('auditoria') or []
        if not registros:
            self.skipTest('sem registros de auditoria para conferir')
        data_hora = registros[0].get('data_hora') or ''
        self.assertTrue(data_hora.startswith(appv2.agora().strftime('%Y-%m-%d')),
                        f'auditoria gravou {data_hora}, esperado hoje em Brasília')

    def test_10_nao_sobrou_nenhum_datetime_now_solto(self):
        """
        Uma chamada esquecida a datetime.now() volta a gravar UTC sem avisar.
        Este teste lê o próprio código-fonte para garantir que só existe uma:
        a de dentro da função agora().
        """
        with open(os.path.join(BASE, 'app-v2.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertEqual(fonte.count('datetime.now()'), 0,
                         'existe datetime.now() sem fuso no código — use agora()')
        self.assertEqual(fonte.count('datetime.now(FUSO_BRASILIA)'), 1,
                         'a função agora() deveria ser o único ponto que lê o relógio')
        self.assertEqual(fonte.count('datetime.utcnow()'), 0,
                         'utcnow() grava UTC direto — não deve existir')


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        os.unlink(_tmp.name)
