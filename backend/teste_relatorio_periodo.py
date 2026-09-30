"""
Testes do relatório de caixa por período (vários dias).

O endpoint /api/admin/caixa aceitava só um dia (`data`). Agora aceita
`data_fim` e soma o intervalo inclusive — semana, mês, o que for.

Rodar:  python teste_relatorio_periodo.py
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

# (data, hora, turno, posto, produto_id, litros, valor_original, desconto, valor_final)
MOVIMENTO = [
    ('2026-08-31', '10:00:00', 'Turno 1', 'CAJ', 1, 10, 58.0, 5.0, 53.0),   # fora (agosto)
    ('2026-09-01', '07:00:00', 'Turno 1', 'CAJ', 1, 10, 58.0, 5.0, 53.0),
    ('2026-09-01', '15:00:00', 'Turno 2', 'SKY', 3, 20, 80.0, 5.0, 75.0),
    ('2026-09-15', '23:00:00', 'Turno 3', 'CAJ', 1, 30, 174.0, 5.0, 169.0),
    ('2026-09-30', '12:00:00', 'Turno 1', 'SKY', 1, 40, 232.0, 5.0, 227.0),
    ('2026-10-01', '09:00:00', 'Turno 1', 'CAJ', 1, 10, 58.0, 5.0, 53.0),   # fora (outubro)
]


class Periodo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appv2.app.config['TESTING'] = True
        cls.c = appv2.app.test_client()
        cls.c.post('/api/admin/setup', json={
            'usuario': 'master', 'senha': 'senhaMaster1',
            'email': 'master@teste.com', 'nome': 'Edmundo'})
        r = cls.c.post('/api/admin/login', json={'usuario': 'master', 'senha': 'senhaMaster1'})
        cls.h = {'X-Admin-Token': r.get_json()['token']}

        conn = appv2.get_db()
        cur = conn.cursor()
        for m in MOVIMENTO:
            cur.execute(
                'INSERT INTO abastecimentos (cliente_id, produto_id, poster_id, data, hora, '
                'turno, quantidade, valor_original, valor_desconto, valor_final) '
                'VALUES (0, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (m[4], m[3], m[0], m[1], m[2], m[5], m[6], m[7], m[8]))
        conn.commit()
        conn.close()

    def get(self, qs):
        return self.c.get('/api/admin/caixa?' + qs, headers=self.h)

    # ---------- o caminho feliz ----------
    def test_mes_inteiro_soma_so_setembro(self):
        r = self.get('data=2026-09-01&data_fim=2026-09-30')
        self.assertEqual(r.status_code, 200)
        t = r.get_json()['total']
        self.assertEqual(t['abastecimentos'], 4)
        self.assertEqual(t['litros'], 100.0)
        self.assertEqual(t['valor_recebido'], 524.0)
        self.assertEqual(t['desconto_concedido'], 20.0)

    def test_limites_do_intervalo_sao_inclusivos(self):
        d = self.get('data=2026-09-01&data_fim=2026-09-01').get_json()
        self.assertEqual(d['total']['abastecimentos'], 2)
        d = self.get('data=2026-09-30&data_fim=2026-10-01').get_json()
        self.assertEqual(d['total']['abastecimentos'], 2)

    def test_resumo_dia_a_dia(self):
        d = self.get('data=2026-09-01&data_fim=2026-09-30').get_json()
        dias = {x['data']: x for x in d['por_dia']}
        self.assertEqual(sorted(dias), ['2026-09-01', '2026-09-15', '2026-09-30'])
        self.assertEqual(dias['2026-09-01']['abastecimentos'], 2)
        self.assertEqual(dias['2026-09-01']['valor_recebido'], 128.0)
        # a soma dos dias bate com o total
        self.assertEqual(round(sum(x['valor_recebido'] for x in d['por_dia']), 2),
                         d['total']['valor_recebido'])

    def test_detalhes_trazem_a_data(self):
        d = self.get('data=2026-09-01&data_fim=2026-09-30').get_json()
        self.assertTrue(all('data' in x for x in d['detalhes']))
        datas = [x['data'] for x in d['detalhes']]
        self.assertEqual(datas, sorted(datas))

    def test_filtros_de_posto_e_produto_valem_no_periodo(self):
        d = self.get('data=2026-09-01&data_fim=2026-09-30&poster_id=SKY').get_json()
        self.assertEqual(d['total']['abastecimentos'], 2)
        d = self.get('data=2026-09-01&data_fim=2026-09-30&produto_id=3').get_json()
        self.assertEqual(d['total']['abastecimentos'], 1)

    # ---------- compatibilidade: o dia único continua igual ----------
    def test_dia_unico_sem_data_fim_continua_funcionando(self):
        d = self.get('data=2026-09-01').get_json()
        self.assertEqual(d['total']['abastecimentos'], 2)
        self.assertEqual(d['data_fim'], '2026-09-01')

    def test_janela_de_horas_continua_valendo_em_um_dia(self):
        d = self.get('data=2026-09-01&hora_inicio=14:00&hora_fim=22:00').get_json()
        self.assertEqual(d['total']['abastecimentos'], 1)

    def test_janela_de_horas_e_ignorada_em_varios_dias(self):
        # hora só faz sentido num dia; num intervalo, vale o dia inteiro
        d = self.get('data=2026-09-01&data_fim=2026-09-30&hora_inicio=14:00&hora_fim=22:00').get_json()
        self.assertEqual(d['total']['abastecimentos'], 4)

    # ---------- tentar burlar ----------
    def test_data_final_antes_da_inicial_e_recusada(self):
        r = self.get('data=2026-09-30&data_fim=2026-09-01')
        self.assertEqual(r.status_code, 400)

    def test_data_invalida_e_recusada(self):
        self.assertEqual(self.get('data=30/09/2026').status_code, 400)
        self.assertEqual(self.get('data=2026-09-01&data_fim=abc').status_code, 400)

    def test_periodo_acima_de_um_ano_e_recusado(self):
        r = self.get('data=2025-01-01&data_fim=2026-09-30')
        self.assertEqual(r.status_code, 400)

    def test_sem_login_nao_ve_nada(self):
        r = self.c.get('/api/admin/caixa?data=2026-09-01&data_fim=2026-09-30')
        self.assertIn(r.status_code, (401, 403))

    def test_periodo_sem_movimento_devolve_zero(self):
        d = self.get('data=2026-01-01&data_fim=2026-01-31').get_json()
        self.assertEqual(d['total']['abastecimentos'], 0)
        self.assertEqual(d['por_dia'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
