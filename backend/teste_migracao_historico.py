"""
Testes da correção do histórico de postos (AMBOS -> CAJ).

Até 22/10/2026 só o Jardins vendeu, mas os usuários nasciam com posto AMBOS e
as vendas foram gravadas assim. A correção passa tudo que não é CAJ nem SKY
para CAJ, UMA vez, guardando o valor antigo para poder desfazer.

Rodar:  python teste_migracao_historico.py
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
# Por padrão roda em SQLite temporário. Para conferir também no PostgreSQL (o
# banco do Render), aponte TESTE_DATABASE_URL para um banco de teste.
if os.environ.get('TESTE_DATABASE_URL'):
    os.environ['DATABASE_URL'] = os.environ['TESTE_DATABASE_URL']
else:
    os.environ.pop('DATABASE_URL', None)

import database  # noqa: E402


def nova_venda(cur, posto, data='2026-10-20'):
    cur.execute(
        'INSERT INTO abastecimentos (cliente_id, produto_id, poster_id, data, hora, turno, '
        'quantidade, valor_original, valor_desconto, valor_final) '
        "VALUES (0, 1, ?, ?, '10:00:00', 'Turno 1', 10, 58.0, 5.0, 53.0)", (posto, data))
    return cur.lastrowid


def novo_fechamento(cur, posto):
    cur.execute(
        'INSERT INTO fechamentos_turno (usuario, nome, poster_id, fechado_em, total_liquido) '
        "VALUES ('func', 'Func', ?, '2026-10-20 14:00:00', 100)", (posto,))
    return cur.lastrowid


def postos(cur, tabela):
    cur.execute(f'SELECT id, poster_id FROM {tabela} ORDER BY id')
    return {r['id']: r['poster_id'] for r in cur.fetchall()}


class MigracaoHistorico(unittest.TestCase):
    def setUp(self):
        """Cada teste começa de um banco 'antigo': tabelas vazias e sem a trava."""
        database.init_db()
        conn = database.get_db()
        cur = conn.cursor()
        for t in ('abastecimentos', 'fechamentos_turno', 'backup_posto_historico'):
            cur.execute(f'DELETE FROM {t}')
        cur.execute('DELETE FROM migracoes_dados')
        conn.commit()
        conn.close()

    def rodar(self):
        database.init_db()

    def test_ambos_vira_caj_em_vendas_e_fechamentos(self):
        conn = database.get_db()
        cur = conn.cursor()
        v1 = nova_venda(cur, 'AMBOS')
        v2 = nova_venda(cur, 'AMBOS', '2026-10-21')
        f1 = novo_fechamento(cur, 'AMBOS')
        conn.commit()
        conn.close()

        self.rodar()

        conn = database.get_db()
        cur = conn.cursor()
        self.assertEqual(postos(cur, 'abastecimentos'), {v1: 'CAJ', v2: 'CAJ'})
        self.assertEqual(postos(cur, 'fechamentos_turno'), {f1: 'CAJ'})
        conn.close()

    def test_valores_estranhos_e_vazios_tambem_viram_caj(self):
        conn = database.get_db()
        cur = conn.cursor()
        a = nova_venda(cur, 'jardins')
        b = nova_venda(cur, '')
        c = nova_venda(cur, 'N/A')
        conn.commit()
        conn.close()
        self.rodar()
        conn = database.get_db()
        self.assertEqual(set(postos(conn.cursor(), 'abastecimentos').values()), {'CAJ'})
        conn.close()

    def test_o_que_ja_e_caj_ou_sky_nao_e_tocado(self):
        conn = database.get_db()
        cur = conn.cursor()
        caj = nova_venda(cur, 'CAJ')
        sky = nova_venda(cur, 'SKY')
        ambos = nova_venda(cur, 'AMBOS')
        f_sky = novo_fechamento(cur, 'SKY')
        conn.commit()
        conn.close()
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        p = postos(cur, 'abastecimentos')
        self.assertEqual((p[caj], p[sky], p[ambos]), ('CAJ', 'SKY', 'CAJ'))
        self.assertEqual(postos(cur, 'fechamentos_turno')[f_sky], 'SKY')
        conn.close()

    def test_valores_dos_abastecimentos_nao_mudam(self):
        conn = database.get_db()
        cur = conn.cursor()
        v = nova_venda(cur, 'AMBOS')
        conn.commit()
        conn.close()
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute('SELECT * FROM abastecimentos WHERE id = ?', (v,))
        r = cur.fetchone()
        conn.close()
        self.assertEqual((r['quantidade'], r['valor_original'], r['valor_desconto'],
                          r['valor_final'], r['data'], r['hora'], r['turno']),
                         (10, 58.0, 5.0, 53.0, '2026-10-20', '10:00:00', 'Turno 1'))

    def test_guarda_o_valor_antigo_para_desfazer(self):
        conn = database.get_db()
        cur = conn.cursor()
        v = nova_venda(cur, 'AMBOS')
        f = novo_fechamento(cur, 'jardins')
        nova_venda(cur, 'SKY')   # esta não deve entrar no backup
        conn.commit()
        conn.close()
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute('SELECT tabela, registro_id, poster_id_antigo FROM backup_posto_historico')
        backup = {(r['tabela'], r['registro_id']): r['poster_id_antigo'] for r in cur.fetchall()}
        conn.close()
        self.assertEqual(backup, {('abastecimentos', v): 'AMBOS', ('fechamentos_turno', f): 'jardins'})

    def test_roda_uma_vez_so(self):
        self.rodar()   # primeira vez: marca como feita
        conn = database.get_db()
        cur = conn.cursor()
        depois = nova_venda(cur, 'AMBOS')   # algo gravado como AMBOS depois da correção
        conn.commit()
        conn.close()

        self.rodar()   # reiniciar o servidor não pode mexer de novo
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        self.assertEqual(postos(cur, 'abastecimentos')[depois], 'AMBOS')
        cur.execute('SELECT COUNT(*) AS n FROM migracoes_dados WHERE nome = ?',
                    (database.MIGRACAO_HISTORICO_CAJ,))
        self.assertEqual(cur.fetchone()['n'], 1)
        conn.close()

    def test_reiniciar_nao_duplica_o_backup(self):
        conn = database.get_db()
        cur = conn.cursor()
        nova_venda(cur, 'AMBOS')
        conn.commit()
        conn.close()
        self.rodar()
        self.rodar()
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) AS n FROM backup_posto_historico')
        self.assertEqual(cur.fetchone()['n'], 1)
        conn.close()

    def test_banco_vazio_so_registra_a_trava(self):
        self.rodar()
        conn = database.get_db()
        cur = conn.cursor()
        cur.execute('SELECT detalhe FROM migracoes_dados WHERE nome = ?',
                    (database.MIGRACAO_HISTORICO_CAJ,))
        linha = cur.fetchone()
        conn.close()
        self.assertIsNotNone(linha)
        self.assertIn('abastecimentos: 0', linha['detalhe'])

    def test_desfazer_com_o_backup_restaura_o_posto_antigo(self):
        conn = database.get_db()
        cur = conn.cursor()
        v = nova_venda(cur, 'AMBOS')
        conn.commit()
        conn.close()
        self.rodar()

        conn = database.get_db()
        cur = conn.cursor()
        cur.execute(
            'UPDATE abastecimentos SET poster_id = '
            '(SELECT poster_id_antigo FROM backup_posto_historico b '
            " WHERE b.tabela = 'abastecimentos' AND b.registro_id = abastecimentos.id) "
            "WHERE id IN (SELECT registro_id FROM backup_posto_historico WHERE tabela = 'abastecimentos')")
        conn.commit()
        self.assertEqual(postos(cur, 'abastecimentos')[v], 'AMBOS')
        conn.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
