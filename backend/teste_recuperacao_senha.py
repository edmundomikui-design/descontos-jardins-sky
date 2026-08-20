"""
Testes da recuperação de senha ("esqueci minha senha").

Cobre motorista e equipe do painel. O envio de e-mail é substituído por uma
caixa de correio falsa: o teste não manda e-mail de verdade, mas confere que
o link certo foi montado e para quem.

Rodar:  python teste_recuperacao_senha.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest

# O arquivo se chama "app-v2.py" (com hífen), que não é nome de módulo válido.
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# Banco isolado por execução — nunca encostar no banco real.
_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_PATH'] = _tmp.name
os.environ.pop('DATABASE_URL', None)
os.environ['RESEND_API_KEY'] = 'chave-de-teste'

_spec = importlib.util.spec_from_file_location('appv2', os.path.join(BASE, 'app-v2.py'))
appv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(appv2)

CAIXA = []   # e-mails que teriam sido enviados


def _fake_link(destino, nome, token, tipo='cliente', validade_minutos=60):
    CAIXA.append({'para': destino, 'nome': nome, 'token': token,
                  'tipo': tipo, 'assunto': 'link'})
    return True, None


def _fake_aviso(destino, nome, tipo='cliente'):
    CAIXA.append({'para': destino, 'nome': nome, 'assunto': 'aviso'})
    return True, None


appv2.enviar_link_recuperacao = _fake_link
appv2.enviar_aviso_senha_alterada = _fake_aviso


CLIENTE = {
    'cpf': '52998224725', 'nome': 'Joao Motorista', 'ocupacao': 'taxi',
    'tel': '11999999999', 'endereco': 'Rua dos Testes, 100 - Sao Paulo',
    'email': 'joao.motorista@teste.com', 'senha': 'senhaAntiga1',
    'placa': 'ABC1D23', 'registro_numero': '123456',
    'aceita_promocoes': True,
    'foto_comprovante': 'data:image/jpeg;base64,' + ('A' * 3000),
}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appv2.app.config['TESTING'] = True
        cls.c = appv2.app.test_client()
        cls.c.post('/api/auth/cadastro', json=CLIENTE)
        cls.c.post('/api/admin/setup', json={
            'usuario': 'edmundo', 'senha': 'senhaMaster1',
            'email': 'master@teste.com', 'nome': 'Edmundo'})
        r = cls.c.post('/api/admin/login',
                       json={'usuario': 'edmundo', 'senha': 'senhaMaster1'})
        cls.token_admin = r.get_json()['token']

    def setUp(self):
        CAIXA.clear()

    def cab(self):
        return {'X-Admin-Token': self.token_admin}

    def zerar_espera(self, tabela, campo, valor):
        """A trava de 1 minuto atrapalha o teste; limpa o carimbo de tempo."""
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute(f'UPDATE {tabela} SET reset_pedido_em = NULL WHERE {campo} = ?',
                    (valor,))
        conn.commit()
        conn.close()


class TestMotorista(Base):

    def test_01_pedido_envia_link(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        r = self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 1)
        self.assertEqual(CAIXA[0]['para'], CLIENTE['email'])
        self.assertEqual(CAIXA[0]['tipo'], 'cliente')

    def test_02_email_inexistente_responde_igual_e_nao_envia(self):
        """Não pode dar para descobrir quem é cliente testando e-mails."""
        r1 = self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        CAIXA.clear()
        r2 = self.c.post('/api/auth/esqueci-senha',
                         json={'email': 'ninguem@teste.com'})
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertEqual(r1.get_json()['mensagem'], r2.get_json()['mensagem'])
        self.assertEqual(len(CAIXA), 0)

    def test_03_email_invalido_recusado(self):
        r = self.c.post('/api/auth/esqueci-senha', json={'email': 'nao-e-email'})
        self.assertEqual(r.status_code, 400)

    def test_04_maiuscula_no_email_funciona(self):
        """O teclado do celular põe maiúscula sozinho na primeira letra."""
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        r = self.c.post('/api/auth/esqueci-senha',
                        json={'email': CLIENTE['email'].upper()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 1)

    def test_05_segundo_pedido_seguido_nao_reenvia(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        CAIXA.clear()
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        self.assertEqual(len(CAIXA), 0, 'a trava de 1 minuto deixou reenviar')

    def test_06_token_valido_e_troca_a_senha(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']

        v = self.c.get(f'/api/auth/validar-reset?token={token}&tipo=cliente')
        self.assertTrue(v.get_json()['valido'])

        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': 'novaSenha123'})
        self.assertEqual(r.status_code, 200)

        entrou = self.c.post('/api/auth/login', json={
            'email': CLIENTE['email'], 'senha': 'novaSenha123'})
        self.assertEqual(entrou.status_code, 200)

        velha = self.c.post('/api/auth/login', json={
            'email': CLIENTE['email'], 'senha': CLIENTE['senha']})
        self.assertEqual(velha.status_code, 401, 'a senha antiga continuou valendo')

    def test_07_token_so_serve_uma_vez(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']

        self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': 'primeira123'})
        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': 'segunda123'})
        self.assertEqual(r.status_code, 400)

        # E a senha que valeu foi a primeira, não a segunda.
        ok = self.c.post('/api/auth/login', json={
            'email': CLIENTE['email'], 'senha': 'primeira123'})
        self.assertEqual(ok.status_code, 200)

    def test_08_token_expirado_recusado(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("UPDATE clientes SET reset_expira = '2020-01-01 00:00:00' "
                    "WHERE LOWER(email) = LOWER(?)", (CLIENTE['email'],))
        conn.commit()
        conn.close()

        v = self.c.get(f'/api/auth/validar-reset?token={token}&tipo=cliente')
        self.assertFalse(v.get_json()['valido'])
        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': 'qualquer123'})
        self.assertEqual(r.status_code, 400)

    def test_09_token_inventado_recusado(self):
        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': 'token-que-eu-inventei', 'tipo': 'cliente',
            'senha_nova': 'qualquer123'})
        self.assertEqual(r.status_code, 400)

    def test_10_senha_curta_recusada(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']
        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': '123'})
        self.assertEqual(r.status_code, 400)

    def test_11_banco_nao_guarda_o_token_em_claro(self):
        """Se o banco vazar, o que estiver lá não pode dar acesso a ninguém."""
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']

        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute('SELECT reset_token_hash FROM clientes WHERE LOWER(email) = LOWER(?)',
                    (CLIENTE['email'],))
        guardado = cur.fetchone()['reset_token_hash']
        conn.close()

        self.assertNotEqual(guardado, token)
        self.assertEqual(len(guardado), 64, 'não parece um hash sha256')

    def test_12_aviso_de_senha_alterada_sai(self):
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token = CAIXA[0]['token']
        CAIXA.clear()
        self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'cliente', 'senha_nova': 'avisada123'})
        avisos = [m for m in CAIXA if m['assunto'] == 'aviso']
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0]['para'], CLIENTE['email'])


class TestEquipe(Base):

    def criar(self, usuario, email, senha='senhaBoa123', nivel='caixa'):
        return self.c.post('/api/admin/usuarios', headers=self.cab(), json={
            'usuario': usuario, 'senha': senha, 'nivel': nivel,
            'email': email, 'nome': usuario})

    def test_20_email_obrigatorio_para_usuario_novo(self):
        r = self.c.post('/api/admin/usuarios', headers=self.cab(), json={
            'usuario': 'sememail', 'senha': 'senhaBoa123', 'nivel': 'caixa'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('mail', r.get_json()['erro'])

    def test_21_email_invalido_recusado(self):
        r = self.criar('emailruim', 'isso-nao-e-email')
        self.assertEqual(r.status_code, 400)

    def test_22_email_repetido_recusado(self):
        self.criar('carlos', 'carlos@teste.com')
        r = self.criar('lourival', 'carlos@teste.com')
        self.assertEqual(r.status_code, 400)
        self.assertIn('carlos', r.get_json()['erro'])

    def test_23_pedido_da_equipe_envia_link(self):
        self.criar('leandro', 'leandro@teste.com')
        r = self.c.post('/api/admin/esqueci-senha', json={'identificador': 'leandro'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 1)
        self.assertEqual(CAIXA[0]['para'], 'leandro@teste.com')
        self.assertEqual(CAIXA[0]['tipo'], 'admin')

    def test_24_aceita_pelo_email_tambem(self):
        self.criar('marcia', 'marcia@teste.com')
        r = self.c.post('/api/admin/esqueci-senha',
                        json={'identificador': 'marcia@teste.com'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 1)

    def test_25_maiuscula_no_usuario_funciona(self):
        self.criar('pedro', 'pedro@teste.com')
        r = self.c.post('/api/admin/esqueci-senha', json={'identificador': 'PEDRO'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 1)

    def test_26_usuario_desativado_nao_recebe_link(self):
        self.criar('demitido', 'demitido@teste.com')
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("UPDATE admin SET ativo = 0 WHERE usuario = 'demitido'")
        conn.commit()
        conn.close()
        CAIXA.clear()
        r = self.c.post('/api/admin/esqueci-senha', json={'identificador': 'demitido'})
        self.assertEqual(r.status_code, 200)   # resposta genérica
        self.assertEqual(len(CAIXA), 0, 'mandou link para quem foi desativado')

    def test_27_usuario_inexistente_responde_igual(self):
        r = self.c.post('/api/admin/esqueci-senha', json={'identificador': 'fantasma'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(CAIXA), 0)

    def test_28_troca_senha_e_derruba_a_sessao_aberta(self):
        self.criar('sessao', 'sessao@teste.com', senha='senhaVelha1')
        login = self.c.post('/api/admin/login',
                            json={'usuario': 'sessao', 'senha': 'senhaVelha1'})
        token_sessao = login.get_json()['token']

        CAIXA.clear()
        self.c.post('/api/admin/esqueci-senha', json={'identificador': 'sessao'})
        token_reset = CAIXA[0]['token']

        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token_reset, 'tipo': 'admin', 'senha_nova': 'senhaNova123'})
        self.assertEqual(r.status_code, 200)

        # A sessão que estava aberta com a senha velha tem de cair.
        usa = self.c.get('/api/admin/produtos',
                         headers={'X-Admin-Token': token_sessao})
        self.assertIn(usa.status_code, (401, 403))

        entra = self.c.post('/api/admin/login',
                            json={'usuario': 'sessao', 'senha': 'senhaNova123'})
        self.assertEqual(entra.status_code, 200)

    def test_29_equipe_exige_8_caracteres(self):
        self.criar('oito', 'oito@teste.com')
        CAIXA.clear()
        self.c.post('/api/admin/esqueci-senha', json={'identificador': 'oito'})
        token = CAIXA[0]['token']
        curta = self.c.post('/api/auth/redefinir-senha', json={
            'token': token, 'tipo': 'admin', 'senha_nova': 'sete123'})
        self.assertEqual(curta.status_code, 400)

    def test_30_token_de_cliente_nao_serve_para_admin(self):
        """Trocar o 'tipo' na URL não pode dar acesso ao painel."""
        self.zerar_espera('clientes', 'email', CLIENTE['email'])
        self.c.post('/api/auth/esqueci-senha', json={'email': CLIENTE['email']})
        token_cliente = CAIXA[-1]['token']
        r = self.c.post('/api/auth/redefinir-senha', json={
            'token': token_cliente, 'tipo': 'admin', 'senha_nova': 'invasao123'})
        self.assertEqual(r.status_code, 400)

    def test_31_master_preenche_email_de_quem_nao_tinha(self):
        # Simula um usuário criado antes desta mudança.
        conn = appv2.get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO admin (usuario, senha_hash, poster_id, nivel, "
                    "nome, ativo) VALUES (?, ?, ?, ?, ?, ?)",
                    ('antigo', 'x', 'AMBOS', 'caixa', 'Antigo', 1))
        conn.commit()
        cur.execute("SELECT id FROM admin WHERE usuario = 'antigo'")
        uid = cur.fetchone()['id']
        conn.close()

        # Sem e-mail, não recebe link.
        CAIXA.clear()
        self.c.post('/api/admin/esqueci-senha', json={'identificador': 'antigo'})
        self.assertEqual(len(CAIXA), 0)

        # O Master preenche.
        r = self.c.post(f'/api/admin/usuarios/{uid}', headers=self.cab(),
                        json={'email': 'antigo@teste.com'})
        self.assertEqual(r.status_code, 200)

        # Agora recebe.
        CAIXA.clear()
        self.c.post('/api/admin/esqueci-senha', json={'identificador': 'antigo'})
        self.assertEqual(len(CAIXA), 1)
        self.assertEqual(CAIXA[0]['para'], 'antigo@teste.com')

    def test_32_lista_marca_quem_esta_sem_email(self):
        r = self.c.get('/api/admin/usuarios', headers=self.cab())
        d = r.get_json()
        self.assertIn('email_configurado', d)
        self.assertTrue(any('email' in u for u in d['usuarios']))

    def test_33_setup_do_primeiro_master_exige_email(self):
        """Verificado na rota: sem e-mail o primeiro Master não é criado."""
        r = self.c.post('/api/admin/setup',
                        json={'usuario': 'outro', 'senha': 'senhaBoa123'})
        # Já existe admin, então a resposta é 403 — mas a validação de e-mail
        # vem antes, então sem e-mail o erro é 400.
        self.assertEqual(r.status_code, 400)
        self.assertIn('mail', r.get_json()['erro'])


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        os.unlink(_tmp.name)
