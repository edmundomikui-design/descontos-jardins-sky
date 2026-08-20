"""
Envio de e-mail do CAJ SKY.

Usa a API do Resend por HTTP (nada de SMTP). Motivo: SMTP em servidor de
nuvem costuma ser bloqueado ou cair em spam, e a API é uma chamada só,
usando a biblioteca `requests` que o projeto já tem.

Configuração (variáveis de ambiente no Render):

    RESEND_API_KEY    obrigatória — a chave criada em resend.com
    EMAIL_REMETENTE   opcional  — padrão "CAJ SKY <contato@cajsky.com.br>"
    APP_URL           opcional  — padrão "https://www.cajsky.com.br"

Se a chave não estiver configurada, o envio falha de forma controlada e
devolve uma mensagem explicando o que falta — o servidor não quebra.
"""

import os
import requests

RESEND_URL = 'https://api.resend.com/emails'
TIMEOUT = 15


def _config():
    return {
        'chave': os.environ.get('RESEND_API_KEY', '').strip(),
        'remetente': os.environ.get(
            'EMAIL_REMETENTE', 'CAJ SKY <contato@cajsky.com.br>').strip(),
        'app_url': os.environ.get(
            'APP_URL', 'https://www.cajsky.com.br').strip().rstrip('/'),
    }


def email_configurado():
    """True se dá para enviar e-mail. Usado para avisar no painel."""
    return bool(_config()['chave'])


def app_url():
    return _config()['app_url']


def enviar_email(destino, assunto, html):
    """
    Envia um e-mail. Devolve (True, None) ou (False, 'motivo').

    Nunca levanta exceção: quem chama decide o que mostrar na tela. Uma
    falha de envio não pode derrubar a rota inteira.
    """
    cfg = _config()

    if not cfg['chave']:
        return False, ('Envio de e-mail não configurado no servidor '
                       '(falta a variável RESEND_API_KEY).')

    try:
        resposta = requests.post(
            RESEND_URL,
            headers={
                'Authorization': f"Bearer {cfg['chave']}",
                'Content-Type': 'application/json',
            },
            json={
                'from': cfg['remetente'],
                'to': [destino],
                'subject': assunto,
                'html': html,
            },
            timeout=TIMEOUT,
        )

        if resposta.status_code in (200, 201):
            return True, None

        # O Resend devolve o motivo em JSON. Vale registrar no log do Render:
        # domínio não verificado e chave inválida são os erros comuns no começo.
        try:
            detalhe = resposta.json().get('message') or resposta.text
        except Exception:
            detalhe = resposta.text
        print(f"⚠️ Falha ao enviar e-mail ({resposta.status_code}): {detalhe}")
        return False, f'O servidor de e-mail recusou o envio ({resposta.status_code}).'

    except requests.exceptions.Timeout:
        return False, 'O servidor de e-mail demorou demais para responder.'
    except Exception as e:
        print(f"⚠️ Erro ao enviar e-mail: {e}")
        return False, 'Não foi possível enviar o e-mail agora.'


# ==================== MODELO DA MENSAGEM ====================

def _moldura(titulo, corpo_html):
    """
    HTML simples de propósito. Cliente de e-mail não é navegador: nada de
    CSS externo, nada de flexbox, tudo inline e em tabela.
    """
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f4f5f7;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:520px;background:#ffffff;border-radius:10px;
                    border:1px solid #e3e5e8;overflow:hidden;">
        <tr>
          <td style="background:#1e3a8a;padding:20px 24px;">
            <div style="color:#ffffff;font-size:20px;font-weight:bold;">⛽ CAJ SKY</div>
            <div style="color:#c7d2fe;font-size:13px;margin-top:2px;">
              Combustível com desconto
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 24px;color:#1f2937;font-size:15px;line-height:1.6;">
            <h2 style="margin:0 0 14px;font-size:18px;color:#111827;">{titulo}</h2>
            {corpo_html}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 24px;background:#f9fafb;border-top:1px solid #e3e5e8;
                     color:#6b7280;font-size:12px;line-height:1.5;">
            Postos CAJ e SKY — R. Estados Unidos, 1930 e 1776, Jardins, São Paulo.<br>
            Esta mensagem foi enviada automaticamente. Não responda a este e-mail.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _botao(link, rotulo):
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0;">
      <tr><td style="background:#1e3a8a;border-radius:8px;">
        <a href="{link}" style="display:inline-block;padding:13px 26px;color:#ffffff;
           font-size:15px;font-weight:bold;text-decoration:none;">{rotulo}</a>
      </td></tr>
    </table>
    <p style="margin:0 0 6px;font-size:12px;color:#6b7280;">
      Se o botão não funcionar, copie e cole este endereço no navegador:
    </p>
    <p style="margin:0;font-size:12px;color:#1e3a8a;word-break:break-all;">{link}</p>
    """


def enviar_link_recuperacao(destino, nome, token, tipo='cliente', validade_minutos=60):
    """
    Manda o link de redefinição de senha.

    `tipo` separa o motorista ('cliente') da equipe do painel ('admin'):
    é o mesmo formulário na tela, mas cada um mexe numa tabela diferente.
    """
    link = f"{app_url()}/redefinir-senha.html?token={token}&tipo={tipo}"
    primeiro_nome = (nome or '').strip().split(' ')[0] or 'Olá'

    if tipo == 'admin':
        contexto = ('Recebemos um pedido para redefinir a senha do seu acesso ao '
                    '<strong>painel administrativo</strong> do CAJ SKY.')
        aviso_extra = ('<p style="margin:16px 0 0;padding:12px;background:#fef3c7;'
                       'border-radius:6px;font-size:13px;color:#78350f;">'
                       '<strong>Atenção:</strong> este acesso mexe em preços e '
                       'descontos. Se não foi você que pediu, avise o responsável '
                       'imediatamente e não repasse este link a ninguém.</p>')
    else:
        contexto = ('Recebemos um pedido para redefinir a senha do seu cadastro '
                    'no CAJ SKY.')
        aviso_extra = ''

    corpo = f"""
    <p style="margin:0 0 12px;">{primeiro_nome},</p>
    <p style="margin:0 0 4px;">{contexto}</p>
    {_botao(link, 'Criar nova senha')}
    <p style="margin:18px 0 0;font-size:13px;color:#6b7280;">
      O link vale por <strong>{validade_minutos} minutos</strong> e só pode ser
      usado uma vez. Se você não pediu, pode ignorar esta mensagem — sua senha
      atual continua valendo e ninguém consegue entrar sem ela.
    </p>
    {aviso_extra}
    """
    return enviar_email(destino, 'CAJ SKY — redefinir sua senha',
                        _moldura('Redefinir sua senha', corpo))


def enviar_aviso_senha_alterada(destino, nome, tipo='cliente'):
    """
    Avisa que a senha mudou. É a rede de segurança: se alguém trocou a senha
    sem ser o dono, ele descobre pelo e-mail em vez de descobrir trancado.
    """
    primeiro_nome = (nome or '').strip().split(' ')[0] or 'Olá'
    onde = 'do painel administrativo' if tipo == 'admin' else 'do seu cadastro'

    corpo = f"""
    <p style="margin:0 0 12px;">{primeiro_nome},</p>
    <p style="margin:0 0 12px;">
      A senha {onde} no CAJ SKY acabou de ser alterada. Se foi você, está tudo
      certo e não precisa fazer nada.
    </p>
    <p style="margin:0;padding:12px;background:#fee2e2;border-radius:6px;
              font-size:13px;color:#7f1d1d;">
      <strong>Se não foi você</strong>, peça uma nova redefinição agora mesmo em
      <a href="{app_url()}" style="color:#7f1d1d;">{app_url().replace('https://', '')}</a>
      e avise a gerência do posto.
    </p>
    """
    return enviar_email(destino, 'CAJ SKY — sua senha foi alterada',
                        _moldura('Sua senha foi alterada', corpo))
