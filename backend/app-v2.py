from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import uuid
import qrcode
from io import BytesIO
import base64
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import re
import hashlib
import secrets

from database import init_db, get_db

# ==================== FUSO HORÁRIO ====================
#
# O servidor do Render roda em UTC — três horas à frente de Brasília. Sem
# corrigir isso, quatro coisas saem erradas:
#
#   - a hora impressa no comprovante do motorista
#   - o turno do abastecimento (12h daqui vira 15h, cai no turno errado)
#   - o "dia" do cupom, que passaria a virar às 21h em vez da meia-noite —
#     ou seja, quem gerasse cupom às 21h30 de segunda estaria gastando o de
#     terça, e o limite de um por dia seguiria esse dia deslocado
#   - a data nos relatórios de fechamento de turno
#
# A correção fica no CÓDIGO, não numa variável de ambiente do Render. Se
# dependesse de configuração, bastaria alguém recriar o serviço sem ela para
# tudo voltar a errar em silêncio.

try:
    from zoneinfo import ZoneInfo
    FUSO_BRASILIA = ZoneInfo('America/Sao_Paulo')
except Exception:
    # Se a base de fusos não estiver instalada no servidor, cai no
    # deslocamento fixo. O Brasil não usa horário de verão desde 2019, então
    # UTC-3 vale o ano inteiro — mas o certo continua sendo o ZoneInfo, que
    # se ajusta sozinho caso o horário de verão volte.
    from datetime import timezone
    FUSO_BRASILIA = timezone(timedelta(hours=-3))


def agora():
    """
    Data e hora de Brasília, SEM o fuso embutido.

    Devolver "ingênuo" (sem fuso) é de propósito: o banco guarda datas como
    texto simples e o resto do código compara e subtrai esses valores. Um
    horário com fuso embutido quebraria essas contas — subtrair um horário
    com fuso de outro sem fuso é erro em Python.
    """
    return datetime.now(FUSO_BRASILIA).replace(tzinfo=None)


from email_service import (
    enviar_link_recuperacao,
    enviar_aviso_senha_alterada,
    email_configurado,
)

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Admin-Token"]
    }
})

# Em produção a chave vem de variável de ambiente
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or str(uuid.uuid4())

# Inicializa banco de dados
init_db()

# ==================== UTILIDADES ====================

def validar_cpf(cpf):
    """Valida CPF básico"""
    cpf = re.sub(r'\D', '', cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False
    return True

# ==================== IDENTIFICAÇÃO DA CATEGORIA ====================
# O desconto é para taxista e motorista de aplicativo. Sem nada que ligue a
# pessoa à categoria, qualquer um se declara motorista e leva o desconto.
#
# Duas travas diferentes, porque as duas categorias são diferentes:
#
#  - COMPROVANTE (foto, no cadastro): taxista manda a licença; motorista de
#    aplicativo manda o print do perfil dele no app de motorista. Não é prova
#    inviolável — é atrito e rastro com nome em cima.
#
#  - PLACA (editável, conferida na bomba): no táxi ela é estável, porque
#    acompanha a permissão quando o taxista troca de carro. No aplicativo o
#    carro muda, então a placa vale para o dia e o motorista atualiza quando
#    trocar. É a única conferência que não depende de sistema nenhum: ou bate
#    com o carro na frente do frentista, ou não bate.

# Formato antigo (ABC1234) e Mercosul (ABC1D23)
_PLACA_ANTIGA = re.compile(r'^[A-Z]{3}[0-9]{4}$')
_PLACA_MERCOSUL = re.compile(r'^[A-Z]{3}[0-9][A-Z][0-9]{2}$')

# O que cada ocupação precisa comprovar
# Cupom vale para UM abastecimento só. Se o motorista pôs 20 L num cupom de
# 50 L, o cupom fecha e os 30 L restantes não valem mais — ele deve aproveitar
# o limite de uma vez.
#
# Regra de negócio, não limitação técnica: evita o mesmo cliente voltando
# várias vezes no dia por pouco volume. Trocar para False devolve o uso em
# partes, sem mexer em mais nada.
USO_UNICO = True

# ==================== LIMITE DE CUPONS POR CATEGORIA ====================
#
# Antes desta regra a trava era POR PRODUTO por dia — o que na prática não
# limitava nada: o mesmo cliente gerava cinco cupons de combustível no mesmo
# dia (comum, aditivada, premium, etanol, diesel) e mais quatro de óleo.
#
# Agora o limite é POR CATEGORIA:
#
#   combustível → 1 cupom por dia, qualquer que seja o combustível
#   óleo        → 1 cupom a cada 7 dias, qualquer que seja o óleo
#
# O intervalo do óleo conta 7 dias corridos desde o último cupom, e não a
# semana do calendário. É mais justo (quem pegou no sábado não ganha outro na
# segunda) e mais fácil de explicar na pista: "sete dias depois do último".
#
# Duas saídas para o cliente que esbarra na regra:
#
#   - Se o cupom do dia ainda NÃO foi usado, ele pode trocar de produto. O
#     antigo é cancelado e sai o novo. Sem isso, um toque errado no celular
#     custaria o desconto do dia inteiro e cairia no colo do frentista.
#   - Se já foi usado, só com liberação extra do Master (uso único, com
#     motivo, registrada na auditoria).

INTERVALO_DIAS = {
    'combustivel': 1,   # um por dia
    'oleo': 7,          # um por semana
}

# Nome que aparece nas mensagens para o cliente
NOME_CATEGORIA = {
    'combustivel': 'combustível',
    'oleo': 'óleo',
}

# Frentista é um cliente diferenciado (decisão de 23/08): tem direito a
# combustível uma vez a cada 7 dias corridos — mesmo mecanismo já usado no
# óleo dos clientes comuns — e NENHUM direito a óleo. A trava existe para
# não dar a ele a mesma liberdade de um cliente comum (1 combustível por
# dia), que abriria brecha para gerar cupom e repassar pra fora da pista.
# Conta de frentista só é criada pelo Master (ver /api/admin/frentistas) —
# nunca pelo cadastro público — e o CPF já sendo único no banco impede a
# mesma pessoa de ter, ao mesmo tempo, uma conta de frentista e outra
# "comum" para escapar do limite.
INTERVALO_FRENTISTA_COMBUSTIVEL_DIAS = 7

# Quantos dias uma liberação do Master vale antes de expirar sozinha.
# Sem prazo, uma liberação esquecida ficaria valendo para sempre.
VALIDADE_LIBERACAO_DIAS = 7


def categoria_do_produto(tipo):
    """
    Traduz o tipo do produto para a categoria do limite.

    Qualquer coisa que não seja óleo entra como combustível — assim, se um
    produto novo for cadastrado com um tipo que ninguém previu, ele cai na
    regra mais restritiva em vez de ficar sem limite nenhum.
    """
    return 'oleo' if (tipo or '').strip().lower() == 'oleo' else 'combustivel'


PERFIL_OCUPACAO = {
    'táxi':       {'registro': 'condutax', 'comprovante': 'licenca_taxi'},
    'taxi':       {'registro': 'condutax', 'comprovante': 'licenca_taxi'},
    'uber':       {'registro': 'conduapp', 'comprovante': 'perfil_app'},
    'aplicativo': {'registro': 'conduapp', 'comprovante': 'perfil_app'},
}

DESCRICAO_COMPROVANTE = {
    'licenca_taxi': 'a foto da sua licença de taxista (alvará ou CONDUTAX)',
    'perfil_app': 'o print da tela de cadastro do seu aplicativo de motorista',
    'convenio': 'o comprovante de vínculo com a empresa conveniada',
}

# ~1,4 MB de base64 ≈ 1 MB de imagem. O app já reduz a foto no celular;
# o limite existe para ninguém entupir o banco mandando direto na API.
LIMITE_FOTO_BASE64 = 1_400_000


def normalizar_cnpj(cnpj):
    """Devolve o CNPJ só com números, ou None."""
    if not cnpj:
        return None
    limpo = re.sub(r'\D', '', str(cnpj))
    return limpo or None


def validar_cnpj(cnpj):
    """
    Confere os dois dígitos verificadores do CNPJ.

    Isso pega erro de digitação, não fraude: qualquer um acha o CNPJ real de
    qualquer empresa em segundos. A trava de verdade é a empresa precisar
    estar cadastrada aqui pela gerência (convênio assinado).
    """
    n = normalizar_cnpj(cnpj)
    if not n or len(n) != 14 or n == n[0] * 14:
        return False

    def digito(base, pesos):
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return '0' if resto < 2 else str(11 - resto)

    d1 = digito(n[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = digito(n[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return n[12:] == d1 + d2


def exige_alcada_master(email, dominio_empresa):
    """
    Diz se este cadastro só pode ser liberado pelo Master.

    Vale quando a empresa exige e-mail corporativo e a pessoa se cadastrou com
    outro (Gmail, por exemplo). Não é bloqueio: é exceção, e exceção sobe de
    nível. Calculado na hora, comparando o e-mail com o domínio atual da
    empresa — se o convênio mudar de domínio depois, a regra acompanha.
    """
    dominio = (dominio_empresa or '').strip().lower().lstrip('@')
    if not dominio:
        return False
    return not (email or '').strip().lower().endswith('@' + dominio)


def formatar_cnpj(cnpj):
    """00.000.000/0000-00 para exibição."""
    n = normalizar_cnpj(cnpj)
    if not n or len(n) != 14:
        return cnpj
    return f'{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}'


def normalizar_placa(placa):
    """Devolve a placa só com letras e números, em maiúsculas, ou None."""
    if not placa:
        return None
    limpa = re.sub(r'[^A-Za-z0-9]', '', str(placa)).upper()
    return limpa or None


def validar_placa(placa):
    """Aceita o formato antigo e o Mercosul. Devolve erro em texto ou None."""
    if not placa:
        return 'Informe a placa do carro que você está usando.'
    if len(placa) != 7:
        return 'A placa deve ter 7 caracteres (exemplos: ABC1D23 ou ABC1234).'
    if not (_PLACA_ANTIGA.match(placa) or _PLACA_MERCOSUL.match(placa)):
        return 'Placa em formato inválido. Use ABC1D23 (Mercosul) ou ABC1234 (antiga).'
    return None


def validar_foto(foto, tipo_comprovante):
    """Confere que veio uma imagem plausível, sem tentar adivinhar o conteúdo."""
    descricao = DESCRICAO_COMPROVANTE.get(tipo_comprovante, 'o comprovante')
    if not foto:
        return f'Envie {descricao}.'
    if not str(foto).startswith('data:image/'):
        return 'Arquivo inválido. Envie uma imagem.'
    if len(foto) > LIMITE_FOTO_BASE64:
        return 'A imagem ficou grande demais. Tente de novo pelo aplicativo.'
    if len(foto) < 2000:
        return 'A imagem não foi enviada por completo. Tente de novo.'
    return None


def _cpf_mascarado(cpf):
    """Mostra só o miolo do CPF (***.123.456-**) para o frentista conferir
    a identidade sem expor o documento inteiro na pista."""
    if not cpf:
        return ''
    d = re.sub(r'\D', '', cpf)
    if len(d) != 11:
        return cpf
    return f'***.{d[3:6]}.{d[6:9]}-**'


def validar_email(email):
    """Valida email básico"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def imagem_qrcode(qr_data):
    """Gera a imagem (base64) de um código já existente"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return base64.b64encode(img_bytes.getvalue()).decode()

def gerar_qrcode():
    """Gera QR code único"""
    qr_data = str(uuid.uuid4())[:12]
    return qr_data, imagem_qrcode(qr_data)

def obter_turno(hora=None):
    """Retorna o turno baseado na hora"""
    if hora is None:
        hora = agora().hour

    if 6 <= hora < 14:
        return "Turno 1 (6h-14h)"
    elif 14 <= hora < 22:
        return "Turno 2 (14h-22h)"
    else:
        return "Turno 3 (22h-6h)"

# ==================== AUTENTICAÇÃO DE ADMIN ====================

def admin_do_token():
    """Retorna o admin dono do token enviado no header, ou None."""
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    if not token:
        return None

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, usuario, nome, nivel, poster_id, token_expira, ativo
        FROM admin WHERE token = ?
    ''', (token,))
    admin = cursor.fetchone()
    conn.close()

    if not admin or admin['ativo'] == 0:
        return None

    if admin['token_expira'] and admin['token_expira'] < agora().strftime('%Y-%m-%d %H:%M:%S'):
        return None

    return admin


def exige_admin(f):
    """Qualquer usuário logado (Master ou Caixa)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def exige_master(f):
    """Somente o nível Master — usuários e configurações sensíveis."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        if admin['nivel'] != 'master':
            return jsonify({
                'erro': 'Permissão negada. Somente o administrador Master pode fazer isso.'
            }), 403
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def exige_gerencia(f):
    """Master ou Gerência — alterar preços e descontos (com trava de margem)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = admin_do_token()
        if not admin:
            return jsonify({'erro': 'Acesso restrito. Faça login.'}), 401
        if admin['nivel'] not in ('master', 'gerencia'):
            return jsonify({
                'erro': 'Permissão negada. Seu acesso é de consulta (Caixa) e não permite alterações.'
            }), 403
        request.admin = admin
        return f(*args, **kwargs)
    return wrapper


def registrar_auditoria(cursor, admin, acao, produto_id=None, produto_nome=None,
                        campo=None, valor_anterior=None, valor_novo=None, detalhe=None):
    """Grava quem mudou o quê, quando e de qual valor para qual."""
    cursor.execute('''
        INSERT INTO auditoria
        (data_hora, admin_id, admin_usuario, admin_nivel, acao,
         produto_id, produto_nome, campo, valor_anterior, valor_novo, detalhe)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        agora().strftime('%Y-%m-%d %H:%M:%S'),
        admin['id'] if admin else None,
        admin['usuario'] if admin else None,
        admin['nivel'] if admin else None,
        acao, produto_id, produto_nome, campo,
        None if valor_anterior is None else str(valor_anterior),
        None if valor_novo is None else str(valor_novo),
        detalhe
    ))


def ler_config_indicacoes(cursor):
    """Linha única de configuração do programa de indicação (ou None)."""
    cursor.execute('''
        SELECT meta_indicacoes, valor_recompensa, ativo,
               minimo_litros_combustivel, minimo_litros_oleo,
               data_fim_campanha, validade_premio_dias, validade_indicacao_dias
        FROM config_indicacoes LIMIT 1
    ''')
    return cursor.fetchone()


def campanha_indicacao_encerrada(config):
    """A campanha tem data de fim e ela já passou? (vazio = sem prazo)"""
    if not config:
        return False
    fim = (config['data_fim_campanha'] or '').strip()[:10]
    return bool(fim) and agora().strftime('%Y-%m-%d') > fim


def expirar_premios_vencidos(cursor, cliente_id=None):
    """
    Marca como 'expirado' os prêmios disponíveis que passaram da validade.

    Feito de forma preguiçosa — na hora em que alguém olha os prêmios de um
    cliente — para não depender de nenhuma rotina agendada, que este projeto
    não tem. Prêmio já aplicado a um cupom não é tocado aqui.
    """
    hoje = agora().strftime('%Y-%m-%d')
    sql = ("UPDATE recompensas_indicacao SET status = 'expirado' "
           "WHERE status = 'disponivel' AND validade IS NOT NULL AND validade < ?")
    params = [hoje]
    if cliente_id is not None:
        sql += ' AND cliente_id = ?'
        params.append(cliente_id)
    cursor.execute(sql, params)


def minimo_litros_da_categoria(config, categoria):
    """Quantos litros um abastecimento precisa ter para positivar a indicação."""
    if not config:
        return 20
    if (categoria or 'combustivel') == 'oleo':
        return config['minimo_litros_oleo'] if config['minimo_litros_oleo'] is not None else 1
    return (config['minimo_litros_combustivel']
            if config['minimo_litros_combustivel'] is not None else 20)


def _processar_indicacao_positiva(cursor, indicado_id, indicador_id,
                                  quantidade, categoria):
    """
    Chamada quando um cliente indicado dá baixa num cupom. Só conta ponto
    para quem indicou se o abastecimento atingir o mínimo de litros da
    categoria — decisão do Edmundo (23/08): indicação só vale se virar
    cliente de verdade, não se o amigo puser 1 litro de combustível só para
    fechar a conta.

    Se não atingir, NADA é marcado: a indicação continua aguardando e será
    contada no primeiro abastecimento que atingir o mínimo, mesmo semanas
    depois. Ninguém perde a indicação por causa de um abastecimento pequeno.

    Cada indicado vale UM ponto na vida inteira: assim que é contado,
    `indicacao_positiva_contada` fica em 1 e ele nunca mais gera positivação
    nenhuma, por mais que abasteça. Quem já rendeu o brinde está encerrado.

    Quando conta, a cada N indicações positivas (configurável pelo Master)
    nasce um cupom-prêmio. N sempre bate — se a meta é 3, o prêmio nasce nas
    indicações 3, 6, 9… — porque a contagem acontece aqui, uma vez só para
    cada indicado, nunca duas.
    """
    config = ler_config_indicacoes(cursor)

    # Programa desligado: não queima a indicação. Ela fica esperando o
    # programa voltar, em vez de sumir sem nunca ter valido nada.
    if not config or not config['ativo']:
        return

    # Campanha com data de fim já passada: para de gerar ponto e prêmio novo.
    # Também não marca nada — se o Edmundo prorrogar o prazo, tudo volta a
    # valer de onde parou.
    if campanha_indicacao_encerrada(config):
        return

    minimo = minimo_litros_da_categoria(config, categoria)
    if (quantidade or 0) < minimo:
        return

    # Prazo da indicação pendente: quem se cadastrou pelo link tem um número
    # de dias para abastecer e virar ponto. Passou disso, não conta mais —
    # mas também não marca, para o caso de o prazo ser aumentado depois.
    dias_ind = config['validade_indicacao_dias']
    if dias_ind and dias_ind > 0:
        cursor.execute('SELECT data_criacao FROM clientes WHERE id = ?', (indicado_id,))
        linha_cad = cursor.fetchone()
        cadastro = str((linha_cad['data_criacao'] if linha_cad else '') or '')[:10]
        if cadastro:
            try:
                limite = (datetime.strptime(cadastro, '%Y-%m-%d')
                          + timedelta(days=int(dias_ind))).strftime('%Y-%m-%d')
                if agora().strftime('%Y-%m-%d') > limite:
                    return
            except ValueError:
                pass   # data em formato inesperado: não bloqueia o ponto

    cursor.execute(
        'UPDATE clientes SET indicacao_positiva_contada = 1 WHERE id = ?',
        (indicado_id,)
    )

    cursor.execute('''
        SELECT COUNT(*) AS n FROM clientes
        WHERE indicado_por_id = ? AND indicacao_positiva_contada = 1
    ''', (indicador_id,))
    total = (cursor.fetchone()['n'] or 0)

    meta = config['meta_indicacoes'] or 3
    if meta <= 0 or total % meta != 0:
        return

    # Validade do prêmio: congelada aqui, junto com o valor. Mudar a
    # configuração depois não encurta nem alonga prêmios já concedidos.
    dias_premio = config['validade_premio_dias']
    validade = None
    if dias_premio and dias_premio > 0:
        validade = (agora() + timedelta(days=int(dias_premio))).strftime('%Y-%m-%d')

    cursor.execute('''
        INSERT INTO recompensas_indicacao
        (cliente_id, valor, indicacoes_completas, status, data_concessao, validade)
        VALUES (?, ?, ?, 'disponivel', ?, ?)
    ''', (
        indicador_id,
        config['valor_recompensa'],
        total,
        agora().strftime('%Y-%m-%d %H:%M:%S'),
        validade
    ))

    registrar_auditoria(
        cursor, None, 'recompensa_indicacao_gerada',
        detalhe=(f'Cliente #{indicador_id} completou {total} indicações e ganhou '
                 f'um cupom-prêmio de R$ {config["valor_recompensa"]:.2f}'
                 + (f' (válido até {validade})' if validade else ''))
    )


def desconto_por_unidade(preco, valor, tipo):
    """Converte o desconto informado em reais por unidade."""
    return preco * (valor / 100) if tipo == 'percentual' else valor


# Acima desta variação, o sistema pede confirmação antes de gravar preço de
# custo ou de venda. 10% é folgado para o mercado de combustível e apertado o
# bastante para pegar erro de digitação.
VARIACAO_QUE_PEDE_CONFIRMACAO = 10.0


def _conferir_variacao_precos(cursor, produtos):
    """
    Devolve a lista de mudanças de preço que fogem do normal e ainda não foram
    confirmadas pelo Master. Lista vazia = pode gravar.

    Só compara quando já existe valor anterior: o primeiro cadastro de um custo
    não tem com o que ser comparado e passa direto.
    """
    pendentes = []

    for p in produtos:
        if not p.get('id') or p.get('confirma_variacao'):
            continue

        cursor.execute('SELECT nome, preco_atual, preco_custo FROM produtos WHERE id = ?',
                       (p['id'],))
        atual = cursor.fetchone()
        if not atual:
            continue

        for campo, rotulo in (('preco_custo', 'preço de custo'),
                              ('preco_atual', 'preço de venda')):
            if campo not in p:
                continue

            try:
                novo = round(float(p[campo]), 2)
            except (TypeError, ValueError):
                continue

            anterior = round(atual[campo] or 0, 2)
            if anterior <= 0 or novo == anterior:
                continue

            variacao = (novo - anterior) / anterior * 100
            if abs(variacao) > VARIACAO_QUE_PEDE_CONFIRMACAO:
                pendentes.append({
                    'produto_id': p['id'],
                    'produto': atual['nome'],
                    'campo': rotulo,
                    'de': anterior,
                    'para': novo,
                    'variacao_pct': round(variacao, 1),
                    'sentido': 'aumento' if variacao > 0 else 'queda'
                })

    return pendentes


def validar_margem(nivel, nome, preco, custo, margem_minima, desc_unidade):
    """Trava de margem. Devolve mensagem de erro ou None se estiver liberado.

    - Ninguém pode deixar o preço final abaixo do custo.
    - Gerência ainda precisa respeitar a margem mínima do produto.
    """
    # Trabalha em centavos arredondados: sem isso, 6.09 - 0.37 vira 5.719999...
    # e um preço que bate exatamente no piso seria recusado por engano.
    preco_final = round(preco - desc_unidade, 2)
    custo = round(custo or 0, 2)

    if preco_final < 0:
        return f'{nome}: o desconto deixa o preço negativo.'

    if custo <= 0:
        if nivel != 'master':
            return (f'{nome}: o preço de custo não está cadastrado. '
                    f'Peça ao administrador Master para informá-lo antes de dar desconto.')
        return None  # Master pode operar produto sem custo cadastrado

    if preco_final < custo:
        return (f'{nome}: o preço final R$ {preco_final:.2f} fica ABAIXO do custo '
                f'R$ {custo:.2f}. Prejuízo por unidade de R$ {custo - preco_final:.2f}.')

    if nivel != 'master':
        piso = round(custo * (1 + (margem_minima or 0) / 100), 2)
        if preco_final < piso:
            return (f'{nome}: o preço final R$ {preco_final:.2f} fica abaixo do mínimo '
                    f'permitido para o seu nível (R$ {piso:.2f} = custo + {margem_minima:.0f}%). '
                    f'Fale com o administrador Master.')

    return None


# ==================== ROTAS DE AUTENTICAÇÃO ====================

@app.route('/api/auth/cadastro', methods=['POST'])
def cadastro():
    """Cadastra novo cliente"""
    try:
        data = request.get_json()

        if not validar_cpf(data.get('cpf')):
            return jsonify({'erro': 'CPF inválido'}), 400

        if not validar_email(data.get('email')):
            return jsonify({'erro': 'Email inválido'}), 400

        if len(data.get('endereco', '')) < 10:
            return jsonify({'erro': 'Endereço incompleto'}), 400

        # Dois aceites, guardados com a data (prova exigida pela LGPD):
        #  - avisos do aplicativo (cupons e preços do dia): OBRIGATÓRIO, é a função do app
        #  - promoções de parceiros: OPCIONAL, marketing puro
        def marcado(v):
            return v in (True, 1, '1', 'true', 'on')

        aceita_promocoes = 1 if marcado(data.get('aceita_promocoes')) else 0
        if not aceita_promocoes:
            return jsonify({
                'erro': 'É preciso aceitar os avisos do aplicativo (cupons e preços do dia) para se cadastrar'
            }), 400

        aceita_parceiros = 1 if marcado(data.get('aceita_parceiros')) else 0
        agora_iso = agora().isoformat()
        data_consentimento = agora_iso
        data_consentimento_parceiros = agora_iso if aceita_parceiros else None

        # ---- comprovação da categoria e carro em uso ----
        ocupacao = (data.get('ocupacao') or '').strip()
        perfil = PERFIL_OCUPACAO.get(ocupacao.lower())

        registro_tipo = perfil['registro'] if perfil else 'convenio'
        tipo_comprovante = perfil['comprovante'] if perfil else 'convenio'
        registro_numero = re.sub(r'[^A-Za-z0-9]', '', str(data.get('registro_numero') or '')).upper()
        empresa_convenio = (data.get('empresa_convenio') or '').strip() or None
        empresa_convenio_id = data.get('empresa_convenio_id')

        # A placa é a conferência da bomba, então vale para todo mundo.
        # Quem troca de carro atualiza depois, em dois toques.
        placa = normalizar_placa(data.get('placa'))
        erro_placa = validar_placa(placa)
        if erro_placa:
            return jsonify({'erro': erro_placa}), 400

        # A foto é a prova da categoria: licença de taxista ou print do perfil
        # no app de motorista. O número do registro fica opcional.
        foto_comprovante = data.get('foto_comprovante')
        erro_foto = validar_foto(foto_comprovante, tipo_comprovante)
        if erro_foto:
            return jsonify({'erro': erro_foto}), 400

        cpf = re.sub(r'\D', '', data.get('cpf'))

        conn = get_db()
        cursor = conn.cursor()

        # ---- convênio de empresa: só da lista, e sempre com análise ----
        #
        # Táxi e motorista de aplicativo continuam liberados na hora. Convênio
        # não: a empresa precisa ter contrato assinado (cadastrada pela
        # gerência) e o cadastro fica pendente até alguém de alçada aprovar.
        status_inicial = 'ativo'
        empresa = None
        sem_email_corporativo = False

        if registro_tipo == 'convenio':
            if not empresa_convenio_id:
                conn.close()
                return jsonify({
                    'erro': 'Escolha a empresa do convênio na lista. '
                            'Se a sua empresa não aparece, ela ainda não tem convênio '
                            'com os postos CAJ e SKY — fale com o RH dela.'
                }), 400

            cursor.execute(
                'SELECT id, nome, cnpj, dominio_email, limite_funcionarios, ativo '
                'FROM empresas_convenio WHERE id = ?',
                (empresa_convenio_id,)
            )
            empresa = cursor.fetchone()

            if not empresa or not empresa['ativo']:
                conn.close()
                return jsonify({
                    'erro': 'Essa empresa não tem convênio ativo com os postos CAJ e SKY.'
                }), 400

            # O domínio corporativo é a prova mais barata de vínculo, mas
            # barrar quem não tem seria injusto: muito funcionário de chão de
            # fábrica só tem Gmail. Então não bloqueia — deixa cadastrar,
            # marca o caso e joga a decisão para o Master (gerência não
            # resolve exceção).
            dominio = (empresa['dominio_email'] or '').strip().lower().lstrip('@')
            email_informado = (data.get('email') or '').strip().lower()
            sem_email_corporativo = bool(dominio) and not email_informado.endswith('@' + dominio)

            # Teto de funcionários combinado com o RH: mesmo que alguém burle
            # tudo, o estrago para no número contratado.
            limite = empresa['limite_funcionarios'] or 0
            if limite > 0:
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM clientes "
                    "WHERE empresa_convenio_id = ? AND status <> 'recusado'",
                    (empresa['id'],)
                )
                linha_lim = cursor.fetchone()
                if (linha_lim['n'] if linha_lim else 0) >= limite:
                    conn.close()
                    return jsonify({
                        'erro': f'O convênio da {empresa["nome"]} já atingiu o limite de '
                                f'{limite} funcionários. Fale com o RH da empresa.'
                    }), 400

            empresa_convenio = empresa['nome']
            empresa_convenio_id = empresa['id']
            status_inicial = 'pendente'
        else:
            # Táxi/Uber não carregam vínculo de empresa.
            empresa_convenio_id = None

        cursor.execute('SELECT id FROM clientes WHERE cpf = ?', (cpf,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'erro': 'CPF já cadastrado'}), 400

        # ---- programa de indicação: "quem te trouxe?" ----
        #
        # O código vem do link que o indicador compartilhou (?ref=CJ123). Não
        # bloqueia o cadastro se o código não existir ou estiver errado — só
        # não liga a indicação a ninguém, silenciosamente. Link velho, link
        # digitado errado ou promoção que já não vale mais não pode impedir
        # a pessoa de virar cliente.
        #
        # Trava simples contra autoindicação: o CPF já é único (então a
        # mesma pessoa não abre duas contas), mas nada impede alguém de
        # cadastrar a esposa/o sócio com o próprio telefone só para acumular
        # prêmio. Se o telefone do novo cadastro bater com o do indicador,
        # a indicação não é contabilizada — o cadastro segue normal.
        indicado_por_id = None
        codigo_usado = re.sub(r'[^A-Za-z0-9]', '', str(data.get('indicado_por_codigo') or '')).upper()
        if codigo_usado:
            cursor.execute(
                'SELECT id, tel FROM clientes WHERE UPPER(codigo_indicacao) = ?',
                (codigo_usado,)
            )
            indicador = cursor.fetchone()
            tel_novo = re.sub(r'\D', '', str(data.get('tel') or ''))
            tel_indicador = re.sub(r'\D', '', str(indicador['tel'] or '')) if indicador else ''
            if indicador and (not tel_novo or tel_novo != tel_indicador):
                indicado_por_id = indicador['id']

        cursor.execute('SELECT id FROM clientes WHERE LOWER(email) = LOWER(?)',
                       ((data.get('email') or '').strip(),))
        if cursor.fetchone():
            conn.close()
            return jsonify({'erro': 'Email já cadastrado'}), 400

        # Placa repetida não bloqueia — dois motoristas podem dividir o mesmo
        # táxi por turno. Mas fica registrado e aparece no painel de suspeitas
        # e na tela do frentista.
        cursor.execute('SELECT COUNT(*) AS n FROM clientes WHERE placa = ?', (placa,))
        linha = cursor.fetchone()
        placa_repetida = (linha['n'] if linha else 0) > 0

        senha_hash = generate_password_hash(data.get('senha'))

        cursor.execute('''
            INSERT INTO clientes
            (cpf, nome, ocupacao, tel, endereco, email, senha_hash, desconto_tipo, desconto_valor,
             aceita_promocoes, data_consentimento, aceita_parceiros, data_consentimento_parceiros,
             placa, data_placa, registro_tipo, registro_numero, empresa_convenio,
             foto_comprovante, foto_comprovante_tipo, data_foto_comprovante,
             empresa_convenio_id, status, indicado_por_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cpf,
            data.get('nome'),
            ocupacao,
            data.get('tel'),
            data.get('endereco'),
            data.get('email'),
            senha_hash,
            'fixo',  # Tipo: valor fixo em reais
            1.00,    # Desconto padrão: R$ 1,00 por litro
            aceita_promocoes,
            data_consentimento,
            aceita_parceiros,
            data_consentimento_parceiros,
            placa,
            agora_iso,
            registro_tipo,
            registro_numero or None,
            empresa_convenio,
            foto_comprovante,
            tipo_comprovante,
            agora_iso,
            empresa_convenio_id,
            status_inicial,
            indicado_por_id
        ))

        cliente_id = cursor.lastrowid

        # O código de indicação deste cliente novo é gerado a partir do
        # próprio id — "CJ" + id. Simples, sem chance de colidir com outro
        # cliente (o id já é único) e sem precisar checar nada no banco.
        meu_codigo_indicacao = f'CJ{cliente_id}'
        cursor.execute('UPDATE clientes SET codigo_indicacao = ? WHERE id = ?',
                       (meu_codigo_indicacao, cliente_id))

        conn.commit()
        conn.close()

        if status_inicial == 'pendente' and sem_email_corporativo:
            mensagem = (f'Cadastro enviado para análise. Como você não usou o e-mail da '
                        f'{empresa_convenio}, a liberação precisa passar pelo responsável '
                        f'dos postos — pode demorar um pouco mais.')
        elif status_inicial == 'pendente':
            mensagem = (f'Cadastro enviado para análise. Assim que a gerência confirmar '
                        f'seu vínculo com a {empresa_convenio}, você poderá gerar cupons. '
                        f'Você receberá um aviso.')
        else:
            mensagem = 'Cadastro realizado! Confirme seu email.'

        return jsonify({
            'mensagem': mensagem,
            'cliente_id': cliente_id,
            'placa': placa,
            'placa_ja_cadastrada': placa_repetida,
            'status': status_inicial,
            'aguardando_aprovacao': status_inicial == 'pendente',
            'codigo_indicacao': meu_codigo_indicacao,
            'veio_de_indicacao': bool(indicado_por_id)
        }), 201

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login do cliente"""
    try:
        data = request.get_json()
        email = data.get('email')
        senha = data.get('senha')

        conn = get_db()
        cursor = conn.cursor()

        # Mesmo motivo do login do painel: o celular põe maiúscula na primeira
        # letra sozinho, e o motorista que cadastrou "Joao@gmail.com" não
        # conseguia mais entrar digitando "joao@gmail.com".
        cursor.execute('''
            SELECT id, nome, email, senha_hash, placa, ocupacao,
                   status, motivo_recusa, empresa_convenio, codigo_indicacao
            FROM clientes WHERE LOWER(email) = LOWER(?)
        ''', ((email or '').strip(),))
        cliente = cursor.fetchone()

        if not cliente or not check_password_hash(cliente['senha_hash'], senha):
            conn.close()
            return jsonify({'erro': 'Email ou senha incorretos'}), 401

        # O login continua funcionando com cadastro pendente de propósito: a
        # pessoa precisa conseguir entrar para acompanhar a análise. O que não
        # sai é o cupom.
        situacao = (cliente['status'] or 'ativo').lower()

        # Clientes de antes do programa de indicação existir não têm código
        # ainda — gera na hora do primeiro login, para não deixar ninguém
        # sem link para compartilhar.
        codigo_indicacao = cliente['codigo_indicacao']
        if not codigo_indicacao:
            codigo_indicacao = f'CJ{cliente["id"]}'
            cursor.execute('UPDATE clientes SET codigo_indicacao = ? WHERE id = ?',
                           (codigo_indicacao, cliente['id']))
            conn.commit()

        conn.close()

        return jsonify({
            'cliente_id': cliente['id'],
            'nome': cliente['nome'],
            'email': cliente['email'],
            'placa': cliente['placa'],
            'ocupacao': cliente['ocupacao'],
            'status': situacao,
            'empresa_convenio': cliente['empresa_convenio'],
            'motivo_recusa': cliente['motivo_recusa'],
            'codigo_indicacao': codigo_indicacao,
            'mensagem': 'Login realizado com sucesso'
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== RECUPERAÇÃO DE SENHA ====================
#
# Vale para os dois públicos, com a mesma mecânica e tabelas diferentes:
#
#   - motorista  (tabela clientes) — entra por e-mail
#   - equipe     (tabela admin)    — entra por nome de usuário, e-mail à parte
#
# Três regras que valem para os dois e não são detalhe:
#
# 1. O banco guarda o HASH do token, nunca o token. O link vai por e-mail e
#    some dali; quem olhar o banco depois não acha nada aproveitável.
#
# 2. A resposta é sempre a mesma, exista o cadastro ou não. Se dissesse
#    "e-mail não encontrado", qualquer um descobriria quem é cliente do posto
#    testando e-mails na tela — dado de cliente vazando de graça.
#
# 3. Redefinir a senha derruba a sessão aberta. Se alguém entrou com a senha
#    antiga, a redefinição expulsa essa pessoa, que é justamente o que se
#    espera de "esqueci a senha" quando a conta foi tomada.

RESET_VALIDADE_MIN = 60      # o link vale 1 hora
RESET_INTERVALO_SEG = 60     # espera mínima entre dois pedidos do mesmo cadastro

# Resposta única do pedido de link — ver regra 2 acima.
_RESPOSTA_GENERICA = ('Se este cadastro existir, o link para criar uma nova senha '
                      'foi enviado para o e-mail cadastrado. Confira também a '
                      'caixa de spam.')


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _gerar_token():
    """Token aleatório de verdade (secrets, não random)."""
    return secrets.token_urlsafe(32)


def _agora():
    return agora().strftime('%Y-%m-%d %H:%M:%S')


def _pediu_agora_ha_pouco(pedido_em):
    """
    Trava simples contra alguém ficar apertando 'enviar' e enchendo a caixa
    de e-mail de outra pessoa — e contra gastar a cota de envio à toa.
    """
    if not pedido_em:
        return False
    try:
        anterior = datetime.strptime(pedido_em, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False
    return (agora() - anterior).total_seconds() < RESET_INTERVALO_SEG


def _gravar_pedido_reset(cursor, tabela, registro_id, token):
    expira = (agora() + timedelta(minutes=RESET_VALIDADE_MIN)
              ).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        f'UPDATE {tabela} SET reset_token_hash = ?, reset_expira = ?, '
        f'reset_pedido_em = ? WHERE id = ?',
        (_hash_token(token), expira, _agora(), registro_id))


def _buscar_por_token(cursor, tabela, campos, token):
    """
    Acha o dono de um token válido. Devolve o registro ou None.
    Confere as três coisas: existe, não expirou, não foi usado.
    """
    cursor.execute(
        f'SELECT {campos}, reset_expira FROM {tabela} WHERE reset_token_hash = ?',
        (_hash_token(token),))
    registro = cursor.fetchone()
    if not registro:
        return None
    if not registro['reset_expira'] or registro['reset_expira'] < _agora():
        return None
    return registro


@app.route('/api/auth/esqueci-senha', methods=['POST'])
def esqueci_senha_cliente():
    """Motorista pede o link de redefinição, informando o e-mail do cadastro."""
    try:
        email = (request.get_json().get('email') or '').strip()

        if not validar_email(email):
            return jsonify({'erro': 'Digite um e-mail válido'}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, email, reset_pedido_em
            FROM clientes WHERE LOWER(email) = LOWER(?)
        ''', (email,))
        cliente = cursor.fetchone()

        # Cadastro inexistente: responde igual e não envia nada.
        if not cliente:
            conn.close()
            return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200

        if _pediu_agora_ha_pouco(cliente['reset_pedido_em']):
            conn.close()
            return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200

        token = _gerar_token()
        _gravar_pedido_reset(cursor, 'clientes', cliente['id'], token)
        conn.commit()
        conn.close()

        ok, erro = enviar_link_recuperacao(
            cliente['email'], cliente['nome'], token,
            tipo='cliente', validade_minutos=RESET_VALIDADE_MIN)

        # Falha de envio é problema do posto, não do motorista: aí sim
        # respondemos com erro, senão ele fica esperando um e-mail que nunca
        # vai chegar.
        if not ok:
            return jsonify({'erro': erro}), 502

        return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/esqueci-senha', methods=['POST'])
def esqueci_senha_admin():
    """
    Usuário do painel pede o link. Aceita o nome de usuário OU o e-mail —
    o frentista lembra do login, e nem sempre de qual e-mail cadastrou.
    """
    try:
        identificador = (request.get_json().get('identificador') or '').strip()

        if len(identificador) < 3:
            return jsonify({'erro': 'Digite seu usuário ou seu e-mail'}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, usuario, nome, email, ativo, reset_pedido_em
            FROM admin
            WHERE LOWER(usuario) = LOWER(?) OR LOWER(email) = LOWER(?)
        ''', (identificador, identificador))
        admin = cursor.fetchone()

        # Não existe, está desativado, ou nunca teve e-mail preenchido:
        # em todos os casos a resposta é a mesma e não sai e-mail nenhum.
        if not admin or admin['ativo'] == 0 or not (admin['email'] or '').strip():
            conn.close()
            return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200

        if _pediu_agora_ha_pouco(admin['reset_pedido_em']):
            conn.close()
            return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200

        token = _gerar_token()
        _gravar_pedido_reset(cursor, 'admin', admin['id'], token)
        conn.commit()
        conn.close()

        ok, erro = enviar_link_recuperacao(
            admin['email'], admin['nome'] or admin['usuario'], token,
            tipo='admin', validade_minutos=RESET_VALIDADE_MIN)

        if not ok:
            return jsonify({'erro': erro}), 502

        return jsonify({'mensagem': _RESPOSTA_GENERICA}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/auth/validar-reset', methods=['GET'])
def validar_reset():
    """
    A tela pergunta se o link ainda vale, antes de deixar a pessoa digitar.
    Sem isto ela escolhe uma senha nova, confirma e só então descobre que o
    link tinha vencido — e ainda por cima não sabe se a senha mudou ou não.
    """
    try:
        token = (request.args.get('token') or '').strip()
        tipo = (request.args.get('tipo') or 'cliente').strip()

        if not token:
            return jsonify({'valido': False, 'erro': 'Link inválido'}), 400

        conn = get_db()
        cursor = conn.cursor()

        if tipo == 'admin':
            registro = _buscar_por_token(cursor, 'admin', 'id, usuario, nome', token)
            quem = (registro['nome'] or registro['usuario']) if registro else None
        else:
            registro = _buscar_por_token(cursor, 'clientes', 'id, nome', token)
            quem = registro['nome'] if registro else None

        conn.close()

        if not registro:
            return jsonify({
                'valido': False,
                'erro': 'Este link já foi usado ou passou de 1 hora. '
                        'Peça um novo na tela de login.'
            }), 400

        return jsonify({
            'valido': True,
            'nome': quem,
            'minimo_senha': 8 if tipo == 'admin' else 6,
        }), 200
    except Exception as e:
        return jsonify({'valido': False, 'erro': str(e)}), 500


@app.route('/api/auth/redefinir-senha', methods=['POST'])
def redefinir_senha():
    """Grava a senha nova. Serve motorista e equipe, conforme o 'tipo'."""
    try:
        data = request.get_json()
        token = (data.get('token') or '').strip()
        tipo = (data.get('tipo') or 'cliente').strip()
        senha_nova = data.get('senha_nova') or ''

        if not token:
            return jsonify({'erro': 'Link inválido'}), 400

        # A equipe mexe em preço e desconto, então a exigência é maior.
        minimo = 8 if tipo == 'admin' else 6
        if len(senha_nova) < minimo:
            return jsonify({
                'erro': f'A senha deve ter ao menos {minimo} caracteres'
            }), 400

        conn = get_db()
        cursor = conn.cursor()

        if tipo == 'admin':
            registro = _buscar_por_token(cursor, 'admin',
                                         'id, usuario, nome, email', token)
        else:
            registro = _buscar_por_token(cursor, 'clientes', 'id, nome, email', token)

        if not registro:
            conn.close()
            return jsonify({
                'erro': 'Este link já foi usado ou passou de 1 hora. '
                        'Peça um novo na tela de login.'
            }), 400

        senha_hash = generate_password_hash(senha_nova)

        if tipo == 'admin':
            # token = NULL derruba a sessão aberta (ver regra 3 no topo).
            cursor.execute('''
                UPDATE admin
                SET senha_hash = ?, token = NULL, token_expira = NULL,
                    reset_token_hash = NULL, reset_expira = NULL
                WHERE id = ?
            ''', (senha_hash, registro['id']))
        else:
            cursor.execute('''
                UPDATE clientes
                SET senha_hash = ?, reset_token_hash = NULL, reset_expira = NULL
                WHERE id = ?
            ''', (senha_hash, registro['id']))

        conn.commit()
        conn.close()

        # Aviso de que a senha mudou. Se falhar, a senha já foi trocada e a
        # pessoa consegue entrar — não é motivo para devolver erro.
        try:
            destino = (registro['email'] or '').strip()
            if destino:
                enviar_aviso_senha_alterada(
                    destino,
                    registro['nome'] or (registro['usuario'] if tipo == 'admin' else ''),
                    tipo=tipo)
        except Exception as e:
            print(f"⚠️ Senha redefinida, mas o aviso não saiu: {e}")

        return jsonify({
            'mensagem': 'Senha alterada. Já pode entrar com a senha nova.'
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== ROTAS DE PRODUTOS ====================

@app.route('/api/cliente/placa', methods=['POST'])
def atualizar_placa():
    """Troca a placa do carro em uso.

    Motorista de aplicativo troca de carro com frequência — alugado, da frota,
    o do fim de semana. Se a placa fosse fixa no cadastro, a conferência na
    bomba falharia justamente para quem mais usa o app. Aqui ele atualiza em
    dois toques, e o cupom congela a placa no momento em que é gerado.
    """
    try:
        data = request.get_json()
        cliente_id = data.get('cliente_id')
        if not cliente_id:
            return jsonify({'erro': 'cliente_id é obrigatório'}), 400

        placa = normalizar_placa(data.get('placa'))
        erro = validar_placa(placa)
        if erro:
            return jsonify({'erro': erro}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT placa FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        anterior = cliente['placa']
        agora_iso = agora().isoformat()

        cursor.execute('UPDATE clientes SET placa = ?, data_placa = ? WHERE id = ?',
                       (placa, agora_iso, cliente_id))

        # Troca de placa é exatamente o movimento que uma conta emprestada faria.
        # Não bloqueia, mas fica gravado para o painel de suspeitas.
        if anterior and anterior != placa:
            cursor.execute('''
                INSERT INTO auditoria
                (data_hora, admin_usuario, admin_nivel, acao, campo,
                 valor_anterior, valor_novo, detalhe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                agora().strftime('%Y-%m-%d %H:%M:%S'),
                f'cliente:{cliente_id}', 'cliente', 'troca_placa', 'placa',
                anterior, placa, f'Cliente {cliente_id} trocou a placa do carro em uso'
            ))

        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Placa atualizada.', 'placa': placa}), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/produtos', methods=['GET'])
def listar_produtos():
    """Lista todos os produtos com preços"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, nome, tipo, preco_atual, unidade, icone
            FROM produtos
            WHERE ativo = 1
            ORDER BY tipo DESC, id
        ''')
        produtos = cursor.fetchall()
        conn.close()

        return jsonify({
            'produtos': [
                {
                    'id': p['id'],
                    'nome': p['nome'],
                    'tipo': p['tipo'],
                    'preco': round(p['preco_atual'], 2),
                    'unidade': p['unidade'],
                    'icone': p['icone']
                }
                for p in produtos
            ]
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/produtos/<int:produto_id>/preco', methods=['GET'])
def get_preco_produto(produto_id):
    """Obtém preço atual de um produto"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, nome, preco_atual, data_atualizacao
            FROM produtos
            WHERE id = ? AND ativo = 1
        ''', (produto_id,))
        produto = cursor.fetchone()
        conn.close()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        return jsonify({
            'produto_id': produto['id'],
            'nome': produto['nome'],
            'preco_atual': round(produto['preco_atual'], 2),
            'data_atualizacao': produto['data_atualizacao']
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE CUPOM ====================

@app.route('/api/cupom/gerar', methods=['POST'])
def gerar_cupom():
    """Gera cupom/QR code para cliente em um produto específico"""
    try:
        data = request.get_json()
        cliente_id = data.get('cliente_id')
        produto_id = data.get('produto_id')

        if not produto_id:
            return jsonify({'erro': 'Produto_id é obrigatório'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Verifica cliente
        cursor.execute('''
            SELECT id, nome, desconto_tipo, desconto_valor, status, motivo_recusa,
                   tipo_cliente
            FROM clientes
            WHERE id = ?
        ''', (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        # A alçada só vale se travar aqui: sem isto o cadastro de convênio
        # ficaria "pendente" no painel e mesmo assim saía abastecendo com
        # desconto — a aprovação viraria enfeite.
        situacao = (cliente['status'] or 'ativo').lower()
        if situacao == 'pendente':
            conn.close()
            return jsonify({
                'erro': 'Seu cadastro ainda está em análise pela gerência. '
                        'Assim que for aprovado você poderá gerar cupons.',
                'status': 'pendente'
            }), 403
        if situacao in ('recusado', 'bloqueado', 'inativo'):
            motivo = cliente['motivo_recusa'] or 'Fale com a gerência dos postos.'
            conn.close()
            return jsonify({
                'erro': f'Seu cadastro não está liberado. {motivo}',
                'status': situacao
            }), 403

        # Verifica produto (preço, desconto e limite vêm da tela de administrador)
        cursor.execute('''
            SELECT id, nome, tipo, preco_atual, unidade, desconto_valor, desconto_tipo,
                   limite_litros, preco_custo
            FROM produtos
            WHERE id = ? AND ativo = 1
        ''', (produto_id,))
        produto = cursor.fetchone()

        if not produto:
            return jsonify({'erro': 'Produto não encontrado'}), 404

        # ---- limite por categoria ----
        #
        # Um cupom de combustível por dia e um de óleo por semana, contando a
        # categoria inteira e não o produto. Ver o bloco de constantes no topo.
        #
        # Frentista é um caso à parte: combustível vira "uma vez a cada 7 dias
        # corridos" (em vez de por dia) e óleo é bloqueado por completo — a
        # única saída para óleo é o Master liberar manualmente, mesma válvula
        # de escape usada em qualquer outro limite deste sistema.
        hoje = agora().strftime('%Y-%m-%d')
        categoria = categoria_do_produto(produto['tipo'])
        tipo_cliente = (cliente['tipo_cliente'] or 'comum').strip().lower()
        eh_frentista = (tipo_cliente == 'frentista')

        if eh_frentista and categoria == 'oleo':
            cursor.execute('''
                SELECT id FROM liberacoes_extras
                WHERE cliente_id = ? AND categoria IN ('oleo', 'qualquer')
                  AND usada = 0 AND cancelada = 0 AND validade >= ?
                ORDER BY id ASC
            ''', (cliente_id, hoje))
            liberacao = cursor.fetchone()
            if not liberacao:
                conn.close()
                return jsonify({
                    'erro': 'Cupom de óleo não faz parte do programa de frentistas. '
                            'Fale com a gerência se for um caso especial.',
                    'limite_atingido': True,
                    'categoria': 'oleo'
                }), 403
            liberacao_usada = liberacao['id']
            cupom_a_cancelar = None
        else:
            intervalo = (INTERVALO_FRENTISTA_COMBUSTIVEL_DIAS if eh_frentista
                         else INTERVALO_DIAS[categoria])
            desde = (agora() - timedelta(days=intervalo - 1)).strftime('%Y-%m-%d')

            # Cupons da mesma categoria dentro da janela. Cancelados não contam —
            # cupom trocado antes de usar não pode consumir o direito do dia.
            cursor.execute('''
                SELECT c.id, c.status, c.data_geracao, c.qrcode,
                       c.quantidade_permitida, c.quantidade_utilizada,
                       p.nome AS produto_nome, p.id AS produto_id
                FROM cupons c
                LEFT JOIN produtos p ON p.id = c.produto_id
                WHERE c.cliente_id = ?
                  AND COALESCE(c.categoria, CASE WHEN LOWER(p.tipo) = 'oleo'
                                                 THEN 'oleo' ELSE 'combustivel' END) = ?
                  AND c.data_geracao >= ?
                  AND COALESCE(c.status, '') <> 'cancelado'
                ORDER BY c.id DESC
            ''', (cliente_id, categoria, desde))
            na_janela = cursor.fetchall()

            if na_janela:
                existente = na_janela[0]
                ja_usado = (existente['status'] or '') in ('parcial', 'completo')

                # Mesmo produto e ainda não usado: não é caso de troca nem de
                # bloqueio — é o cliente reabrindo o cupom que já tem.
                if not ja_usado and existente['produto_id'] == produto['id']:
                    conn.close()
                    return jsonify({
                        'erro': f'Você já tem um cupom de {produto["nome"]} em aberto hoje. '
                                f'Ele está na sua tela inicial.',
                        'ja_tem': True,
                        'qrcode_data': existente['qrcode']
                    }), 400

                # Ainda não usado, produto diferente: pode trocar. Não gasta
                # liberação nenhuma — o direito do dia continua sendo um só.
                if not ja_usado:
                    if not data.get('confirmar_troca'):
                        conn.close()
                        return jsonify({
                            'erro': f'Você já gerou um cupom de {existente["produto_nome"]} hoje.',
                            'pode_trocar': True,
                            'cupom_atual_id': existente['id'],
                            'cupom_atual_produto': existente['produto_nome'],
                            'produto_novo': produto['nome'],
                            'mensagem': (f'Quer trocar o cupom de {existente["produto_nome"]} '
                                         f'pelo de {produto["nome"]}? O anterior deixa de valer.')
                        }), 409
                    # Confirmado: cancela o antigo mais abaixo, depois de saber o
                    # id do novo. Guarda a referência por enquanto.
                    cupom_a_cancelar = existente['id']
                else:
                    cupom_a_cancelar = None

                # Já usado dentro da janela: só passa com liberação do Master.
                if ja_usado:
                    cursor.execute('''
                        SELECT id, motivo, liberado_por FROM liberacoes_extras
                        WHERE cliente_id = ? AND categoria IN (?, 'qualquer')
                          AND usada = 0 AND cancelada = 0 AND validade >= ?
                        ORDER BY id ASC
                    ''', (cliente_id, categoria, hoje))
                    liberacao = cursor.fetchone()

                    if not liberacao:
                        conn.close()
                        if eh_frentista:
                            proxima = (datetime.strptime(existente['data_geracao'], '%Y-%m-%d')
                                       + timedelta(days=intervalo)).strftime('%d/%m/%Y')
                            aviso = (f'Você já usou seu cupom de combustível da semana. '
                                     f'O próximo fica disponível em {proxima}.')
                        elif categoria == 'oleo':
                            proxima = (datetime.strptime(existente['data_geracao'], '%Y-%m-%d')
                                       + timedelta(days=intervalo)).strftime('%d/%m/%Y')
                            aviso = (f'Você já usou seu cupom de óleo. O próximo fica '
                                     f'disponível em {proxima}.')
                        else:
                            aviso = ('Você já usou seu cupom de combustível hoje. '
                                     'O próximo fica disponível amanhã.')
                        return jsonify({
                            'erro': aviso,
                            'limite_atingido': True,
                            'categoria': categoria,
                            'produto_usado': existente['produto_nome']
                        }), 403

                    liberacao_usada = liberacao['id']
                else:
                    liberacao_usada = None
            else:
                cupom_a_cancelar = None
                liberacao_usada = None

        # Desconto do produto; só cai no desconto do cliente se o produto nunca
        # foi configurado (None). Um valor explícito de zero significa "este
        # produto não tem desconto" e vale como está — antes, `<= 0` tratava
        # zero como "não configurado" e reintroduzia o desconto do cliente
        # escondido, então nenhum produto conseguia ter desconto zero de verdade.
        desconto_valor = produto['desconto_valor']
        desconto_tipo = produto['desconto_tipo'] or 'fixo'

        if desconto_valor is None:
            desconto_valor = cliente['desconto_valor'] or 0
            desconto_tipo = cliente['desconto_tipo'] or 'fixo'

        preco = produto['preco_atual'] or 0
        if desconto_tipo == 'percentual':
            desconto_por_unidade = preco * (desconto_valor / 100)
        else:
            desconto_por_unidade = desconto_valor

        desconto_por_unidade = min(desconto_por_unidade, preco)
        preco_final_unitario = preco - desconto_por_unidade

        # Segunda camada: não emite cupom que já nasce dando prejuízo. A trava
        # da tela de preços resolve o caso normal, mas o desconto pode ter sido
        # gravado antes dessa trava existir, ou o custo pode ter subido depois.
        # Cupom errado emitido é dinheiro perdido — o preço fica congelado nele.
        custo_produto = round(produto['preco_custo'] or 0, 2)
        if custo_produto > 0 and round(preco_final_unitario, 2) < custo_produto:
            conn.close()
            return jsonify({
                'erro': f'{produto["nome"]} está com o desconto mal configurado no momento '
                        f'e não podemos emitir o cupom. Tente outro produto ou volte mais '
                        f'tarde — já avisamos a gerência.',
                'motivo_tecnico': (f'preço final R$ {preco_final_unitario:.2f} abaixo do '
                                   f'custo R$ {custo_produto:.2f}')
            }), 409

        quantidade_permitida = produto['limite_litros'] or 50
        economia_total = desconto_por_unidade * quantidade_permitida

        # ---- programa de indicação: prêmio pronto para usar? ----
        #
        # Se este cupom está trocando um outro que já tinha um prêmio
        # congelado (troca de produto antes de usar), devolve o prêmio para
        # "disponível" primeiro — senão a troca faria o cliente perder o
        # prêmio, ou pior, permitiria pegar um segundo por engano.
        if cupom_a_cancelar:
            cursor.execute('''
                UPDATE recompensas_indicacao
                SET status = 'disponivel', cupom_id = NULL, data_aplicacao = NULL
                WHERE cupom_id = ? AND status = 'aplicado'
            ''', (cupom_a_cancelar,))

        # Prêmio que ficou preso num cupom de outro dia que nunca foi usado
        # (o cupom vale só no dia em que nasceu) volta para a prateleira. Sem
        # isto, esquecer de abastecer num dia custaria o prêmio inteiro ao
        # cliente — e ele não tem como saber por quê. A limpeza é feita aqui,
        # na hora de gerar o próximo cupom, para não depender de nenhuma
        # rotina agendada.
        cursor.execute('''
            UPDATE recompensas_indicacao
            SET status = 'disponivel', cupom_id = NULL, data_aplicacao = NULL
            WHERE cliente_id = ? AND status = 'aplicado' AND cupom_id IN (
                SELECT id FROM cupons
                WHERE cliente_id = ?
                  AND COALESCE(status, '') <> 'completo'
                  AND data_geracao < ?
            )
        ''', (cliente_id, cliente_id, hoje))

        # Prêmio que passou da validade vira 'expirado' antes de escolher.
        expirar_premios_vencidos(cursor, cliente_id)

        cursor.execute('''
            SELECT id, valor FROM recompensas_indicacao
            WHERE cliente_id = ? AND status = 'disponivel'
            ORDER BY id ASC LIMIT 1
        ''', (cliente_id,))
        recompensa = cursor.fetchone()
        valor_recompensa_congelada = round(recompensa['valor'], 2) if recompensa else 0

        # Gera cupom (preço e desconto ficam congelados neste cupom)
        qr_data, qr_image = gerar_qrcode()

        cursor.execute('''
            INSERT INTO cupons
            (cliente_id, produto_id, qrcode, data_geracao, quantidade_permitida,
             quantidade_utilizada, status, preco_unitario, desconto_unitario,
             desconto_valor, desconto_tipo, categoria, liberacao_id,
             valor_recompensa_aplicada)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cliente_id,
            produto_id,
            qr_data,
            hoje,
            quantidade_permitida,
            0,
            'pendente',
            preco,
            desconto_por_unidade,
            desconto_valor,
            desconto_tipo,
            categoria,
            liberacao_usada,
            valor_recompensa_congelada
        ))

        cupom_id = cursor.lastrowid

        if recompensa:
            cursor.execute('''
                UPDATE recompensas_indicacao
                SET status = 'aplicado', cupom_id = ?, data_aplicacao = ?
                WHERE id = ?
            ''', (cupom_id, agora().strftime('%Y-%m-%d %H:%M:%S'), recompensa['id']))

        # Troca confirmada: o cupom antigo é cancelado e passa a apontar para o
        # novo. Cancelar em vez de apagar mantém a troca visível no histórico.
        if cupom_a_cancelar:
            cursor.execute('''
                UPDATE cupons
                SET status = 'cancelado', trocado_por = ?, data_cancelamento = ?
                WHERE id = ? AND status = 'pendente'
            ''', (cupom_id, agora().strftime('%Y-%m-%d %H:%M:%S'), cupom_a_cancelar))

        # Liberação do Master é de uso único: gasta agora.
        if liberacao_usada:
            cursor.execute('''
                UPDATE liberacoes_extras
                SET usada = 1, cupom_id = ?, data_uso = ?
                WHERE id = ?
            ''', (cupom_id, agora().strftime('%Y-%m-%d %H:%M:%S'), liberacao_usada))
            registrar_auditoria(
                cursor, None, 'liberacao_extra_usada',
                detalhe=(f'Cliente {cliente["nome"]} usou liberação extra de '
                         f'{NOME_CATEGORIA[categoria]} no cupom #{cupom_id} '
                         f'({produto["nome"]})'))

        conn.commit()
        conn.close()

        return jsonify({
            'cupom_id': cupom_id,
            'trocou': bool(cupom_a_cancelar),
            'usou_liberacao': bool(liberacao_usada),
            'qrcode_data': qr_data,
            'qrcode_image': f'data:image/png;base64,{qr_image}',
            'cliente_nome': cliente['nome'],
            'produto_id': produto['id'],
            'produto_nome': produto['nome'],
            'unidade': produto['unidade'],
            'preco_produto': round(preco, 2),
            'preco_unitario_com_desconto': round(preco_final_unitario, 2),
            'desconto_tipo': desconto_tipo,
            'desconto_valor': desconto_valor,
            'desconto_por_unidade': round(desconto_por_unidade, 2),
            'quantidade_permitida': quantidade_permitida,
            # A economia por litro é o cálculo de sempre; o prêmio de
            # indicação é um valor fixo, somado uma vez só (não por litro).
            'economia_total': round(economia_total + valor_recompensa_congelada, 2),
            'bonus_indicacao': valor_recompensa_congelada,
            'uso_unico': USO_UNICO,
            'mensagem': 'QR code gerado com sucesso!'
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/cupom/ativos', methods=['GET'])
def cupons_ativos():
    """Devolve os cupons do cliente gerados HOJE, com o QR code reconstruído.

    Permite ao motorista recuperar o cupom mesmo depois de fechar o app.
    """
    try:
        cliente_id = request.args.get('cliente_id')
        if not cliente_id:
            return jsonify({'erro': 'cliente_id é obrigatório'}), 400

        hoje = agora().date()

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, desconto_tipo, desconto_valor FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        cursor.execute('''
            SELECT c.id, c.qrcode, c.data_geracao, c.status,
                   c.quantidade_permitida, c.quantidade_utilizada,
                   c.preco_unitario, c.desconto_unitario, c.desconto_valor, c.desconto_tipo,
                   p.id AS produto_id, p.nome AS produto_nome,
                   p.preco_atual, p.unidade, p.icone
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            WHERE c.cliente_id = ? AND c.data_geracao = ?
              AND COALESCE(c.status, '') <> 'cancelado'
            ORDER BY c.id DESC
        ''', (cliente_id, hoje))
        linhas = cursor.fetchall()
        conn.close()

        cupons = []
        for c in linhas:
            permitida = c['quantidade_permitida'] or 0
            utilizada = c['quantidade_utilizada'] or 0

            # preço e desconto CONGELADOS na geração do cupom
            preco = c['preco_unitario'] or c['preco_atual'] or 0
            desconto_por_unidade = c['desconto_unitario'] or 0

            if desconto_por_unidade <= 0:
                tipo = c['desconto_tipo'] or cliente['desconto_tipo']
                valor = c['desconto_valor'] or cliente['desconto_valor'] or 0
                desconto_por_unidade = preco * (valor / 100) if tipo == 'percentual' else valor

            cupons.append({
                'cupom_id': c['id'],
                'qrcode_data': c['qrcode'],
                'qrcode_image': f"data:image/png;base64,{imagem_qrcode(c['qrcode'])}",
                'status': c['status'],
                'produto_id': c['produto_id'],
                'produto_nome': c['produto_nome'],
                'produto_icone': c['icone'],
                'unidade': c['unidade'],
                'preco_produto': round(preco, 2),
                'preco_unitario_com_desconto': round(preco - desconto_por_unidade, 2),
                'desconto_tipo': cliente['desconto_tipo'],
                'desconto_valor': cliente['desconto_valor'],
                'desconto_por_unidade': round(desconto_por_unidade, 2),
                'quantidade_permitida': permitida,
                'quantidade_utilizada': utilizada,
                'quantidade_restante': round(permitida - utilizada, 2),
                'economia_total': round(desconto_por_unidade * permitida, 2),
                'cliente_nome': cliente['nome']
            })

        return jsonify({'data': str(hoje), 'cupons': cupons}), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/cupom/consultar', methods=['GET'])
@exige_admin
def consultar_cupom():
    """Lê um QR code e devolve o cupom SEM registrar nada.

    É o que a tela do frentista chama assim que a câmera lê o código: mostra
    de quem é o cupom, qual combustível, o preço já com desconto e quantos
    litros ainda restam, para o frentista conferir antes de liberar a bomba.
    """
    try:
        qr = (request.args.get('qrcode') or '').strip()
        if not qr:
            return jsonify({'erro': 'Informe o código do cupom'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.*, p.nome AS produto_nome, p.unidade, p.icone,
                   p.preco_atual, cl.nome AS cliente_nome, cl.cpf AS cliente_cpf,
                   cl.status AS cliente_status, cl.placa AS cliente_placa,
                   cl.ocupacao AS cliente_ocupacao,
                   cl.desconto_tipo AS cli_desc_tipo, cl.desconto_valor AS cli_desc_valor
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            LEFT JOIN clientes cl ON cl.id = c.cliente_id
            WHERE c.qrcode = ?
        ''', (qr,))
        cupom = cursor.fetchone()

        # A mesma placa em vários cadastros pode ser táxi dividido por turno —
        # ou conta emprestada. O frentista precisa ver isso antes de liberar.
        placa = cupom['cliente_placa'] if cupom else None
        contagem_placa = 1
        if placa:
            cursor.execute('SELECT COUNT(*) AS n FROM clientes WHERE placa = ?', (placa,))
            linha = cursor.fetchone()
            contagem_placa = (linha['n'] if linha else 1) or 1

        conn.close()

        if not cupom:
            return jsonify({
                'erro': 'Cupom não encontrado. Confira o código ou peça ao '
                        'motorista para gerar um novo no aplicativo.'
            }), 404

        # Preço e desconto ficam CONGELADOS na geração — reajuste posterior
        # não muda um cupom já emitido.
        preco = cupom['preco_unitario'] or cupom['preco_atual'] or 0
        desc_unidade = cupom['desconto_unitario'] or 0
        if desc_unidade <= 0:
            tipo = cupom['desconto_tipo'] or cupom['cli_desc_tipo']
            valor = cupom['desconto_valor'] or cupom['cli_desc_valor'] or 0
            desc_unidade = desconto_por_unidade(preco, valor, tipo)

        permitida = cupom['quantidade_permitida'] or 0
        utilizada = cupom['quantidade_utilizada'] or 0
        restante = round(permitida - utilizada, 2)

        data_geracao = str(cupom['data_geracao'])[:10]
        hoje = str(agora().date())

        # Um único lugar decide se pode abastecer — a tela só exibe o motivo.
        if data_geracao != hoje:
            valido, motivo = False, (
                f'Cupom de {data_geracao[8:10]}/{data_geracao[5:7]}. '
                f'Vale só no dia em que foi gerado — peça um novo no aplicativo.'
            )
        elif cupom['cliente_status'] and cupom['cliente_status'] != 'ativo':
            valido, motivo = False, 'Cadastro do motorista está inativo.'
        elif (cupom['status'] or '').lower() == 'cancelado':
            # O motorista trocou este cupom por outro produto antes de usar.
            # Sem esta mensagem, o frentista veria "cupom não vale" e não
            # saberia que existe um novo válido no celular do motorista.
            valido, motivo = False, ('Este cupom foi trocado por outro no aplicativo. '
                                     'Peça ao motorista para mostrar o cupom atual.')
        elif (cupom['status'] or '').lower() == 'completo':
            # Com uso único o cupom fecha mesmo sobrando saldo. Quem manda é o
            # status, não a conta de litros — senão a tela mostraria "válido,
            # restam 30 L" para um cupom que a baixa já encerrou.
            valido, motivo = False, ('Cupom já utilizado. Vale para um abastecimento só — '
                                     'o motorista deve gerar um novo amanhã.')
        elif restante <= 0:
            valido, motivo = False, 'Cupom já usado por completo hoje.'
        else:
            valido, motivo = True, None

        # Cupom encerrado não tem saldo a mostrar, mesmo que a subtração dê
        # um número positivo.
        if not valido and (cupom['status'] or '').lower() == 'completo':
            restante = 0

        return jsonify({
            'valido': valido,
            'motivo': motivo,
            'cupom_id': cupom['id'],
            'qrcode': qr,
            'status': cupom['status'],
            'data_geracao': data_geracao,
            'cliente_nome': cupom['cliente_nome'],
            'cliente_cpf': _cpf_mascarado(cupom['cliente_cpf']),
            'placa': placa,
            'ocupacao': cupom['cliente_ocupacao'],
            'placa_em_varios_cadastros': contagem_placa > 1,
            'placa_qtd_cadastros': contagem_placa,
            'produto_id': cupom['produto_id'],
            'produto_nome': cupom['produto_nome'],
            'produto_icone': cupom['icone'],
            'unidade': cupom['unidade'] or 'L',
            'preco_bomba': round(preco, 2),
            'desconto_por_unidade': round(desc_unidade, 2),
            'preco_com_desconto': round(preco - desc_unidade, 2),
            'quantidade_permitida': round(permitida, 2),
            'quantidade_utilizada': round(utilizada, 2),
            'quantidade_restante': restante,
            # Avisa a tela do caixa/frentista ANTES da baixa: o motorista
            # precisa saber que não volta depois com o que sobrar.
            'uso_unico': USO_UNICO
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/cupom/usar', methods=['POST'])
@exige_admin
def usar_cupom():
    """Registra o abastecimento. Chamado pela tela do frentista na pista."""
    try:
        data = request.get_json()
        qrcode = data.get('qrcode')
        produto_id = data.get('produto_id')
        quantidade_agora = float(data.get('quantidade', 0))
        valor_sem_desconto = float(data.get('valor_sem_desconto', 0))

        # O posto vem do usuário logado — o frentista não escolhe, para o
        # abastecimento não cair no caixa do posto errado.
        poster_id = request.admin['poster_id'] or data.get('poster_id')

        if not qrcode or not produto_id or quantidade_agora <= 0:
            return jsonify({'erro': 'Dados incompletos'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Busca cupom
        cursor.execute('''
            SELECT * FROM cupons
            WHERE qrcode = ? AND status IN ('pendente', 'parcial')
        ''', (qrcode,))
        cupom = cursor.fetchone()

        if not cupom:
            conn.close()
            return jsonify({'erro': 'Cupom não encontrado ou já utilizado por completo'}), 404

        # Valida validade: cupom vale apenas no dia em que foi gerado
        data_geracao = str(cupom['data_geracao'])[:10]
        if data_geracao != str(agora().date()):
            conn.close()
            return jsonify({
                'erro': f'Cupom expirado (gerado em {data_geracao}). O cliente deve gerar um novo cupom hoje.'
            }), 400

        # Valida produto
        if cupom['produto_id'] != produto_id:
            conn.close()
            return jsonify({'erro': 'Produto não corresponde ao cupom'}), 400

        # Busca cliente e produto
        cursor.execute('''
            SELECT nome, placa, desconto_tipo, desconto_valor
            FROM clientes
            WHERE id = ?
        ''', (cupom['cliente_id'],))
        cliente = cursor.fetchone()

        cursor.execute('''
            SELECT nome, preco_custo
            FROM produtos
            WHERE id = ?
        ''', (produto_id,))
        produto = cursor.fetchone()

        # Verifica saldo
        litros_restantes = cupom['quantidade_permitida'] - cupom['quantidade_utilizada']

        if litros_restantes <= 0:
            conn.close()
            return jsonify({'erro': 'Cupom já utilizado por completo hoje'}), 400

        if quantidade_agora > litros_restantes:
            conn.close()
            return jsonify({
                'erro': f'Excede o saldo do cupom: restam {litros_restantes:.2f} L '
                        f'e foram informados {quantidade_agora:.2f} L.',
                'limite': litros_restantes,
                'solicitado': quantidade_agora
            }), 400

        # Se a tela não mandou o valor, calcula pelo preço congelado no cupom.
        # Evita que um erro de digitação na pista vire um valor cobrado errado.
        if valor_sem_desconto <= 0:
            preco_congelado = cupom['preco_unitario'] or 0
            if preco_congelado <= 0:
                cursor.execute('SELECT preco_atual FROM produtos WHERE id = ?', (produto_id,))
                linha = cursor.fetchone()
                preco_congelado = (linha['preco_atual'] if linha else 0) or 0
            valor_sem_desconto = round(preco_congelado * quantidade_agora, 2)

        # Desconto: usa o valor CONGELADO no cupom (preço/desconto do momento da geração).
        # Só cai no desconto do cliente se o cupom for antigo e realmente não
        # tiver esse dado (None) — um cupom com desconto congelado em zero
        # (produto sem desconto de propósito) precisa continuar em zero aqui,
        # senão a baixa na bomba reintroduz escondido o desconto do cliente que
        # o próprio cupom já tinha descartado ao nascer.
        desconto_unitario = cupom['desconto_unitario']

        if desconto_unitario is not None:
            valor_desconto = desconto_unitario * quantidade_agora
        elif (cupom['desconto_tipo'] or cliente['desconto_tipo']) == 'percentual':
            perc = cupom['desconto_valor'] or cliente['desconto_valor'] or 0
            valor_desconto = valor_sem_desconto * (perc / 100)
        else:
            fixo = cupom['desconto_valor'] or cliente['desconto_valor'] or 0
            valor_desconto = fixo * quantidade_agora

        # nunca deixar o desconto passar do valor da compra
        valor_desconto = min(valor_desconto, valor_sem_desconto)
        valor_final_sem_premio = valor_sem_desconto - valor_desconto

        # Terceira camada, a última antes do dinheiro sair: se este
        # abastecimento fecha abaixo do custo, não registra. Vale para cupons
        # emitidos antes das travas acima existirem, e para o caso do custo ter
        # subido depois que o cupom foi gerado. Decisão do Edmundo (19/08):
        # melhor o constrangimento na pista do que o prejuízo.
        #
        # A conta aqui é feita SEM o prêmio de indicação de propósito — ver o
        # bloco logo abaixo.
        custo_unitario = round((produto['preco_custo'] if produto else 0) or 0, 2)
        if custo_unitario > 0:
            custo_total = round(custo_unitario * quantidade_agora, 2)
            if round(valor_final_sem_premio, 2) < custo_total:
                conn.close()
                return jsonify({
                    'erro': f'ABASTECIMENTO NÃO AUTORIZADO — este cupom está com desconto '
                            f'maior que a margem do produto e daria prejuízo de '
                            f'R$ {custo_total - valor_final_sem_premio:.2f}. Não libere a '
                            f'bomba e avise a gerência.',
                    'motivo': 'desconto_abaixo_do_custo',
                    'valor_cobrado': round(valor_final_sem_premio, 2),
                    'custo': custo_total
                }), 409

        # ---- prêmio do programa de indicação ----
        #
        # Decisão do Edmundo (23/08): o prêmio é **despesa de marketing**, não
        # desconto de preço — ele decidiu gastar R$ 10 para trazer 3 clientes
        # novos. Por isso fica FORA da trava de margem acima: a margem do
        # combustível é fina (uns R$ 0,42/L no etanol), e se o prêmio entrasse
        # na conta da trava o motorista precisaria abastecer uns 24 litros só
        # para o prêmio "caber" — na prática o prêmio seria recusado na bomba
        # quase sempre, e o cliente ficaria com um brinde que não funciona.
        #
        # A trava continua fazendo o trabalho para o qual foi criada: pegar
        # desconto de preço mal configurado (o zero a mais na tela de preços).
        #
        # O que impede alguém de torrar o prêmio em 2 litros é o mesmo mínimo
        # de litros da positivação. Abaixo do mínimo o prêmio não entra e
        # volta para a prateleira — o cliente não perde nada, usa depois.
        bonus_indicacao = cupom['valor_recompensa_aplicada'] or 0
        premio_adiado = False

        if bonus_indicacao > 0:
            config_ind = ler_config_indicacoes(cursor)
            minimo_premio = minimo_litros_da_categoria(config_ind, cupom['categoria'])
            if quantidade_agora < minimo_premio:
                cursor.execute('''
                    UPDATE recompensas_indicacao
                    SET status = 'disponivel', cupom_id = NULL, data_aplicacao = NULL
                    WHERE cupom_id = ? AND status = 'aplicado'
                ''', (cupom['id'],))
                cursor.execute(
                    'UPDATE cupons SET valor_recompensa_aplicada = 0 WHERE id = ?',
                    (cupom['id'],))
                bonus_indicacao = 0
                premio_adiado = True

        valor_desconto = min(valor_desconto + bonus_indicacao, valor_sem_desconto)
        valor_final = valor_sem_desconto - valor_desconto

        turno = obter_turno()

        # Registra abastecimento
        momento = agora()
        cursor.execute('''
            INSERT INTO abastecimentos
            (cupom_id, cliente_id, produto_id, poster_id, data, hora, turno,
             quantidade, valor_original, valor_desconto, valor_final, registrado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cupom['id'],
            cupom['cliente_id'],
            produto_id,
            poster_id,
            momento.strftime('%Y-%m-%d'),
            momento.strftime('%H:%M:%S'),
            turno,
            quantidade_agora,
            valor_sem_desconto,
            valor_desconto,
            valor_final,
            request.admin['usuario']
        ))

        # Atualiza cupom
        nova_quantidade_utilizada = cupom['quantidade_utilizada'] + quantidade_agora

        # USO ÚNICO: o cupom fecha na primeira baixa, sobrando saldo ou não.
        # É decisão de negócio, não limitação técnica — o objetivo é que o
        # motorista encha o tanque de uma vez em vez de voltar três vezes no
        # dia por 10 litros. Para voltar a permitir uso em partes, basta
        # trocar USO_UNICO para False lá no começo do arquivo.
        if USO_UNICO:
            novo_status = 'completo'
        else:
            novo_status = ('completo' if nova_quantidade_utilizada >= cupom['quantidade_permitida']
                           else 'parcial')

        cursor.execute('''
            UPDATE cupons
            SET quantidade_utilizada = ?, status = ?, data_ultimo_uso = ?, turno_ultimo_uso = ?
            WHERE id = ?
        ''', (
            nova_quantidade_utilizada,
            novo_status,
            momento.strftime('%Y-%m-%d'),
            turno,
            cupom['id']
        ))

        # Deixa rastro de quem liberou o abastecimento na pista
        registrar_auditoria(
            cursor, request.admin, 'abastecimento',
            produto_id=produto_id,
            produto_nome=produto['nome'] if produto else None,
            detalhe=(f"{quantidade_agora:.2f} L para {cliente['nome']} no posto {poster_id} — "
                     f"cobrado R$ {valor_final:.2f} (desconto R$ {valor_desconto:.2f})")
        )

        # Programa de indicação: este é o momento em que "cadastro" vira de
        # fato "cliente" — gerou cupom E abasteceu. Duas travas: só a
        # PRIMEIRA vez de cada indicado conta ponto (indicacao_positiva_contada)
        # e o abastecimento precisa atingir o mínimo de litros da categoria.
        # Se não atingir, a indicação fica aguardando o próximo abastecimento.
        if novo_status == 'completo':
            cursor.execute(
                'SELECT indicado_por_id, indicacao_positiva_contada FROM clientes WHERE id = ?',
                (cupom['cliente_id'],)
            )
            cliente_indicado = cursor.fetchone()
            if (cliente_indicado and cliente_indicado['indicado_por_id']
                    and not cliente_indicado['indicacao_positiva_contada']):
                _processar_indicacao_positiva(
                    cursor, cupom['cliente_id'], cliente_indicado['indicado_por_id'],
                    quantidade_agora, cupom['categoria'])

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': 'Abastecimento registrado!',
            'cliente': cliente['nome'],
            # Placa e código do cupom vão para o comprovante impresso: é o que
            # liga o papel na mão do motorista ao registro no sistema, se
            # alguém precisar conferir depois.
            'placa': cliente['placa'],
            'cupom': cupom['qrcode'],
            'produto': produto['nome'] if produto else 'N/A',
            'posto': poster_id,
            'registrado_por': request.admin['nome'] or request.admin['usuario'],
            'hora': momento.strftime('%H:%M'),
            'quantidade': quantidade_agora,
            'valor_original': round(valor_sem_desconto, 2),
            'valor_desconto': round(valor_desconto, 2),
            'valor_final': round(valor_final, 2),
            'bonus_indicacao': round(bonus_indicacao, 2),
            # Avisa a tela da pista quando o prêmio ficou para a próxima por
            # causa do volume — senão o motorista acha que o brinde sumiu.
            'premio_adiado': premio_adiado,
            'premio_adiado_aviso': (
                f'O prêmio de indicação não entrou neste abastecimento porque o '
                f'volume ficou abaixo do mínimo. Ele continua guardado e entra '
                f'no próximo cupom.' if premio_adiado else None),
            'cupom_status': novo_status,
            'quantidade_utilizada': nova_quantidade_utilizada,
            'quantidade_permitida': cupom['quantidade_permitida'],
            # Com uso único o cupom fecha aqui, sobrando saldo ou não — o
            # saldo deixa de existir e a tela não pode sugerir que resta algo.
            'quantidade_restante': (0 if USO_UNICO
                                    else litros_restantes - quantidade_agora),
            'uso_unico': USO_UNICO,
            'saldo_perdido': (round(litros_restantes - quantidade_agora, 2)
                              if USO_UNICO else 0)
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/relatorio', methods=['GET'])
@exige_admin
def relatorio_admin():
    """Retorna relatórios para admin"""
    try:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        turno = request.args.get('turno')
        poster_id = request.args.get('poster_id')
        produto_id = request.args.get('produto_id')

        conn = get_db()
        cursor = conn.cursor()

        query = 'SELECT * FROM abastecimentos WHERE 1=1'
        params = []

        if data_inicio:
            query += ' AND data >= ?'
            params.append(data_inicio)

        if data_fim:
            query += ' AND data <= ?'
            params.append(data_fim)

        if turno:
            query += ' AND turno = ?'
            params.append(turno)

        if poster_id:
            query += ' AND poster_id = ?'
            params.append(poster_id)

        if produto_id:
            query += ' AND produto_id = ?'
            params.append(produto_id)

        cursor.execute(query, params)
        abastecimentos = cursor.fetchall()

        # Agrupa dados
        total_quantidade = sum([row['quantidade'] for row in abastecimentos])
        total_original = sum([row['valor_original'] for row in abastecimentos])
        total_desconto = sum([row['valor_desconto'] for row in abastecimentos])
        total_final = sum([row['valor_final'] for row in abastecimentos])
        total_abastecimentos = len(abastecimentos)

        conn.close()

        return jsonify({
            'total_abastecimentos': total_abastecimentos,
            'total_quantidade': round(total_quantidade, 2),
            'total_valor_original': round(total_original, 2),
            'total_valor_desconto': round(total_desconto, 2),
            'total_valor_final': round(total_final, 2),
            'abastecimentos': [dict(row) for row in abastecimentos]
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/existe', methods=['GET'])
def admin_existe():
    """Informa se já há administrador cadastrado (para a tela decidir login x cadastro)."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) AS total FROM admin')
        total = cursor.fetchone()['total']
        conn.close()
        return jsonify({'existe': total > 0}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/setup', methods=['POST'])
def admin_setup():
    """Cria o PRIMEIRO administrador. Só funciona enquanto não existir nenhum."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''
        email = (data.get('email') or '').strip()

        if len(usuario) < 3:
            return jsonify({'erro': 'Usuário deve ter ao menos 3 caracteres'}), 400
        if len(senha) < 8:
            return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400
        # O primeiro Master é justamente quem não tem ninguém acima para
        # destravá-lo. Sem e-mail, esquecer a senha custaria o painel inteiro.
        if not email:
            return jsonify({
                'erro': 'Informe o e-mail. Sem ele não há como recuperar este '
                        'acesso se a senha for esquecida.'
            }), 400
        if not validar_email(email):
            return jsonify({'erro': 'E-mail inválido'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) AS total FROM admin')
        if cursor.fetchone()['total'] > 0:
            conn.close()
            return jsonify({'erro': 'Já existe administrador cadastrado. Faça login.'}), 403

        cursor.execute(
            'INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, email, ativo) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (usuario, generate_password_hash(senha), data.get('poster_id') or 'AMBOS',
             'master', data.get('nome') or usuario, email, 1)
        )
        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Administrador Master criado! Faça login.'}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Login do administrador. Devolve um token válido por 12 horas."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''

        conn = get_db()
        cursor = conn.cursor()
        # LOWER dos dois lados: nome de usuário não pode ser sensível a
        # maiúscula. Quem foi cadastrado como "Carlos" e digitava "carlos" no
        # balcão levava "usuário ou senha incorretos" sem ter como descobrir
        # o porquê — o painel ainda o mostrava como Ativo.
        cursor.execute('''
            SELECT id, usuario, nome, senha_hash, poster_id, nivel, ativo
            FROM admin WHERE LOWER(usuario) = LOWER(?)
        ''', (usuario,))
        admin = cursor.fetchone()

        if not admin or not check_password_hash(admin['senha_hash'], senha):
            conn.close()
            return jsonify({'erro': 'Usuário ou senha incorretos'}), 401

        if admin['ativo'] == 0:
            conn.close()
            return jsonify({'erro': 'Usuário desativado. Fale com o administrador Master.'}), 403

        token = str(uuid.uuid4())
        expira = (agora() + timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('UPDATE admin SET token = ?, token_expira = ? WHERE id = ?',
                       (token, expira, admin['id']))
        conn.commit()
        conn.close()

        return jsonify({
            'token': token,
            'usuario': admin['usuario'],
            'nome': admin['nome'] or admin['usuario'],
            'nivel': admin['nivel'] or 'master',
            'poster_id': admin['poster_id'],
            'expira_em': expira
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/senha', methods=['POST'])
@exige_admin
def admin_trocar_senha():
    """Troca a senha do administrador logado."""
    try:
        data = request.get_json()
        senha_atual = data.get('senha_atual') or ''
        senha_nova = data.get('senha_nova') or ''

        if len(senha_nova) < 8:
            return jsonify({'erro': 'A nova senha deve ter ao menos 8 caracteres'}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT senha_hash FROM admin WHERE id = ?', (request.admin['id'],))
        atual = cursor.fetchone()

        if not check_password_hash(atual['senha_hash'], senha_atual):
            conn.close()
            return jsonify({'erro': 'Senha atual incorreta'}), 401

        cursor.execute('UPDATE admin SET senha_hash = ?, token = NULL WHERE id = ?',
                       (generate_password_hash(senha_nova), request.admin['id']))
        conn.commit()
        conn.close()

        return jsonify({'mensagem': 'Senha alterada. Faça login novamente.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== USUÁRIOS DO PAINEL (SÓ MASTER) ====================

@app.route('/api/admin/usuarios', methods=['GET'])
@exige_master
def admin_listar_usuarios():
    """Lista os usuários do painel."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, usuario, nome, email, nivel, poster_id, ativo, data_criacao
            FROM admin ORDER BY nivel, usuario
        ''')
        usuarios = cursor.fetchall()
        conn.close()

        return jsonify({
            # A tela usa isto para avisar quando o envio de e-mail não está
            # configurado no servidor — senão o Master cadastraria os e-mails
            # de todo mundo achando que a recuperação funciona.
            'email_configurado': email_configurado(),
            'usuarios': [{
                'id': u['id'],
                'usuario': u['usuario'],
                'nome': u['nome'] or u['usuario'],
                'email': (u['email'] or '').strip(),
                'nivel': u['nivel'] or 'master',
                'poster_id': u['poster_id'],
                'ativo': u['ativo'] if u['ativo'] is not None else 1
            } for u in usuarios]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/usuarios', methods=['POST'])
@exige_master
def admin_criar_usuario():
    """Cria um usuário Master ou Caixa."""
    try:
        data = request.get_json()
        usuario = (data.get('usuario') or '').strip()
        senha = data.get('senha') or ''
        nivel = data.get('nivel') or 'caixa'
        email = (data.get('email') or '').strip()

        if len(usuario) < 3:
            return jsonify({'erro': 'Usuário deve ter ao menos 3 caracteres'}), 400
        if len(senha) < 8:
            return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400
        if nivel not in ('master', 'gerencia', 'caixa'):
            return jsonify({'erro': "Nível deve ser 'master', 'gerencia' ou 'caixa'"}), 400

        # E-mail obrigatório para usuário novo. É o único caminho de volta
        # quando alguém esquece a senha: sem ele, destravar depende de outro
        # Master estar disponível — e por muito tempo houve um Master só.
        if not email:
            return jsonify({
                'erro': 'Informe o e-mail. É por ele que a pessoa recupera a '
                        'senha se esquecer.'
            }), 400
        if not validar_email(email):
            return jsonify({'erro': 'E-mail inválido'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # Mesma regra da tela de login: se "carlos" e "Carlos" logam no mesmo
        # lugar, não podem existir os dois.
        cursor.execute('SELECT id, usuario FROM admin WHERE LOWER(usuario) = LOWER(?)', (usuario,))
        ja_existe = cursor.fetchone()
        if ja_existe:
            conn.close()
            return jsonify({
                'erro': f'Já existe um usuário "{ja_existe["usuario"]}" '
                        f'(maiúsculas e minúsculas não diferenciam).'
            }), 400

        # Dois usuários do painel com o mesmo e-mail quebrariam a recuperação:
        # o link chegaria para um e serviria para outro.
        cursor.execute('SELECT usuario FROM admin WHERE LOWER(email) = LOWER(?)', (email,))
        email_repetido = cursor.fetchone()
        if email_repetido:
            conn.close()
            return jsonify({
                'erro': f'Este e-mail já está no usuário "{email_repetido["usuario"]}". '
                        f'Cada pessoa precisa do seu próprio.'
            }), 400

        cursor.execute('''
            INSERT INTO admin (usuario, senha_hash, poster_id, nivel, nome, email, ativo)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (usuario, generate_password_hash(senha), data.get('poster_id') or 'AMBOS',
              nivel, data.get('nome') or usuario, email, 1))

        conn.commit()
        conn.close()

        rotulos = {'master': 'Master (acesso total)',
                   'gerencia': 'Gerência (altera preços respeitando a margem mínima)',
                   'caixa': 'Caixa (somente consulta)'}
        rotulo = rotulos[nivel]
        return jsonify({'mensagem': f'Usuário {usuario} criado como {rotulo}'}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/usuarios/<int:usuario_id>', methods=['POST'])
@exige_master
def admin_alterar_usuario(usuario_id):
    """Ativa/desativa, troca nível ou redefine a senha de um usuário."""
    try:
        data = request.get_json() or {}

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, usuario, nivel FROM admin WHERE id = ?', (usuario_id,))
        alvo = cursor.fetchone()

        if not alvo:
            conn.close()
            return jsonify({'erro': 'Usuário não encontrado'}), 404

        # Não deixar o Master remover a si mesmo e ficar sem acesso
        if alvo['id'] == request.admin['id'] and (data.get('ativo') == 0 or data.get('nivel') in ('caixa', 'gerencia')):
            conn.close()
            return jsonify({'erro': 'Você não pode remover o próprio acesso Master'}), 400

        if 'nivel' in data:
            if data['nivel'] not in ('master', 'gerencia', 'caixa'):
                conn.close()
                return jsonify({'erro': "Nível deve ser 'master', 'gerencia' ou 'caixa'"}), 400
            cursor.execute('UPDATE admin SET nivel = ? WHERE id = ?', (data['nivel'], usuario_id))

        if 'ativo' in data:
            cursor.execute('UPDATE admin SET ativo = ?, token = NULL WHERE id = ?',
                           (int(data['ativo']), usuario_id))

        if data.get('senha_nova'):
            if len(data['senha_nova']) < 8:
                conn.close()
                return jsonify({'erro': 'A senha deve ter ao menos 8 caracteres'}), 400
            cursor.execute('UPDATE admin SET senha_hash = ?, token = NULL WHERE id = ?',
                           (generate_password_hash(data['senha_nova']), usuario_id))

        # Preencher ou corrigir o e-mail. É por aqui que os usuários criados
        # antes desta mudança ganham e-mail, sem precisar recriar ninguém.
        if 'email' in data:
            email = (data.get('email') or '').strip()
            if not email:
                conn.close()
                return jsonify({'erro': 'O e-mail não pode ficar em branco'}), 400
            if not validar_email(email):
                conn.close()
                return jsonify({'erro': 'E-mail inválido'}), 400
            cursor.execute(
                'SELECT usuario FROM admin WHERE LOWER(email) = LOWER(?) AND id <> ?',
                (email, usuario_id))
            repetido = cursor.fetchone()
            if repetido:
                conn.close()
                return jsonify({
                    'erro': f'Este e-mail já está no usuário "{repetido["usuario"]}". '
                            f'Cada pessoa precisa do seu próprio.'
                }), 400
            # Trocar o e-mail invalida qualquer link de recuperação em aberto:
            # o link antigo foi para o endereço antigo.
            cursor.execute(
                'UPDATE admin SET email = ?, reset_token_hash = NULL, '
                'reset_expira = NULL WHERE id = ?', (email, usuario_id))

        conn.commit()
        conn.close()

        return jsonify({'mensagem': f"Usuário {alvo['usuario']} atualizado"}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== PREÇOS E DESCONTOS (ADMIN) ====================

@app.route('/api/admin/produtos', methods=['GET'])
@exige_admin
def admin_listar_produtos():
    """Lista todos os produtos com preço, desconto e limite — para a tela de administrador."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, tipo, preco_atual, preco_custo, margem_minima,
                   unidade, icone, ativo,
                   desconto_valor, desconto_tipo, limite_litros, data_atualizacao
            FROM produtos
            ORDER BY tipo, id
        ''')
        produtos = cursor.fetchall()
        conn.close()

        lista = []
        for p in produtos:
            preco = p['preco_atual'] or 0
            custo = p['preco_custo'] or 0
            margem_min = p['margem_minima'] if p['margem_minima'] is not None else 10
            desconto = p['desconto_valor'] or 0
            por_unidade = desconto_por_unidade(preco, desconto, p['desconto_tipo'])
            preco_final = preco - por_unidade

            lista.append({
                'id': p['id'],
                'nome': p['nome'],
                'tipo': p['tipo'],
                'icone': p['icone'],
                'unidade': p['unidade'],
                'ativo': p['ativo'],
                'preco_custo': round(custo, 2),
                'margem_minima': margem_min,
                'preco_atual': round(preco, 2),
                'desconto_valor': round(desconto, 2),
                'desconto_tipo': p['desconto_tipo'] or 'fixo',
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco_final, 2),
                'margem_reais': round(preco_final - custo, 2) if custo else None,
                'margem_percentual': round(((preco_final - custo) / custo) * 100, 1) if custo else None,
                # piso que a Gerência precisa respeitar
                'preco_minimo_gerencia': round(custo * (1 + margem_min / 100), 2) if custo else None,
                'limite_litros': p['limite_litros'] or 0,
                'data_atualizacao': p['data_atualizacao']
            })

        return jsonify({'produtos': lista, 'meu_nivel': request.admin['nivel']}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/produtos/atualizar', methods=['POST'])
@exige_gerencia
def admin_atualizar_produtos():
    """Atualiza preço, desconto e limite de um ou vários produtos de uma vez.

    Espera: {"produtos": [{"id": 1, "preco_atual": 5.89, "desconto_valor": 0.30,
                           "desconto_tipo": "fixo", "limite_litros": 50, "ativo": 1}, ...]}
    """
    try:
        data = request.get_json()
        produtos = data.get('produtos') or []

        if not produtos:
            return jsonify({'erro': 'Nenhum produto enviado'}), 400

        conn = get_db()
        cursor = conn.cursor()
        momento = agora().strftime('%Y-%m-%d %H:%M:%S')
        atualizados = []

        nivel = request.admin['nivel']
        mudancas = []   # guarda o que mudou para gravar na auditoria depois do commit

        # ---- conferência de variação brusca (antes de gravar qualquer coisa) ----
        #
        # O preço de custo é o número que sustenta TODAS as travas de margem: se
        # ele entra errado para baixo, a trava afrouxa e passa a liberar desconto
        # que dá prejuízo. Combustível não varia 10% de um dia para o outro, então
        # salto maior que isso é dedo errado (um zero a menos), não mercado.
        #
        # Não bloqueia: pede confirmação. O app reenvia com confirma_variacao=true
        # depois que o Master olhar e concordar.
        confirmacoes = _conferir_variacao_precos(cursor, produtos)
        if confirmacoes:
            conn.close()
            return jsonify({
                'erro': 'variacao_alta',
                'confirmar': confirmacoes,
                'mensagem': 'Variação fora do normal. Confira antes de salvar.'
            }), 409

        for p in produtos:
            produto_id = p.get('id')
            if not produto_id:
                continue

            cursor.execute('''
                SELECT id, nome, preco_atual, preco_custo, margem_minima,
                       desconto_valor, desconto_tipo, limite_litros, ativo
                FROM produtos WHERE id = ?
            ''', (produto_id,))
            atual = cursor.fetchone()
            if not atual:
                continue

            nome = (p.get('nome') or atual['nome']).strip()
            if len(nome) < 2:
                conn.close()
                return jsonify({'erro': 'O nome do produto não pode ficar vazio'}), 400

            preco = float(p.get('preco_atual', atual['preco_atual']))
            desconto = float(p.get('desconto_valor', atual['desconto_valor'] or 0))
            tipo = p.get('desconto_tipo', atual['desconto_tipo'] or 'fixo')
            limite = float(p.get('limite_litros', atual['limite_litros'] or 50))
            ativo = int(p.get('ativo', atual['ativo'] if atual['ativo'] is not None else 1))

            # custo e margem mínima: somente o Master altera
            custo_atual = atual['preco_custo'] or 0
            margem_atual = atual['margem_minima'] if atual['margem_minima'] is not None else 10

            if nivel == 'master':
                custo = float(p.get('preco_custo', custo_atual))
                margem = float(p.get('margem_minima', margem_atual))
            else:
                custo, margem = custo_atual, margem_atual
                if ('preco_custo' in p and float(p['preco_custo']) != custo_atual) or \
                   ('margem_minima' in p and float(p['margem_minima']) != margem_atual):
                    conn.close()
                    return jsonify({
                        'erro': 'Somente o administrador Master pode alterar preço de custo e margem mínima.'
                    }), 403

            if preco < 0 or desconto < 0 or limite < 0 or custo < 0 or margem < 0:
                conn.close()
                return jsonify({'erro': f'Valores negativos não são permitidos ({atual["nome"]})'}), 400

            if tipo not in ('fixo', 'percentual'):
                conn.close()
                return jsonify({'erro': "Tipo de desconto deve ser 'fixo' ou 'percentual'"}), 400

            if tipo == 'percentual' and desconto > 100:
                conn.close()
                return jsonify({'erro': f'{nome}: desconto percentual não pode passar de 100%'}), 400

            por_unidade = desconto_por_unidade(preco, desconto, tipo)

            # ===== TRAVA DE MARGEM =====
            erro = validar_margem(nivel, nome, preco, custo, margem, por_unidade)
            if erro:
                registrar_auditoria(cursor, request.admin, 'BLOQUEIO', produto_id, nome,
                                    'desconto', atual['desconto_valor'], desconto, erro)
                conn.commit()
                conn.close()
                return jsonify({'erro': erro, 'bloqueado': True}), 400

            # o que mudou de fato
            for campo, antes, depois in [
                ('nome', atual['nome'], nome),
                ('preco_atual', atual['preco_atual'], preco),
                ('preco_custo', custo_atual, custo),
                ('margem_minima', margem_atual, margem),
                ('desconto_valor', atual['desconto_valor'] or 0, desconto),
                ('desconto_tipo', atual['desconto_tipo'] or 'fixo', tipo),
                ('limite_litros', atual['limite_litros'] or 0, limite),
                ('ativo', atual['ativo'], ativo),
            ]:
                if str(antes) != str(depois):
                    mudancas.append((produto_id, nome, campo, antes, depois))

            cursor.execute('''
                UPDATE produtos
                SET nome = ?, preco_atual = ?, preco_custo = ?, margem_minima = ?,
                    desconto_valor = ?, desconto_tipo = ?, limite_litros = ?,
                    ativo = ?, data_atualizacao = ?
                WHERE id = ?
            ''', (nome, preco, custo, margem, desconto, tipo, limite, ativo, momento, produto_id))

            preco_final = preco - por_unidade
            atualizados.append({
                'id': produto_id,
                'nome': nome,
                'preco_atual': round(preco, 2),
                'preco_custo': round(custo, 2),
                'desconto_por_unidade': round(por_unidade, 2),
                'preco_final': round(preco_final, 2),
                'margem_reais': round(preco_final - custo, 2) if custo else None,
                'margem_percentual': round(((preco_final - custo) / custo) * 100, 1) if custo else None
            })

        for produto_id, nome, campo, antes, depois in mudancas:
            registrar_auditoria(cursor, request.admin, 'ALTERACAO', produto_id, nome,
                                campo, antes, depois)

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': f'{len(atualizados)} produto(s) atualizado(s)',
            'produtos': atualizados,
            'alteracoes_registradas': len(mudancas)
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== AUDITORIA ====================

@app.route('/api/admin/auditoria', methods=['GET'])
@exige_admin
def admin_auditoria():
    """Histórico de alterações de preço e desconto.

    Parâmetros: data_inicio, data_fim, usuario, acao (ALTERACAO/BLOQUEIO), limite
    """
    try:
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        usuario = request.args.get('usuario')
        acao = request.args.get('acao')
        limite = min(int(request.args.get('limite', 200)), 1000)

        conn = get_db()
        cursor = conn.cursor()

        query = 'SELECT * FROM auditoria WHERE 1=1'
        params = []

        if data_inicio:
            query += ' AND data_hora >= ?'
            params.append(f'{data_inicio} 00:00:00')

        if data_fim:
            query += ' AND data_hora <= ?'
            params.append(f'{data_fim} 23:59:59')

        if usuario:
            query += ' AND admin_usuario = ?'
            params.append(usuario)

        if acao:
            query += ' AND acao = ?'
            params.append(acao)

        query += ' ORDER BY id DESC'
        cursor.execute(query, params)
        registros = cursor.fetchall()[:limite]
        conn.close()

        rotulos = {
            'nome': 'Nome',
            'preco_atual': 'Preço de venda',
            'preco_custo': 'Preço de custo',
            'margem_minima': 'Margem mínima (%)',
            'desconto_valor': 'Desconto',
            'desconto_tipo': 'Tipo de desconto',
            'limite_litros': 'Limite',
            'ativo': 'Situação'
        }

        return jsonify({'registros': [{
            'id': r['id'],
            'data_hora': r['data_hora'],
            'usuario': r['admin_usuario'],
            'nivel': r['admin_nivel'],
            'acao': r['acao'],
            'produto': r['produto_nome'],
            'campo': r['campo'],
            'campo_rotulo': rotulos.get(r['campo'], r['campo']),
            'valor_anterior': r['valor_anterior'],
            'valor_novo': r['valor_novo'],
            'detalhe': r['detalhe']
        } for r in registros]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== FECHAMENTO DE CAIXA ====================

@app.route('/api/admin/caixa', methods=['GET'])
@exige_admin
def admin_fechamento_caixa():
    """Fechamento de caixa: abastecimentos, litros e R$ por turno.

    Parâmetros: data (YYYY-MM-DD, padrão hoje), turno (opcional), poster_id (opcional)
    """
    try:
        data_ref = request.args.get('data') or agora().strftime('%Y-%m-%d')
        turno_filtro = request.args.get('turno')
        poster_id = request.args.get('poster_id')
        hora_inicio = request.args.get('hora_inicio')   # ex: 14:00
        hora_fim = request.args.get('hora_fim')         # ex: 18:00
        produto_filtro = request.args.get('produto_id')

        def normaliza_hora(h):
            if not h:
                return None
            h = h.strip()
            return h if len(h) == 8 else f'{h}:00'

        hora_inicio = normaliza_hora(hora_inicio)
        hora_fim = normaliza_hora(hora_fim)

        conn = get_db()
        cursor = conn.cursor()

        query = '''
            SELECT a.*, p.nome AS produto_nome, p.unidade, p.icone, c.nome AS cliente_nome
            FROM abastecimentos a
            LEFT JOIN produtos p ON p.id = a.produto_id
            LEFT JOIN clientes c ON c.id = a.cliente_id
            WHERE a.data = ?
        '''
        params = [data_ref]

        if turno_filtro:
            query += ' AND a.turno = ?'
            params.append(turno_filtro)

        if poster_id:
            query += ' AND a.poster_id = ?'
            params.append(poster_id)

        if produto_filtro:
            query += ' AND a.produto_id = ?'
            params.append(int(produto_filtro))

        if hora_inicio:
            query += ' AND a.hora >= ?'
            params.append(hora_inicio)

        if hora_fim:
            query += ' AND a.hora <= ?'
            params.append(hora_fim)

        query += ' ORDER BY a.hora'
        cursor.execute(query, params)
        registros = cursor.fetchall()
        conn.close()

        turnos = {}
        for r in registros:
            turno = r['turno'] or 'Sem turno'
            t = turnos.setdefault(turno, {
                'turno': turno,
                'abastecimentos': 0,
                'litros': 0.0,
                'valor_bruto': 0.0,
                'desconto_concedido': 0.0,
                'valor_recebido': 0.0,
                'por_produto': {},
                'por_posto': {}
            })

            t['abastecimentos'] += 1
            t['litros'] += r['quantidade'] or 0
            t['valor_bruto'] += r['valor_original'] or 0
            t['desconto_concedido'] += r['valor_desconto'] or 0
            t['valor_recebido'] += r['valor_final'] or 0

            prod = r['produto_nome'] or f"Produto {r['produto_id']}"
            pp = t['por_produto'].setdefault(prod, {
                'produto': prod,
                'icone': r['icone'],
                'unidade': r['unidade'] or 'L',
                'abastecimentos': 0,
                'litros': 0.0,
                'valor_recebido': 0.0
            })
            pp['abastecimentos'] += 1
            pp['litros'] += r['quantidade'] or 0
            pp['valor_recebido'] += r['valor_final'] or 0

            posto = r['poster_id'] or 'N/A'
            ps = t['por_posto'].setdefault(posto, {
                'posto': posto, 'abastecimentos': 0, 'litros': 0.0, 'valor_recebido': 0.0
            })
            ps['abastecimentos'] += 1
            ps['litros'] += r['quantidade'] or 0
            ps['valor_recebido'] += r['valor_final'] or 0

        def arredonda(d, campos):
            for c in campos:
                d[c] = round(d[c], 2)
            return d

        lista_turnos = []
        for t in turnos.values():
            t['por_produto'] = [arredonda(x, ['litros', 'valor_recebido']) for x in t['por_produto'].values()]
            t['por_posto'] = [arredonda(x, ['litros', 'valor_recebido']) for x in t['por_posto'].values()]
            lista_turnos.append(arredonda(t, ['litros', 'valor_bruto', 'desconto_concedido', 'valor_recebido']))

        lista_turnos.sort(key=lambda x: x['turno'])

        total = {
            'abastecimentos': sum(t['abastecimentos'] for t in lista_turnos),
            'litros': round(sum(t['litros'] for t in lista_turnos), 2),
            'valor_bruto': round(sum(t['valor_bruto'] for t in lista_turnos), 2),
            'desconto_concedido': round(sum(t['desconto_concedido'] for t in lista_turnos), 2),
            'valor_recebido': round(sum(t['valor_recebido'] for t in lista_turnos), 2),
        }

        return jsonify({
            'data': data_ref,
            'turno_atual': obter_turno(),
            'filtros': {
                'hora_inicio': hora_inicio,
                'hora_fim': hora_fim,
                'poster_id': poster_id,
                'turno': turno_filtro,
                'produto_id': produto_filtro
            },
            'total': total,
            'turnos': lista_turnos,
            'detalhes': [{
                'hora': r['hora'],
                'turno': r['turno'],
                'posto': r['poster_id'],
                'cliente': r['cliente_nome'],
                'produto': r['produto_nome'],
                'quantidade': r['quantidade'],
                'valor_original': r['valor_original'],
                'valor_desconto': r['valor_desconto'],
                'valor_final': r['valor_final']
            } for r in registros]
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== ROTAS DE ADMIN ====================

@app.route('/api/admin/descontos', methods=['GET'])
@exige_admin
def get_descontos_ocupacoes():
    """Obtém descontos por ocupação"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT ocupacao, desconto_valor
            FROM clientes
            WHERE ocupacao IS NOT NULL
            ORDER BY ocupacao
        ''')
        descontos = cursor.fetchall()
        conn.close()

        return jsonify({
            'descontos_por_ocupacao': [dict(row) for row in descontos]
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/descontos/atualizar', methods=['POST'])
@exige_master
def atualizar_descontos():
    """Atualiza desconto para todos os clientes de uma ocupação"""
    try:
        data = request.get_json()
        ocupacao = data.get('ocupacao')  # 'Táxi', 'Uber', 'Outro'
        novo_valor = float(data.get('valor'))  # R$ 1.00

        if not ocupacao or novo_valor < 0:
            return jsonify({'erro': 'Ocupação e valor inválidos'}), 400

        conn = get_db()
        cursor = conn.cursor()

        # O desconto por ocupação vale para TODO produto que não tenha desconto
        # próprio — então precisa passar pela mesma trava de margem da tela de
        # preços. Sem isto, um 5,00 digitado no lugar de 1,00 saía vendendo
        # abaixo do custo para todos os motoristas daquela ocupação de uma vez,
        # sem nada reclamar em lugar nenhum.
        # "Não tem desconto próprio" é só desconto_valor NULO — um produto com
        # desconto explícito em zero (ex: Gasolina Premium) tem desconto
        # próprio de propósito e não herda mais o da ocupação (ver correção de
        # 24/08: zero configurado passou a valer como zero de verdade). Antes,
        # o `<= 0` aqui tratava esse zero como "sem desconto próprio" também,
        # e o validava contra uma herança que na prática já não acontece mais.
        cursor.execute('''
            SELECT nome, preco_atual, preco_custo
            FROM produtos
            WHERE ativo = 1 AND desconto_valor IS NULL
        ''')
        impedimentos = []
        for prod in cursor.fetchall():
            erro_margem = validar_margem(
                'master', prod['nome'], prod['preco_atual'] or 0,
                prod['preco_custo'] or 0, 0, novo_valor
            )
            if erro_margem:
                impedimentos.append(erro_margem)

        if impedimentos:
            conn.close()
            return jsonify({
                'erro': f'Desconto de R$ {novo_valor:.2f} recusado — deixaria produto '
                        f'abaixo do custo:\n• ' + '\n• '.join(impedimentos),
                'produtos_afetados': len(impedimentos)
            }), 400

        cursor.execute('''
            UPDATE clientes
            SET desconto_valor = ?, desconto_tipo = 'fixo'
            WHERE ocupacao = ?
        ''', (novo_valor, ocupacao))

        registrar_auditoria(
            cursor, request.admin, 'desconto_ocupacao',
            campo=f'desconto {ocupacao}', valor_novo=f'R$ {novo_valor:.2f}',
            detalhe=f'Aplicado a todos os clientes da ocupação {ocupacao}'
        )

        conn.commit()
        clientes_atualizados = cursor.rowcount
        conn.close()

        return jsonify({
            'mensagem': f'Desconto atualizado para {clientes_atualizados} clientes de {ocupacao}',
            'ocupacao': ocupacao,
            'novo_valor': novo_valor,
            'clientes_atualizados': clientes_atualizados
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/admin/suspeitas', methods=['GET'])
@exige_gerencia
def admin_suspeitas():
    """Padrões que merecem um olhar — não são acusações, são pistas.

    A fraude que a foto do comprovante não pega é a de dentro: frentista que
    cadastra amigos e libera desconto para eles. Isso não aparece num
    abastecimento isolado, só no padrão ao longo dos dias.
    """
    try:
        dias = int(request.args.get('dias', 30))
        limite = (agora() - timedelta(days=dias)).strftime('%Y-%m-%d')

        conn = get_db()
        cursor = conn.cursor()
        achados = {}

        # 1. Mesma placa em vários cadastros.
        # Táxi dividido por turno é legítimo; três contas no mesmo carro, nem tanto.
        cursor.execute('''
            SELECT placa, COUNT(*) AS qtd
            FROM clientes
            WHERE placa IS NOT NULL AND placa <> ''
            GROUP BY placa
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        ''')
        placas = []
        for linha in cursor.fetchall():
            cursor.execute('''
                SELECT id, nome, ocupacao, data_criacao
                FROM clientes WHERE placa = ? ORDER BY id
            ''', (linha['placa'],))
            placas.append({
                'placa': linha['placa'],
                'quantidade': linha['qtd'],
                'clientes': [
                    {'id': c['id'], 'nome': c['nome'], 'ocupacao': c['ocupacao'],
                     'cadastrado_em': str(c['data_criacao'])[:10]}
                    for c in cursor.fetchall()
                ]
            })
        achados['placas_repetidas'] = placas

        # 2. Motorista que só abastece com um frentista específico.
        # Quem abastece de verdade pega turnos diferentes; quem tem combinado, não.
        cursor.execute('''
            SELECT a.cliente_id, cl.nome AS cliente_nome, cl.placa,
                   COUNT(*) AS total,
                   COUNT(DISTINCT a.registrado_por) AS operadores,
                   MIN(a.registrado_por) AS operador
            FROM abastecimentos a
            JOIN clientes cl ON cl.id = a.cliente_id
            WHERE a.data >= ? AND a.registrado_por IS NOT NULL
            GROUP BY a.cliente_id, cl.nome, cl.placa
            HAVING COUNT(*) >= 5 AND COUNT(DISTINCT a.registrado_por) = 1
            ORDER BY COUNT(*) DESC
        ''', (limite,))
        achados['sempre_mesmo_frentista'] = [
            {'cliente_id': l['cliente_id'], 'cliente_nome': l['cliente_nome'],
             'placa': l['placa'], 'abastecimentos': l['total'], 'frentista': l['operador']}
            for l in cursor.fetchall()
        ]

        # 3. Cadastros em rajada — vários no mesmo dia costuma ser mutirão de amigos
        cursor.execute('''
            SELECT substr(CAST(data_criacao AS VARCHAR), 1, 10) AS dia, COUNT(*) AS qtd
            FROM clientes
            WHERE substr(CAST(data_criacao AS VARCHAR), 1, 10) >= ?
            GROUP BY substr(CAST(data_criacao AS VARCHAR), 1, 10)
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC
        ''', (limite,))
        achados['cadastros_em_rajada'] = [
            {'dia': l['dia'], 'quantidade': l['qtd']} for l in cursor.fetchall()
        ]

        # 4. Quem abastece com desconto quase todo dia
        cursor.execute('''
            SELECT a.cliente_id, cl.nome AS cliente_nome, cl.placa, cl.ocupacao,
                   COUNT(DISTINCT a.data) AS dias,
                   SUM(a.quantidade) AS litros,
                   SUM(a.valor_desconto) AS desconto
            FROM abastecimentos a
            JOIN clientes cl ON cl.id = a.cliente_id
            WHERE a.data >= ?
            GROUP BY a.cliente_id, cl.nome, cl.placa, cl.ocupacao
            ORDER BY SUM(a.valor_desconto) DESC
        ''', (limite,))
        campeoes = []
        for l in cursor.fetchall()[:15]:
            campeoes.append({
                'cliente_id': l['cliente_id'], 'cliente_nome': l['cliente_nome'],
                'placa': l['placa'], 'ocupacao': l['ocupacao'],
                'dias_com_abastecimento': l['dias'],
                'litros': round(l['litros'] or 0, 2),
                'desconto_total': round(l['desconto'] or 0, 2)
            })
        achados['maiores_beneficiados'] = campeoes

        # 5. Trocas de placa — o movimento típico de conta emprestada
        cursor.execute('''
            SELECT data_hora, admin_usuario, valor_anterior, valor_novo
            FROM auditoria
            WHERE acao = 'troca_placa' AND data_hora >= ?
            ORDER BY data_hora DESC
        ''', (limite,))
        trocas = [
            {'quando': l['data_hora'], 'cliente': l['admin_usuario'],
             'de': l['valor_anterior'], 'para': l['valor_novo']}
            for l in cursor.fetchall()
        ]
        achados['trocas_de_placa'] = trocas[:50]

        conn.close()

        achados['periodo_dias'] = dias
        achados['total_alertas'] = (
            len(achados['placas_repetidas'])
            + len(achados['sempre_mesmo_frentista'])
            + len(achados['cadastros_em_rajada'])
        )
        return jsonify(achados), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cliente/<int:cliente_id>/comprovante', methods=['GET'])
@exige_gerencia
def admin_comprovante(cliente_id):
    """Devolve o comprovante enviado no cadastro, para conferência manual."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT nome, ocupacao, placa, registro_tipo, registro_numero,
                   empresa_convenio, foto_comprovante, foto_comprovante_tipo,
                   data_foto_comprovante
            FROM clientes WHERE id = ?
        ''', (cliente_id,))
        c = cursor.fetchone()
        conn.close()

        if not c:
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        return jsonify({
            'nome': c['nome'],
            'ocupacao': c['ocupacao'],
            'placa': c['placa'],
            'registro_tipo': c['registro_tipo'],
            'registro_numero': c['registro_numero'],
            'empresa_convenio': c['empresa_convenio'],
            'tipo_comprovante': c['foto_comprovante_tipo'],
            'enviado_em': c['data_foto_comprovante'],
            'imagem': c['foto_comprovante']
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cupons-do-dia', methods=['GET'])
@exige_admin
def admin_cupons_do_dia():
    """
    Todo o movimento de cupons do dia, para a tela que fica aberta no caixa.

    Nível caixa também enxerga: quem opera a bomba precisa ver o que está
    valendo agora. A trava de reuso não está aqui — está no saldo gravado no
    banco, que /api/cupom/usar confere a cada baixa. Esta tela é para
    enxergar o movimento e achar um código depressa.
    """
    try:
        data_ref = (request.args.get('data') or '').strip() or agora().strftime('%Y-%m-%d')

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.id, c.qrcode, c.status, c.data_geracao,
                   c.quantidade_permitida, c.quantidade_utilizada,
                   c.data_ultimo_uso, c.turno_ultimo_uso,
                   c.preco_unitario, c.desconto_unitario,
                   p.nome AS produto_nome, p.icone AS produto_icone, p.unidade,
                   cl.id AS cliente_id, cl.nome AS cliente_nome, cl.placa,
                   cl.ocupacao, cl.empresa_convenio
            FROM cupons c
            LEFT JOIN produtos p ON p.id = c.produto_id
            LEFT JOIN clientes cl ON cl.id = c.cliente_id
            WHERE c.data_geracao = ?
            ORDER BY c.id DESC
        ''', (data_ref,))
        linhas = cursor.fetchall()

        # Onde cada cupom foi abastecido. Um cupom pode ser usado em partes,
        # inclusive nos dois postos — por isso a lista de postos, não um só.
        cursor.execute('''
            SELECT cupom_id, poster_id, COUNT(*) AS vezes,
                   MAX(hora) AS ultima_hora, MAX(registrado_por) AS ultimo_frentista
            FROM abastecimentos
            WHERE data = ? AND cupom_id IS NOT NULL
            GROUP BY cupom_id, poster_id
        ''', (data_ref,))
        por_cupom = {}
        for a in cursor.fetchall():
            registro = por_cupom.setdefault(a['cupom_id'], {'postos': [], 'vezes': 0,
                                                            'ultima_hora': None,
                                                            'ultimo_frentista': None})
            registro['postos'].append(a['poster_id'])
            registro['vezes'] += a['vezes'] or 0
            if not registro['ultima_hora'] or (a['ultima_hora'] or '') > registro['ultima_hora']:
                registro['ultima_hora'] = a['ultima_hora']
                registro['ultimo_frentista'] = a['ultimo_frentista']
        conn.close()

        cupons = []
        total_emitidos = total_parciais = total_esgotados = 0
        litros_abastecidos = desconto_concedido = 0.0

        for c in linhas:
            permitida = c['quantidade_permitida'] or 0
            utilizada = c['quantidade_utilizada'] or 0
            restante = round(permitida - utilizada, 2)
            desconto_unit = c['desconto_unitario'] or 0
            uso = por_cupom.get(c['id'], {})

            situacao = (c['status'] or 'pendente').lower()
            if situacao == 'completo' or restante <= 0:
                rotulo, total_esgotados = 'esgotado', total_esgotados + 1
                # Com uso único o cupom fecha sobrando litros. Mostrar "saldo
                # 30 L" num cupom encerrado faria o caixa tentar uma baixa que
                # o servidor vai recusar.
                restante = 0
            elif utilizada > 0:
                rotulo, total_parciais = 'parcial', total_parciais + 1
            else:
                rotulo, total_emitidos = 'emitido', total_emitidos + 1

            litros_abastecidos += utilizada
            desconto_concedido += utilizada * desconto_unit

            cupons.append({
                'cupom_id': c['id'],
                'codigo': c['qrcode'],
                'situacao': rotulo,
                'cliente_id': c['cliente_id'],
                'cliente_nome': c['cliente_nome'],
                'placa': c['placa'],
                'ocupacao': c['ocupacao'],
                'empresa_convenio': c['empresa_convenio'],
                'produto_nome': c['produto_nome'],
                'produto_icone': c['produto_icone'],
                'unidade': c['unidade'] or 'L',
                'quantidade_permitida': round(permitida, 2),
                'quantidade_utilizada': round(utilizada, 2),
                'quantidade_restante': restante,
                'preco_unitario': round(c['preco_unitario'] or 0, 2),
                'desconto_por_unidade': round(desconto_unit, 2),
                'economia_ate_agora': round(utilizada * desconto_unit, 2),
                'postos': sorted(set(p for p in uso.get('postos', []) if p)),
                'vezes_abastecido': uso.get('vezes', 0),
                'ultima_hora': uso.get('ultima_hora'),
                'ultimo_frentista': uso.get('ultimo_frentista')
            })

        return jsonify({
            'data': data_ref,
            'cupons': cupons,
            'resumo': {
                'total': len(cupons),
                'emitidos': total_emitidos,
                'parciais': total_parciais,
                'esgotados': total_esgotados,
                'litros_abastecidos': round(litros_abastecidos, 2),
                'desconto_concedido': round(desconto_concedido, 2)
            }
        }), 200

    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==========================================================================
# CONVÊNIO COM EMPRESAS — lista fechada + alçada de liberação
# ==========================================================================

@app.route('/api/empresas-convenio', methods=['GET'])
def listar_empresas_convenio_publico():
    """
    Lista para o menu do cadastro. Público de propósito — quem vai se
    cadastrar ainda não tem login.

    Devolve só id e nome: CNPJ, domínio e limite são informação interna e não
    servem de nada para quem está preenchendo o formulário.
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, nome FROM empresas_convenio WHERE ativo = 1 ORDER BY nome'
        )
        empresas = [{'id': e['id'], 'nome': e['nome']} for e in cursor.fetchall()]
        conn.close()
        return jsonify({'empresas': empresas}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio', methods=['GET'])
@exige_gerencia
def admin_listar_empresas_convenio():
    """Empresas conveniadas, com quantos funcionários já se cadastraram."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT e.id, e.nome, e.cnpj, e.dominio_email, e.limite_funcionarios,
                   e.ativo, e.observacao, e.criado_por, e.data_criacao,
                   (SELECT COUNT(*) FROM clientes c
                     WHERE c.empresa_convenio_id = e.id AND c.status = 'ativo') AS aprovados,
                   (SELECT COUNT(*) FROM clientes c
                     WHERE c.empresa_convenio_id = e.id AND c.status = 'pendente') AS pendentes
            FROM empresas_convenio e
            ORDER BY e.ativo DESC, e.nome
        ''')
        empresas = []
        for e in cursor.fetchall():
            empresas.append({
                'id': e['id'],
                'nome': e['nome'],
                'cnpj': formatar_cnpj(e['cnpj']),
                'dominio_email': e['dominio_email'],
                'limite_funcionarios': e['limite_funcionarios'] or 0,
                'ativo': bool(e['ativo']),
                'observacao': e['observacao'],
                'criado_por': e['criado_por'],
                'data_criacao': e['data_criacao'],
                'aprovados': e['aprovados'] or 0,
                'pendentes': e['pendentes'] or 0
            })
        conn.close()
        return jsonify({'empresas': empresas}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio', methods=['POST'])
@exige_gerencia
def admin_criar_empresa_convenio():
    """Cadastra uma empresa que assinou convênio."""
    try:
        data = request.get_json() or {}
        nome = (data.get('nome') or '').strip()
        cnpj = normalizar_cnpj(data.get('cnpj'))
        dominio = (data.get('dominio_email') or '').strip().lower().lstrip('@') or None

        try:
            limite = int(data.get('limite_funcionarios') or 0)
        except (TypeError, ValueError):
            limite = 0
        if limite < 0:
            limite = 0

        if len(nome) < 3:
            return jsonify({'erro': 'Informe o nome da empresa.'}), 400
        if not validar_cnpj(cnpj):
            return jsonify({'erro': 'CNPJ inválido. Confira os números.'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome FROM empresas_convenio WHERE cnpj = ?', (cnpj,))
        ja = cursor.fetchone()
        if ja:
            conn.close()
            return jsonify({'erro': f'Esse CNPJ já está cadastrado como "{ja["nome"]}".'}), 400

        cursor.execute('''
            INSERT INTO empresas_convenio
            (nome, cnpj, dominio_email, limite_funcionarios, ativo, observacao, criado_por)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (nome, cnpj, dominio, limite,
              (data.get('observacao') or '').strip() or None,
              request.admin['usuario']))

        empresa_id = cursor.lastrowid
        registrar_auditoria(
            cursor, request.admin, 'convenio_empresa_criada',
            campo='empresa', valor_novo=nome,
            detalhe=f'CNPJ {formatar_cnpj(cnpj)} | limite {limite or "sem limite"}'
        )
        conn.commit()
        conn.close()

        return jsonify({'mensagem': f'Convênio da {nome} cadastrado.',
                        'empresa_id': empresa_id}), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/empresas-convenio/<int:empresa_id>', methods=['POST'])
@exige_gerencia
def admin_alterar_empresa_convenio(empresa_id):
    """Ativa/desativa o convênio ou ajusta limite e domínio."""
    try:
        data = request.get_json() or {}
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, ativo FROM empresas_convenio WHERE id = ?',
                       (empresa_id,))
        empresa = cursor.fetchone()
        if not empresa:
            conn.close()
            return jsonify({'erro': 'Empresa não encontrada'}), 404

        if 'ativo' in data:
            novo = 1 if data['ativo'] in (True, 1, '1', 'true') else 0
            cursor.execute('UPDATE empresas_convenio SET ativo = ? WHERE id = ?',
                           (novo, empresa_id))
            registrar_auditoria(
                cursor, request.admin,
                'convenio_ativado' if novo else 'convenio_encerrado',
                campo='empresa', valor_anterior=empresa['nome'],
                detalhe='Convênio ' + ('reativado' if novo else 'encerrado')
            )

        if 'limite_funcionarios' in data:
            try:
                limite = max(0, int(data['limite_funcionarios'] or 0))
            except (TypeError, ValueError):
                limite = 0
            cursor.execute(
                'UPDATE empresas_convenio SET limite_funcionarios = ? WHERE id = ?',
                (limite, empresa_id))

        if 'dominio_email' in data:
            dom = (data['dominio_email'] or '').strip().lower().lstrip('@') or None
            cursor.execute('UPDATE empresas_convenio SET dominio_email = ? WHERE id = ?',
                           (dom, empresa_id))

        conn.commit()
        conn.close()
        return jsonify({'mensagem': 'Convênio atualizado.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== PROGRAMA DE INDICAÇÃO ====================
#
# "Traga um amigo, ganhe um cupom." Cada cliente tem um código próprio
# (ver clientes.codigo_indicacao) para compartilhar num link. A cada N
# indicações que viraram cliente DE VERDADE — gerou cupom E abasteceu, não
# só se cadastrou — quem indicou ganha um cupom-prêmio. N e o valor do
# prêmio são configuráveis pelo Master (tabela config_indicacoes).
#
# A contagem em si acontece em usar_cupom() (é lá que se sabe que o
# indicado realmente abasteceu) via _processar_indicacao_positiva(). As
# rotas aqui embaixo só leem o que já foi contado e deixam o Master ajustar
# a configuração.

@app.route('/api/indicacao/verificar', methods=['GET'])
def verificar_codigo_indicacao():
    """
    Confere se um código de indicação existe — usado na tela de cadastro
    para mostrar "Fulano te indicou!" quando alguém abre um link de
    indicação. Público de propósito, mas devolve só o primeiro nome: é uma
    tela que qualquer um pode abrir sem estar logado.
    """
    try:
        codigo = re.sub(r'[^A-Za-z0-9]', '', str(request.args.get('codigo') or '')).upper()
        if not codigo:
            return jsonify({'valido': False}), 200

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT nome FROM clientes WHERE UPPER(codigo_indicacao) = ?',
            (codigo,)
        )
        indicador = cursor.fetchone()
        conn.close()

        if not indicador:
            return jsonify({'valido': False}), 200

        primeiro_nome = (indicador['nome'] or '').strip().split(' ')[0]
        return jsonify({'valido': True, 'nome': primeiro_nome}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/cliente/<int:cliente_id>/indicacao', methods=['GET'])
def minha_indicacao(cliente_id):
    """
    Tela "Indique e Ganhe" do cliente: o código dele, quantas indicações já
    viraram cliente de verdade, quanto falta para o próximo prêmio e se já
    tem algum prêmio pronto para usar no próximo cupom.
    """
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, nome, codigo_indicacao FROM clientes WHERE id = ?',
                       (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        codigo_indicacao = cliente['codigo_indicacao']
        if not codigo_indicacao:
            codigo_indicacao = f'CJ{cliente_id}'
            cursor.execute('UPDATE clientes SET codigo_indicacao = ? WHERE id = ?',
                           (codigo_indicacao, cliente_id))
            conn.commit()

        cursor.execute('''
            SELECT COUNT(*) AS n FROM clientes
            WHERE indicado_por_id = ? AND indicacao_positiva_contada = 1
        ''', (cliente_id,))
        total_positivas = (cursor.fetchone()['n'] or 0)

        cursor.execute('''
            SELECT COUNT(*) AS n FROM clientes WHERE indicado_por_id = ?
        ''', (cliente_id,))
        total_cadastros = (cursor.fetchone()['n'] or 0)

        expirar_premios_vencidos(cursor, cliente_id)

        cursor.execute('''
            SELECT COUNT(*) AS n, COALESCE(SUM(valor), 0) AS soma,
                   MIN(validade) AS proxima_validade
            FROM recompensas_indicacao WHERE cliente_id = ? AND status = 'disponivel'
        ''', (cliente_id,))
        disponiveis = cursor.fetchone()

        config = ler_config_indicacoes(cursor)
        conn.commit()
        conn.close()

        meta = (config['meta_indicacoes'] if config else 3) or 3
        valor_recompensa = round((config['valor_recompensa'] if config else 10) or 0, 2)
        encerrada = campanha_indicacao_encerrada(config)
        programa_ativo = (bool(config['ativo']) if config else True) and not encerrada

        faltam = meta - (total_positivas % meta) if programa_ativo else 0
        if faltam == meta:
            faltam = 0  # acabou de completar um ciclo — a próxima meta é do zero

        return jsonify({
            'codigo_indicacao': codigo_indicacao,
            'programa_ativo': programa_ativo,
            'campanha_encerrada': encerrada,
            'data_fim_campanha': (config['data_fim_campanha'] if config else None) or None,
            'meta_indicacoes': meta,
            'valor_recompensa': valor_recompensa,
            'minimo_litros_combustivel': minimo_litros_da_categoria(config, 'combustivel'),
            'minimo_litros_oleo': minimo_litros_da_categoria(config, 'oleo'),
            'validade_indicacao_dias': (config['validade_indicacao_dias'] if config else 90),
            'total_cadastros_indicados': total_cadastros,
            'total_indicacoes_positivas': total_positivas,
            'faltam_para_o_proximo_premio': faltam,
            'premios_disponiveis': disponiveis['n'] or 0,
            'valor_premios_disponiveis': round(disponiveis['soma'] or 0, 2),
            # Até quando o prêmio mais antigo em mãos vale — é o que o cliente
            # precisa ver para não deixar vencer.
            'premio_vence_em': disponiveis['proxima_validade']
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/indicacoes/config', methods=['GET'])
@exige_admin
def admin_config_indicacoes():
    """Configuração atual do programa — qualquer usuário do painel pode ver."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        config = ler_config_indicacoes(cursor)
        conn.close()
        return jsonify({
            'meta_indicacoes': (config['meta_indicacoes'] if config else 3) or 3,
            'valor_recompensa': round((config['valor_recompensa'] if config else 10) or 0, 2),
            'minimo_litros_combustivel': minimo_litros_da_categoria(config, 'combustivel'),
            'minimo_litros_oleo': minimo_litros_da_categoria(config, 'oleo'),
            'data_fim_campanha': (config['data_fim_campanha'] if config else None) or '',
            'validade_premio_dias': (config['validade_premio_dias'] if config else 30),
            'validade_indicacao_dias': (config['validade_indicacao_dias'] if config else 90),
            'campanha_encerrada': campanha_indicacao_encerrada(config),
            'ativo': bool(config['ativo']) if config else True
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/indicacoes/config', methods=['POST'])
@exige_master
def admin_atualizar_config_indicacoes():
    """
    Só o Master mexe aqui — é o mesmo motivo de preços e descontos: um valor
    de prêmio errado sai dinheiro do caixa sem ninguém perceber na hora.
    Mudar isto NÃO afeta prêmios já concedidos (o valor fica congelado em
    cada recompensa no momento em que ela nasce).
    """
    try:
        data = request.get_json() or {}

        try:
            meta = int(data.get('meta_indicacoes'))
        except (TypeError, ValueError):
            return jsonify({'erro': 'Informe de quantas em quantas indicações o prêmio nasce.'}), 400
        if meta < 1:
            return jsonify({'erro': 'A meta de indicações precisa ser pelo menos 1.'}), 400

        try:
            valor = float(data.get('valor_recompensa'))
        except (TypeError, ValueError):
            return jsonify({'erro': 'Informe o valor do prêmio.'}), 400
        if valor < 0:
            return jsonify({'erro': 'O valor do prêmio não pode ser negativo.'}), 400

        try:
            min_comb = float(data.get('minimo_litros_combustivel'))
            min_oleo = float(data.get('minimo_litros_oleo'))
        except (TypeError, ValueError):
            return jsonify({
                'erro': 'Informe os litros mínimos de combustível e de óleo.'
            }), 400
        if min_comb < 0 or min_oleo < 0:
            return jsonify({'erro': 'Os litros mínimos não podem ser negativos.'}), 400

        # ---- os três prazos ----
        # Data de fim vazia = campanha sem prazo. É o padrão.
        fim = (data.get('data_fim_campanha') or '').strip()[:10] or None
        if fim:
            try:
                datetime.strptime(fim, '%Y-%m-%d')
            except ValueError:
                return jsonify({
                    'erro': 'Data de encerramento inválida. Use o seletor de data.'
                }), 400

        try:
            val_premio = int(data.get('validade_premio_dias') or 0)
            val_indicacao = int(data.get('validade_indicacao_dias') or 0)
        except (TypeError, ValueError):
            return jsonify({'erro': 'Os prazos em dias precisam ser números inteiros.'}), 400
        if val_premio < 0 or val_indicacao < 0:
            return jsonify({'erro': 'Os prazos em dias não podem ser negativos.'}), 400

        ativo = 1 if data.get('ativo', True) in (True, 1, '1', 'true') else 0

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE config_indicacoes
            SET meta_indicacoes = ?, valor_recompensa = ?,
                minimo_litros_combustivel = ?, minimo_litros_oleo = ?,
                data_fim_campanha = ?, validade_premio_dias = ?,
                validade_indicacao_dias = ?, ativo = ?,
                atualizado_por = ?, data_atualizacao = ?
            WHERE id = 1
        ''', (meta, valor, min_comb, min_oleo, fim, val_premio, val_indicacao,
              ativo, request.admin['usuario'],
              agora().strftime('%Y-%m-%d %H:%M:%S')))

        registrar_auditoria(
            cursor, request.admin, 'config_indicacoes_alterada',
            campo='programa de indicação',
            valor_novo=(f'a cada {meta} indicações, prêmio de R$ {valor:.2f}; '
                        f'mínimo de {min_comb:g} L (combustível) e {min_oleo:g} L (óleo); '
                        f'campanha até {fim or "sem prazo"}; '
                        f'prêmio vale {val_premio or "sem prazo"} dias; '
                        f'indicação vale {val_indicacao or "sem prazo"} dias '
                        f'({"ativo" if ativo else "desativado"})')
        )

        conn.commit()
        conn.close()
        return jsonify({'mensagem': 'Configuração do programa de indicação atualizada.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/indicacoes', methods=['GET'])
@exige_gerencia
def admin_listar_indicacoes():
    """Ranking de quem mais indica, para acompanhar a campanha."""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT i.id AS indicador_id, i.nome AS indicador_nome,
                   i.codigo_indicacao,
                   COUNT(r.id) AS total_indicados,
                   COALESCE(SUM(CASE WHEN r.indicacao_positiva_contada = 1 THEN 1 ELSE 0 END), 0)
                       AS total_positivas
            FROM clientes i
            JOIN clientes r ON r.indicado_por_id = i.id
            GROUP BY i.id, i.nome, i.codigo_indicacao
            ORDER BY total_positivas DESC, total_indicados DESC
        ''')
        ranking = [dict(row) for row in cursor.fetchall()]

        expirar_premios_vencidos(cursor)

        cursor.execute('''
            SELECT rec.id, rec.cliente_id, c.nome AS cliente_nome, rec.valor,
                   rec.indicacoes_completas, rec.status, rec.data_concessao,
                   rec.validade, rec.data_aplicacao
            FROM recompensas_indicacao rec
            JOIN clientes c ON c.id = rec.cliente_id
            ORDER BY rec.id DESC
            LIMIT 100
        ''')
        premios = [dict(row) for row in cursor.fetchall()]

        conn.commit()
        conn.close()
        return jsonify({'ranking': ranking, 'premios': premios}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== CAMPANHA DE FRENTISTAS (23/08) ====================
#
# Frentista é um cliente diferenciado: 1 cupom de combustível a cada 7 dias
# corridos (INTERVALO_FRENTISTA_COMBUSTIVEL_DIAS), zero cupom de óleo, e
# participa do programa de indicação normalmente — pode indicar e ser
# indicado, ganha o mesmo prêmio a cada 3 indicações positivadas.
#
# A conta só nasce ou se converte pela mão do Master, nunca pelo cadastro
# público — é essa restrição de alçada, somada ao CPF já ser único na
# tabela inteira, que fecha a brecha de um frentista se passar por cliente
# comum para ganhar cupom todo dia e repassar pra fora da pista.

@app.route('/api/admin/frentistas', methods=['POST'])
@exige_master
def admin_criar_frentista():
    """
    Cria a conta de cliente de um frentista, ou converte uma conta que já
    existe (mesmo CPF) de 'comum' para 'frentista'. Só o Master mexe aqui.
    """
    try:
        data = request.get_json() or {}

        cpf = re.sub(r'\D', '', str(data.get('cpf') or ''))
        if not validar_cpf(cpf):
            return jsonify({'erro': 'CPF inválido'}), 400

        nome = (data.get('nome') or '').strip()
        if not nome:
            return jsonify({'erro': 'Nome é obrigatório'}), 400

        email = (data.get('email') or '').strip()
        if not validar_email(email):
            return jsonify({'erro': 'Email inválido'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('SELECT id, tipo_cliente, nome FROM clientes WHERE cpf = ?', (cpf,))
        existente = cursor.fetchone()

        agora_iso = agora().isoformat()

        if existente:
            # CPF já cadastrado: é conversão de uma conta existente, não um
            # cadastro novo — a trava de CPF único impede criar uma segunda
            # linha, e é justamente essa trava que fecha a fraude.
            if (existente['tipo_cliente'] or 'comum') == 'frentista':
                conn.close()
                return jsonify({
                    'erro': f'{existente["nome"]} já está cadastrado como frentista.'
                }), 400

            cursor.execute('''
                UPDATE clientes
                SET tipo_cliente = 'frentista',
                    tipo_cliente_definido_por = ?, tipo_cliente_definido_em = ?
                WHERE id = ?
            ''', (request.admin['usuario'], agora_iso, existente['id']))
            cliente_id = existente['id']
            criado_agora = False
        else:
            placa = normalizar_placa(data.get('placa'))
            erro_placa = validar_placa(placa) if placa else None
            if erro_placa:
                return jsonify({'erro': erro_placa}), 400

            senha = data.get('senha') or ''
            if len(senha) < 6:
                conn.close()
                return jsonify({'erro': 'Senha precisa de pelo menos 6 caracteres'}), 400

            cursor.execute('SELECT id FROM clientes WHERE LOWER(email) = LOWER(?)', (email,))
            if cursor.fetchone():
                conn.close()
                return jsonify({'erro': 'Email já cadastrado'}), 400

            senha_hash = generate_password_hash(senha)

            cursor.execute('''
                INSERT INTO clientes
                (cpf, nome, ocupacao, tel, endereco, email, senha_hash, desconto_tipo,
                 desconto_valor, aceita_promocoes, data_consentimento, placa, data_placa,
                 registro_tipo, status, tipo_cliente, tipo_cliente_definido_por,
                 tipo_cliente_definido_em)
                VALUES (?, ?, 'Frentista', ?, ?, ?, ?, 'fixo', 1.00, 1, ?, ?, ?, 'frentista',
                        'ativo', 'frentista', ?, ?)
            ''', (cpf, nome, data.get('tel'), data.get('endereco') or 'Equipe CAJ SKY',
                  email, senha_hash, agora_iso, placa, agora_iso,
                  request.admin['usuario'], agora_iso))
            cliente_id = cursor.lastrowid

            meu_codigo_indicacao = f'CJ{cliente_id}'
            cursor.execute('UPDATE clientes SET codigo_indicacao = ? WHERE id = ?',
                           (meu_codigo_indicacao, cliente_id))
            criado_agora = True

        registrar_auditoria(
            cursor, request.admin,
            'frentista_cadastrado' if criado_agora else 'frentista_convertido',
            detalhe=f'{nome} (CPF ***{cpf[-4:]}) — 1 combustível a cada 7 dias, sem óleo'
        )

        conn.commit()
        conn.close()
        return jsonify({
            'cliente_id': cliente_id,
            'criado': criado_agora,
            'mensagem': ('Conta de frentista criada.' if criado_agora
                         else 'Conta convertida para frentista.')
        }), 201 if criado_agora else 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/frentistas/<int:cliente_id>/reverter', methods=['POST'])
@exige_master
def admin_reverter_frentista(cliente_id):
    """Devolve a conta ao regime de cliente comum (1 combustível por dia, óleo liberado)."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, nome, tipo_cliente FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404
        if (cliente['tipo_cliente'] or 'comum') != 'frentista':
            conn.close()
            return jsonify({'erro': 'Esta conta não está marcada como frentista.'}), 400

        cursor.execute('''
            UPDATE clientes
            SET tipo_cliente = 'comum',
                tipo_cliente_definido_por = ?, tipo_cliente_definido_em = ?
            WHERE id = ?
        ''', (request.admin['usuario'], agora().isoformat(), cliente_id))

        registrar_auditoria(cursor, request.admin, 'frentista_revertido_para_comum',
                            detalhe=cliente['nome'])

        conn.commit()
        conn.close()
        return jsonify({'mensagem': f'{cliente["nome"]} voltou a ser cliente comum.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/frentistas', methods=['GET'])
@exige_gerencia
def admin_listar_frentistas():
    """Lista os frentistas cadastrados e se já usaram o cupom da semana."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        desde = (agora() - timedelta(days=INTERVALO_FRENTISTA_COMBUSTIVEL_DIAS - 1)) \
            .strftime('%Y-%m-%d')

        cursor.execute('''
            SELECT id, nome, cpf, placa, tel, email, status,
                   tipo_cliente_definido_por, tipo_cliente_definido_em
            FROM clientes
            WHERE tipo_cliente = 'frentista'
            ORDER BY nome ASC
        ''')
        frentistas = []
        for row in cursor.fetchall():
            f = dict(row)
            f['cpf'] = f'***{f["cpf"][-4:]}' if f.get('cpf') else ''
            cursor.execute('''
                SELECT c.data_geracao, c.status
                FROM cupons c
                WHERE c.cliente_id = ?
                  AND COALESCE(c.categoria, 'combustivel') = 'combustivel'
                  AND c.data_geracao >= ?
                  AND COALESCE(c.status, '') <> 'cancelado'
                ORDER BY c.id DESC LIMIT 1
            ''', (f['id'], desde))
            usado = cursor.fetchone()
            if usado:
                proxima = (datetime.strptime(usado['data_geracao'], '%Y-%m-%d')
                           + timedelta(days=INTERVALO_FRENTISTA_COMBUSTIVEL_DIAS)).strftime('%d/%m/%Y')
                f['cupom_semana'] = 'usado' if usado['status'] in ('parcial', 'completo') else 'gerado'
                f['proximo_cupom_em'] = proxima
            else:
                f['cupom_semana'] = 'disponivel'
                f['proximo_cupom_em'] = None
            frentistas.append(f)

        conn.close()
        return jsonify({'frentistas': frentistas}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cadastros-pendentes', methods=['GET'])
@exige_gerencia
def admin_cadastros_pendentes():
    """Fila de cadastros de convênio aguardando liberação."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT c.id, c.nome, c.cpf, c.email, c.tel, c.ocupacao, c.placa,
                   c.empresa_convenio, c.empresa_convenio_id, c.data_criacao,
                   c.foto_comprovante_tipo,
                   e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
                   e.dominio_email AS empresa_dominio
            FROM clientes c
            LEFT JOIN empresas_convenio e ON e.id = c.empresa_convenio_id
            WHERE c.status = 'pendente'
            ORDER BY c.data_criacao
        ''')

        pendentes = []
        total_so_master = 0
        for c in cursor.fetchall():
            email = (c['email'] or '').lower()
            dominio = (c['empresa_dominio'] or '').strip().lower().lstrip('@')
            so_master = exige_alcada_master(c['email'], c['empresa_dominio'])
            if so_master:
                total_so_master += 1
            pendentes.append({
                'exige_master': so_master,
                'empresa_dominio': dominio or None,
                'id': c['id'],
                'nome': c['nome'],
                'cpf': c['cpf'],
                'email': c['email'],
                'tel': c['tel'],
                'placa': c['placa'],
                'empresa': c['empresa_nome'] or c['empresa_convenio'],
                'empresa_cnpj': formatar_cnpj(c['empresa_cnpj']) if c['empresa_cnpj'] else None,
                'cadastrado_em': c['data_criacao'],
                'tem_comprovante': bool(c['foto_comprovante_tipo']),
                # Sinal de apoio para a decisão, não veredito automático.
                'email_corporativo': bool(dominio and email.endswith('@' + dominio))
            })
        conn.close()
        return jsonify({
            'pendentes': pendentes,
            'total': len(pendentes),
            'total_exige_master': total_so_master,
            'meu_nivel': request.admin['nivel']
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/cadastros/<int:cliente_id>/decidir', methods=['POST'])
@exige_gerencia
def admin_decidir_cadastro(cliente_id):
    """
    Aprova ou recusa um cadastro pendente. Fica tudo na auditoria: quem
    liberou, quando e por quê.
    """
    try:
        data = request.get_json() or {}
        decisao = (data.get('decisao') or '').strip().lower()
        motivo = (data.get('motivo') or '').strip() or None

        if decisao not in ('aprovar', 'recusar'):
            return jsonify({'erro': "Decisão deve ser 'aprovar' ou 'recusar'."}), 400
        if decisao == 'recusar' and not motivo:
            return jsonify({'erro': 'Escreva o motivo da recusa.'}), 400

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT c.id, c.nome, c.status, c.email, c.empresa_convenio,
                   e.dominio_email AS empresa_dominio
            FROM clientes c
            LEFT JOIN empresas_convenio e ON e.id = c.empresa_convenio_id
            WHERE c.id = ?
        ''', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        if (cliente['status'] or '').lower() != 'pendente':
            conn.close()
            return jsonify({
                'erro': f'Esse cadastro já foi analisado (situação atual: {cliente["status"]}).'
            }), 400

        # Exceção ao e-mail corporativo é alçada do Master. A gerência aprova o
        # caso normal; abrir mão da única prova objetiva de vínculo não é
        # decisão de quem está no balcão.
        if (decisao == 'aprovar'
                and exige_alcada_master(cliente['email'], cliente['empresa_dominio'])
                and request.admin['nivel'] != 'master'):
            conn.close()
            return jsonify({
                'erro': f'{cliente["nome"]} não usou o e-mail corporativo '
                        f'(@{(cliente["empresa_dominio"] or "").lstrip("@")}). '
                        f'Só o administrador Master pode liberar essa exceção.'
            }), 403

        novo_status = 'ativo' if decisao == 'aprovar' else 'recusado'
        agora_iso = agora().isoformat()

        cursor.execute('''
            UPDATE clientes
            SET status = ?, aprovado_por = ?, data_aprovacao = ?, motivo_recusa = ?
            WHERE id = ?
        ''', (novo_status, request.admin['usuario'], agora_iso, motivo, cliente_id))

        registrar_auditoria(
            cursor, request.admin,
            'cadastro_aprovado' if decisao == 'aprovar' else 'cadastro_recusado',
            campo='cliente', valor_anterior='pendente', valor_novo=novo_status,
            detalhe=f'{cliente["nome"]} — convênio {cliente["empresa_convenio"] or "—"}'
                    + (f' | motivo: {motivo}' if motivo else '')
        )

        conn.commit()
        conn.close()

        if decisao == 'aprovar':
            return jsonify({'mensagem': f'{cliente["nome"]} liberado. Já pode gerar cupons.',
                            'status': novo_status}), 200
        return jsonify({'mensagem': f'Cadastro de {cliente["nome"]} recusado.',
                        'status': novo_status}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== TURNO DO FRENTISTA ====================
#
# O turno é "tudo o que ESTE frentista registrou desde o fechamento anterior
# dele". Não é o relógio que manda: quem entra às 5h e sai às 13h30 tem um
# relatório só, em vez de ter o movimento partido pela virada das 14h.
#
# O que amarra isso é o `fechamento_id` no abastecimento: enquanto for nulo,
# o abastecimento pertence ao turno aberto. Assim a conta não depende de
# comparar horários — relógio errado ou fuso trocado não bagunçam nada.

def _resumo_turno(cursor, usuario):
    """Monta a lista e os totais do turno aberto de um frentista."""
    cursor.execute('''
        SELECT a.id, a.data, a.hora, a.quantidade, a.valor_original,
               a.valor_desconto, a.valor_final, a.poster_id,
               p.nome AS produto_nome, p.unidade,
               cl.nome AS cliente_nome, cl.placa,
               c.qrcode
        FROM abastecimentos a
        LEFT JOIN produtos p ON p.id = a.produto_id
        LEFT JOIN clientes cl ON cl.id = a.cliente_id
        LEFT JOIN cupons c ON c.id = a.cupom_id
        WHERE a.registrado_por = ? AND a.fechamento_id IS NULL
        ORDER BY a.id ASC
    ''', (usuario,))
    linhas = cursor.fetchall()

    itens, tot = [], {'litros': 0.0, 'bruto': 0.0, 'desconto': 0.0, 'liquido': 0.0}
    por_produto = {}

    for a in linhas:
        itens.append({
            'id': a['id'],
            'data': a['data'],
            'hora': (a['hora'] or '')[:5],
            'cliente': a['cliente_nome'],
            'placa': a['placa'],
            'produto': a['produto_nome'],
            'unidade': a['unidade'] or 'L',
            'quantidade': round(a['quantidade'] or 0, 2),
            'bruto': round(a['valor_original'] or 0, 2),
            'desconto': round(a['valor_desconto'] or 0, 2),
            'liquido': round(a['valor_final'] or 0, 2),
            'posto': a['poster_id'],
            'cupom': a['qrcode'],
        })
        tot['litros'] += a['quantidade'] or 0
        tot['bruto'] += a['valor_original'] or 0
        tot['desconto'] += a['valor_desconto'] or 0
        tot['liquido'] += a['valor_final'] or 0

        # Subtotal por combustível: é o que o frentista confere contra a bomba
        # antes de entregar o caixa.
        chave = a['produto_nome'] or '—'
        linha = por_produto.setdefault(chave, {
            'produto': chave, 'unidade': a['unidade'] or 'L',
            'quantidade': 0.0, 'liquido': 0.0, 'vezes': 0})
        linha['quantidade'] += a['quantidade'] or 0
        linha['liquido'] += a['valor_final'] or 0
        linha['vezes'] += 1

    for linha in por_produto.values():
        linha['quantidade'] = round(linha['quantidade'], 2)
        linha['liquido'] = round(linha['liquido'], 2)

    return {
        'itens': itens,
        'por_produto': sorted(por_produto.values(), key=lambda x: -x['liquido']),
        'totais': {
            'abastecimentos': len(itens),
            'litros': round(tot['litros'], 2),
            'bruto': round(tot['bruto'], 2),
            'desconto': round(tot['desconto'], 2),
            'liquido': round(tot['liquido'], 2),
        },
        'primeiro': itens[0] if itens else None,
        'ultimo': itens[-1] if itens else None,
    }


@app.route('/api/frentista/turno', methods=['GET'])
@exige_admin
def frentista_turno():
    """Resumo do turno aberto de quem está logado — para conferir e imprimir."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        resumo = _resumo_turno(cursor, request.admin['usuario'])

        # Quando o turno atual começou: o fechamento anterior é a fronteira.
        cursor.execute('''
            SELECT fechado_em FROM fechamentos_turno
            WHERE usuario = ? ORDER BY id DESC LIMIT 1
        ''', (request.admin['usuario'],))
        anterior = cursor.fetchone()
        conn.close()

        resumo['operador'] = request.admin['nome'] or request.admin['usuario']
        resumo['usuario'] = request.admin['usuario']
        resumo['poster_id'] = request.admin['poster_id']
        resumo['turno_desde'] = anterior['fechado_em'] if anterior else None
        resumo['agora'] = agora().strftime('%d/%m/%Y %H:%M')
        return jsonify(resumo), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/frentista/fechar-turno', methods=['POST'])
@exige_admin
def frentista_fechar_turno():
    """
    Encerra o turno: carimba os abastecimentos, grava os totais e derruba a
    sessão.

    A sessão cair não é detalhe de tela — é a trava. Se o fechamento apenas
    imprimisse um papel e a tela continuasse funcionando, um abastecimento
    feito logo depois entraria no relatório seguinte sem ninguém perceber, e
    a soma impressa deixaria de bater com o sistema. Aqui o token morre no
    servidor: para continuar, é preciso entrar de novo, e o que vier depois
    já é o turno seguinte.
    """
    try:
        usuario = request.admin['usuario']
        conn = get_db()
        cursor = conn.cursor()

        resumo = _resumo_turno(cursor, usuario)

        if not resumo['itens']:
            conn.close()
            return jsonify({
                'erro': 'Não há abastecimentos neste turno para fechar.'
            }), 400

        cursor.execute('''
            SELECT fechado_em FROM fechamentos_turno
            WHERE usuario = ? ORDER BY id DESC LIMIT 1
        ''', (usuario,))
        anterior = cursor.fetchone()
        momento = agora().strftime('%Y-%m-%d %H:%M:%S')
        t = resumo['totais']

        cursor.execute('''
            INSERT INTO fechamentos_turno
            (usuario, nome, poster_id, aberto_em, fechado_em,
             total_abastecimentos, total_litros, total_bruto,
             total_desconto, total_liquido)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (usuario, request.admin['nome'] or usuario, request.admin['poster_id'],
              anterior['fechado_em'] if anterior else None, momento,
              t['abastecimentos'], t['litros'], t['bruto'],
              t['desconto'], t['liquido']))
        fechamento_id = cursor.lastrowid

        # Carimba os abastecimentos deste turno. Daqui em diante eles não
        # aparecem mais como "turno aberto".
        cursor.execute('''
            UPDATE abastecimentos SET fechamento_id = ?
            WHERE registrado_por = ? AND fechamento_id IS NULL
        ''', (fechamento_id, usuario))

        registrar_auditoria(
            cursor, request.admin, 'turno_fechado',
            detalhe=(f'Turno fechado por {usuario}: {t["abastecimentos"]} '
                     f'abastecimento(s), {t["litros"]:.2f} L, '
                     f'R$ {t["liquido"]:.2f} recebidos'))

        # A trava: o token morre aqui.
        cursor.execute('UPDATE admin SET token = NULL, token_expira = NULL '
                       'WHERE usuario = ?', (usuario,))

        conn.commit()
        conn.close()

        resumo['fechamento_id'] = fechamento_id
        resumo['fechado_em'] = momento
        resumo['operador'] = request.admin['nome'] or usuario
        resumo['usuario'] = usuario
        resumo['poster_id'] = request.admin['poster_id']
        resumo['turno_desde'] = anterior['fechado_em'] if anterior else None
        resumo['agora'] = agora().strftime('%d/%m/%Y %H:%M')
        resumo['mensagem'] = 'Turno fechado. Para continuar, entre de novo.'
        return jsonify(resumo), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/fechamentos', methods=['GET'])
@exige_admin
def admin_listar_fechamentos():
    """Turnos já fechados — para a gerência conferir contra o caixa."""
    try:
        data_ref = (request.args.get('data') or '').strip()
        conn = get_db()
        cursor = conn.cursor()

        if data_ref:
            cursor.execute('''
                SELECT * FROM fechamentos_turno
                WHERE SUBSTR(fechado_em, 1, 10) = ?
                ORDER BY id DESC
            ''', (data_ref,))
        else:
            cursor.execute('SELECT * FROM fechamentos_turno ORDER BY id DESC LIMIT 30')

        linhas = cursor.fetchall()
        conn.close()

        return jsonify({'fechamentos': [{
            'id': f['id'],
            'usuario': f['usuario'],
            'nome': f['nome'] or f['usuario'],
            'poster_id': f['poster_id'],
            'aberto_em': f['aberto_em'],
            'fechado_em': f['fechado_em'],
            'abastecimentos': f['total_abastecimentos'],
            'litros': round(f['total_litros'] or 0, 2),
            'bruto': round(f['total_bruto'] or 0, 2),
            'desconto': round(f['total_desconto'] or 0, 2),
            'liquido': round(f['total_liquido'] or 0, 2),
        } for f in linhas]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== LISTAGEM DE CLIENTES (SÓ MASTER) ====================
#
# Antes só existia a busca (mínimo 3 caracteres) — sem digitar nada, o Master
# não tinha como ver quantos clientes existem nem os nomes de todos. Esta
# rota devolve a lista completa, paginada, ordenada por nome, com o total —
# é o ponto de partida para abrir qualquer cadastro e editar (ver seção 39
# das dores do projeto).

@app.route('/api/admin/clientes', methods=['GET'])
@exige_master
def admin_listar_clientes():
    """Lista todos os clientes, paginada, com busca e filtro de status opcionais."""
    try:
        try:
            pagina = max(1, int(request.args.get('pagina', 1)))
        except ValueError:
            pagina = 1
        try:
            por_pagina = int(request.args.get('por_pagina', 50))
        except ValueError:
            por_pagina = 50
        por_pagina = max(1, min(por_pagina, 200))

        status = (request.args.get('status') or '').strip().lower() or None
        termo = (request.args.get('q') or '').strip()

        condicoes = []
        parametros = []

        if status:
            condicoes.append('LOWER(COALESCE(status, \'ativo\')) = ?')
            parametros.append(status)

        if termo:
            so_numeros = re.sub(r'\D', '', termo)
            like = f'%{termo.lower()}%'
            condicoes.append('''(LOWER(nome) LIKE ?
                                  OR LOWER(placa) LIKE ?
                                  OR LOWER(email) LIKE ?
                                  OR (? <> '' AND cpf LIKE ?))''')
            parametros.extend([like, like, like, so_numeros, f'%{so_numeros}%'])

        onde = f"WHERE {' AND '.join(condicoes)}" if condicoes else ''

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(f'SELECT COUNT(*) AS total FROM clientes {onde}', parametros)
        total = cursor.fetchone()['total']

        offset = (pagina - 1) * por_pagina
        cursor.execute(f'''
            SELECT id, nome, cpf, email, tel, placa, ocupacao, status,
                   tipo_cliente, empresa_convenio, data_criacao
            FROM clientes
            {onde}
            ORDER BY nome
            LIMIT ? OFFSET ?
        ''', parametros + [por_pagina, offset])

        clientes = [{
            'id': c['id'],
            'nome': c['nome'],
            # Mesma máscara da busca (seção do balcão) — a tela de listagem
            # não precisa do CPF inteiro; quem edita abre o detalhe completo.
            'cpf': f"***{(c['cpf'] or '')[3:9]}**" if c['cpf'] else '',
            'email': c['email'],
            'tel': c['tel'],
            'placa': c['placa'],
            'ocupacao': c['ocupacao'],
            'status': c['status'] or 'ativo',
            'tipo_cliente': c['tipo_cliente'] or 'comum',
            'empresa_convenio': c['empresa_convenio'],
            'cadastrado_em': c['data_criacao'],
        } for c in cursor.fetchall()]

        conn.close()
        return jsonify({
            'clientes': clientes,
            'total': total,
            'pagina': pagina,
            'por_pagina': por_pagina,
            'total_paginas': max(1, (total + por_pagina - 1) // por_pagina),
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== LIBERAÇÕES EXTRAS (SÓ MASTER) ====================
#
# A regra normal é um cupom de combustível por dia e um de óleo por semana.
# Aqui o Master abre exceção para um cliente específico, uma vez, com motivo.
#
# Por que só o Master: liberar cupom extra é dar desconto fora da regra. Se a
# gerência ou o caixa pudessem fazer isso, a regra deixaria de ser regra —
# viraria sugestão negociável no balcão.

@app.route('/api/admin/clientes/buscar', methods=['GET'])
@exige_master
def admin_buscar_clientes():
    """
    Busca cliente por nome, placa, CPF ou e-mail, para o Master achar quem
    está na frente dele no caixa. Mostra o consumo recente de cada categoria,
    que é a informação que embasa a decisão de liberar ou não.
    """
    try:
        termo = (request.args.get('q') or '').strip()
        if len(termo) < 3:
            return jsonify({'erro': 'Digite ao menos 3 caracteres'}), 400

        so_numeros = re.sub(r'\D', '', termo)
        like = f'%{termo.lower()}%'
        hoje = agora().strftime('%Y-%m-%d')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, cpf, email, placa, ocupacao, status, empresa_convenio
            FROM clientes
            WHERE LOWER(nome) LIKE ?
               OR LOWER(placa) LIKE ?
               OR LOWER(email) LIKE ?
               OR (? <> '' AND cpf LIKE ?)
            ORDER BY nome
        ''', (like, like, like, so_numeros, f'%{so_numeros}%'))
        achados = cursor.fetchall()

        clientes = []
        for c in achados:
            resumo = {}
            for categoria, dias in INTERVALO_DIAS.items():
                desde = (agora() - timedelta(days=dias - 1)).strftime('%Y-%m-%d')
                cursor.execute('''
                    SELECT c.data_geracao, c.status, p.nome AS produto_nome
                    FROM cupons c
                    LEFT JOIN produtos p ON p.id = c.produto_id
                    WHERE c.cliente_id = ?
                      AND COALESCE(c.categoria, CASE WHEN LOWER(p.tipo) = 'oleo'
                                                     THEN 'oleo' ELSE 'combustivel' END) = ?
                      AND c.data_geracao >= ?
                      AND COALESCE(c.status, '') <> 'cancelado'
                    ORDER BY c.id DESC
                ''', (c['id'], categoria, desde))
                cupom = cursor.fetchone()
                resumo[categoria] = {
                    'tem_cupom': bool(cupom),
                    'usado': bool(cupom) and (cupom['status'] or '') in ('parcial', 'completo'),
                    'produto': cupom['produto_nome'] if cupom else None,
                    'data': cupom['data_geracao'] if cupom else None,
                }

            # Liberações que ainda estão de pé para este cliente
            cursor.execute('''
                SELECT id, categoria, motivo, validade
                FROM liberacoes_extras
                WHERE cliente_id = ? AND usada = 0 AND cancelada = 0 AND validade >= ?
                ORDER BY id
            ''', (c['id'], hoje))
            pendentes = [{'id': l['id'], 'categoria': l['categoria'],
                          'motivo': l['motivo'], 'validade': l['validade']}
                         for l in cursor.fetchall()]

            clientes.append({
                'id': c['id'],
                'nome': c['nome'],
                # CPF parcial: o suficiente para conferir quem é, sem espalhar
                # o documento inteiro por uma tela que fica aberta no caixa.
                'cpf': f"***{(c['cpf'] or '')[3:9]}**" if c['cpf'] else '',
                'email': c['email'],
                'placa': c['placa'],
                'ocupacao': c['ocupacao'],
                'status': c['status'] or 'ativo',
                'empresa_convenio': c['empresa_convenio'],
                'consumo': resumo,
                'liberacoes_pendentes': pendentes,
            })

        conn.close()
        return jsonify({'clientes': clientes, 'total': len(clientes)}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


# ==================== CORREÇÃO DE CADASTRO (SÓ MASTER) ====================
#
# O cliente se cadastra sozinho, sem ninguém conferindo em tempo real — se
# ele errar o e-mail, o CPF ou o telefone na hora, ninguém mais consegue
# corrigir: o cadastro fica errado para sempre (mesma dor documentada para
# convênios em "encerrar trava o dado para sempre, em vez de liberar").
#
# Aqui o Master pode abrir um cadastro existente e corrigir os campos que
# tipicamente saem errados na digitação. Fica de fora, de propósito,
# ocupação/registro/empresa de convênio — esses têm fluxo próprio (aprovação
# de cadastro, conversão de frentista) e mexer neles por aqui abriria uma
# porta lateral para a mesma lógica que já existe em outro lugar.

@app.route('/api/admin/clientes/<int:cliente_id>', methods=['GET'])
@exige_master
def admin_detalhe_cliente(cliente_id):
    """Cadastro completo, sem máscara — só para preencher a tela de correção."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, cpf, email, tel, endereco, placa, ocupacao,
                   status, empresa_convenio, tipo_cliente, data_criacao
            FROM clientes WHERE id = ?
        ''', (cliente_id,))
        c = cursor.fetchone()
        conn.close()
        if not c:
            return jsonify({'erro': 'Cliente não encontrado'}), 404
        return jsonify({'cliente': {
            'id': c['id'],
            'nome': c['nome'],
            'cpf': c['cpf'],
            'email': c['email'],
            'tel': c['tel'],
            'endereco': c['endereco'],
            'placa': c['placa'],
            'ocupacao': c['ocupacao'],
            'status': c['status'],
            'empresa_convenio': c['empresa_convenio'],
            'tipo_cliente': c['tipo_cliente'],
            'cadastrado_em': c['data_criacao'],
        }}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/clientes/<int:cliente_id>', methods=['PUT'])
@exige_master
def admin_editar_cliente(cliente_id):
    """
    Corrige nome, CPF, e-mail, telefone, endereço ou placa de um cadastro já
    existente. Só o campo enviado é alterado — o Master corrige só o que
    está errado, sem precisar reenviar o cadastro inteiro.
    """
    try:
        data = request.get_json() or {}

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clientes WHERE id = ?', (cliente_id,))
        atual = cursor.fetchone()
        if not atual:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        campos = ('nome', 'cpf', 'email', 'tel', 'endereco', 'placa')
        if not any(campo in data for campo in campos):
            conn.close()
            return jsonify({'erro': 'Nenhum campo para atualizar.'}), 400

        sets = []
        valores = []
        mudou_algo = False

        if 'nome' in data:
            nome = (data.get('nome') or '').strip()
            if not nome:
                conn.close()
                return jsonify({'erro': 'Nome é obrigatório'}), 400
            if nome != atual['nome']:
                registrar_auditoria(cursor, request.admin, 'cliente_editado',
                                     produto_id=cliente_id, campo='nome',
                                     valor_anterior=atual['nome'], valor_novo=nome)
                sets.append('nome = ?')
                valores.append(nome)
                mudou_algo = True

        if 'cpf' in data:
            cpf = re.sub(r'\D', '', str(data.get('cpf') or ''))
            if not validar_cpf(cpf):
                conn.close()
                return jsonify({'erro': 'CPF inválido'}), 400
            if cpf != (atual['cpf'] or ''):
                cursor.execute('SELECT id FROM clientes WHERE cpf = ? AND id != ?',
                               (cpf, cliente_id))
                if cursor.fetchone():
                    conn.close()
                    return jsonify({'erro': 'Esse CPF já está cadastrado em outro cliente.'}), 400
                registrar_auditoria(
                    cursor, request.admin, 'cliente_editado',
                    produto_id=cliente_id, campo='cpf',
                    valor_anterior=f"***{(atual['cpf'] or '')[-4:]}" if atual['cpf'] else None,
                    valor_novo=f"***{cpf[-4:]}"
                )
                sets.append('cpf = ?')
                valores.append(cpf)
                mudou_algo = True

        if 'email' in data:
            email = (data.get('email') or '').strip()
            if not validar_email(email):
                conn.close()
                return jsonify({'erro': 'Email inválido'}), 400
            if email.lower() != (atual['email'] or '').lower():
                cursor.execute('SELECT id FROM clientes WHERE LOWER(email) = LOWER(?) AND id != ?',
                               (email, cliente_id))
                if cursor.fetchone():
                    conn.close()
                    return jsonify({'erro': 'Esse e-mail já está cadastrado em outro cliente.'}), 400
                registrar_auditoria(cursor, request.admin, 'cliente_editado',
                                     produto_id=cliente_id, campo='email',
                                     valor_anterior=atual['email'], valor_novo=email)
                sets.append('email = ?')
                valores.append(email)
                mudou_algo = True

        if 'tel' in data:
            tel = (data.get('tel') or '').strip()
            if not tel:
                conn.close()
                return jsonify({'erro': 'Telefone é obrigatório'}), 400
            if tel != (atual['tel'] or ''):
                registrar_auditoria(cursor, request.admin, 'cliente_editado',
                                     produto_id=cliente_id, campo='tel',
                                     valor_anterior=atual['tel'], valor_novo=tel)
                sets.append('tel = ?')
                valores.append(tel)
                mudou_algo = True

        if 'endereco' in data:
            endereco = (data.get('endereco') or '').strip()
            if not endereco:
                conn.close()
                return jsonify({'erro': 'Endereço é obrigatório'}), 400
            if endereco != (atual['endereco'] or ''):
                registrar_auditoria(cursor, request.admin, 'cliente_editado',
                                     produto_id=cliente_id, campo='endereco',
                                     valor_anterior=atual['endereco'], valor_novo=endereco)
                sets.append('endereco = ?')
                valores.append(endereco)
                mudou_algo = True

        if 'placa' in data:
            placa = normalizar_placa(data.get('placa'))
            if placa:
                erro_placa = validar_placa(placa)
                if erro_placa:
                    conn.close()
                    return jsonify({'erro': erro_placa}), 400
            if placa != (atual['placa'] or None):
                registrar_auditoria(cursor, request.admin, 'cliente_editado',
                                     produto_id=cliente_id, campo='placa',
                                     valor_anterior=atual['placa'], valor_novo=placa)
                sets.append('placa = ?')
                valores.append(placa)
                mudou_algo = True

        if not mudou_algo:
            conn.close()
            return jsonify({'mensagem': 'Nada mudou — os valores enviados já eram os cadastrados.'}), 200

        sets.append('data_atualizacao = ?')
        valores.append(agora().isoformat())
        valores.append(cliente_id)

        cursor.execute(f'UPDATE clientes SET {", ".join(sets)} WHERE id = ?', valores)
        conn.commit()
        conn.close()
        return jsonify({'mensagem': 'Cadastro atualizado.'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/liberacoes', methods=['GET'])
@exige_master
def admin_listar_liberacoes():
    """Liberações em aberto e as últimas usadas — para conferir e cancelar."""
    try:
        hoje = agora().strftime('%Y-%m-%d')
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT l.id, l.cliente_id, l.categoria, l.motivo, l.liberado_por,
                   l.data_liberacao, l.validade, l.usada, l.data_uso,
                   cl.nome AS cliente_nome, cl.placa
            FROM liberacoes_extras l
            LEFT JOIN clientes cl ON cl.id = l.cliente_id
            WHERE l.cancelada = 0
            ORDER BY l.id DESC
            LIMIT 50
        ''')
        linhas = cursor.fetchall()
        conn.close()

        abertas, historico = [], []
        for l in linhas:
            item = {
                'id': l['id'],
                'cliente_id': l['cliente_id'],
                'cliente_nome': l['cliente_nome'],
                'placa': l['placa'],
                'categoria': l['categoria'],
                'categoria_nome': NOME_CATEGORIA.get(l['categoria'], l['categoria']),
                'motivo': l['motivo'],
                'liberado_por': l['liberado_por'],
                'data_liberacao': l['data_liberacao'],
                'validade': l['validade'],
                'usada': bool(l['usada']),
                'data_uso': l['data_uso'],
                'expirada': (not l['usada']) and l['validade'] < hoje,
            }
            if not item['usada'] and not item['expirada']:
                abertas.append(item)
            else:
                historico.append(item)

        return jsonify({'abertas': abertas, 'historico': historico[:20]}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/liberacoes', methods=['POST'])
@exige_master
def admin_criar_liberacao():
    """Libera um cupom extra para um cliente. Uso único, motivo obrigatório."""
    try:
        data = request.get_json() or {}
        cliente_id = data.get('cliente_id')
        categoria = (data.get('categoria') or '').strip().lower()
        motivo = (data.get('motivo') or '').strip()

        if categoria not in ('combustivel', 'oleo', 'qualquer'):
            return jsonify({'erro': "Categoria deve ser 'combustivel', 'oleo' ou 'qualquer'"}), 400

        # Motivo obrigatório, e não por burocracia: sem ele, daqui a três meses
        # ninguém sabe por que aquele cliente levou cupom fora da regra — e é
        # exatamente isso que a auditoria precisa responder.
        if len(motivo) < 5:
            return jsonify({'erro': 'Escreva o motivo da liberação (ao menos 5 caracteres)'}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, nome FROM clientes WHERE id = ?', (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({'erro': 'Cliente não encontrado'}), 404

        hoje = agora().strftime('%Y-%m-%d')

        # Uma liberação em aberto por categoria. Duas seguidas seria dar dois
        # cupons extras sem que a tela deixasse isso óbvio.
        cursor.execute('''
            SELECT id FROM liberacoes_extras
            WHERE cliente_id = ? AND categoria = ? AND usada = 0
              AND cancelada = 0 AND validade >= ?
        ''', (cliente_id, categoria, hoje))
        if cursor.fetchone():
            conn.close()
            return jsonify({
                'erro': f'{cliente["nome"]} já tem uma liberação de '
                        f'{NOME_CATEGORIA.get(categoria, categoria)} em aberto.'
            }), 400

        validade = (agora() + timedelta(days=VALIDADE_LIBERACAO_DIAS)
                    ).strftime('%Y-%m-%d')

        cursor.execute('''
            INSERT INTO liberacoes_extras
            (cliente_id, categoria, motivo, liberado_por, data_liberacao, validade)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (cliente_id, categoria, motivo, request.admin['usuario'],
              agora().strftime('%Y-%m-%d %H:%M:%S'), validade))

        registrar_auditoria(
            cursor, request.admin, 'liberacao_extra_criada',
            detalhe=(f'Cupom extra de {NOME_CATEGORIA.get(categoria, categoria)} '
                     f'liberado para {cliente["nome"]} — motivo: {motivo}'))

        conn.commit()
        conn.close()

        return jsonify({
            'mensagem': f'Liberado 1 cupom extra de '
                        f'{NOME_CATEGORIA.get(categoria, categoria)} para '
                        f'{cliente["nome"]}. Vale até '
                        f'{datetime.strptime(validade, "%Y-%m-%d").strftime("%d/%m/%Y")}.',
            'validade': validade
        }), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/admin/liberacoes/<int:liberacao_id>/cancelar', methods=['POST'])
@exige_master
def admin_cancelar_liberacao(liberacao_id):
    """Cancela uma liberação que ainda não foi usada."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT l.id, l.usada, l.categoria, cl.nome AS cliente_nome
            FROM liberacoes_extras l
            LEFT JOIN clientes cl ON cl.id = l.cliente_id
            WHERE l.id = ? AND l.cancelada = 0
        ''', (liberacao_id,))
        lib = cursor.fetchone()

        if not lib:
            conn.close()
            return jsonify({'erro': 'Liberação não encontrada'}), 404
        if lib['usada']:
            conn.close()
            return jsonify({'erro': 'Esta liberação já foi usada e não pode ser cancelada'}), 400

        cursor.execute('''
            UPDATE liberacoes_extras
            SET cancelada = 1, cancelada_por = ?, data_cancelamento = ?
            WHERE id = ?
        ''', (request.admin['usuario'], agora().strftime('%Y-%m-%d %H:%M:%S'),
              liberacao_id))

        registrar_auditoria(
            cursor, request.admin, 'liberacao_extra_cancelada',
            detalhe=f'Liberação #{liberacao_id} de {lib["cliente_nome"]} cancelada')

        conn.commit()
        conn.close()
        return jsonify({'mensagem': 'Liberação cancelada'}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'OK',
        'timestamp': agora().isoformat(),
        'versao': '2.1'
    }), 200

if __name__ == '__main__':
    # Debug só na sua máquina. Em produção (Render) fica desligado,
    # senão o Flask expõe um console que executa código no servidor.
    em_producao = bool(os.environ.get('DATABASE_URL') or os.environ.get('RENDER'))
    porta = int(os.environ.get('PORT', 5000))

    app.run(debug=not em_producao, host='0.0.0.0', port=porta)
