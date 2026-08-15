# CAJSKY — Estado do projeto

_Última sessão: 15/08/2026_

Aplicativo de descontos para motoristas de aplicativo e taxistas nos postos
**CAJ** (Rua Estados Unidos, 1930) e **SKY** (Rua Estados Unidos, 1776).

---

## Onde tudo está no ar

| O quê | Endereço |
|---|---|
| App do cliente | https://descontos-jardins-sky.vercel.app |
| Painel administrativo | https://descontos-jardins-sky.vercel.app/admin.html |
| **Tela da pista (frentista)** | https://descontos-jardins-sky.vercel.app/frentista.html |
| Backend (API) | https://descontos-jardins-sky-1.onrender.com |
| Código | https://github.com/edmundomikui-design/descontos-jardins-sky |
| Pasta local | `C:\PESSOAIS\CLAUDE IA\aplicativo CAJSKY\pwa-descontos` |

**Frontend:** Vercel (deploy automático a cada `git push`)
**Backend:** Render — Python 3.10.14, PostgreSQL `cajsky-db`
**Banco:** PostgreSQL persistente (variável `DATABASE_URL` no Render)

---

## Como publicar uma alteração

```powershell
cd "C:\PESSOAIS\CLAUDE IA\aplicativo CAJSKY\pwa-descontos"
git push origin main
```

Vercel publica sozinho em ~1 min. Render leva ~3 min. Se o Render não disparar,
use **Manual Deploy → Deploy latest commit** no painel dele.

---

## O que já funciona

### App do cliente
- Cadastro e login do motorista
- **Aceite de mensagens no cadastro** — dois consentimentos separados:
  - *Avisos do aplicativo* (**obrigatório**): cupons do dia, mudanças de preço e ofertas
    por tempo limitado dos postos CAJ e SKY. É a própria função do app, por isso pode ser
    exigido. Validado no navegador e também no backend (erro 400 sem o aceite).
    Grava `clientes.aceita_promocoes` + `data_consentimento`.
  - *Promoções de parceiros* (**opcional**): marketing puro, marcado à parte.
    Grava `clientes.aceita_parceiros` + `data_consentimento_parceiros`.

  A data de cada aceite é a prova exigida pela LGPD.
- Lista de produtos com preços atualizados
- Geração de cupom com QR code, um por produto por dia
- **Preço final por litro em destaque**, com preço de bomba riscado ao lado
- Recuperação do cupom do dia ao reabrir o app (botão "Ver cupom")
- Funciona sem internet na pista (cupom fica salvo no celular, expira à meia-noite)
- Botão para salvar o QR code na galeria
- Cupom vale **somente no dia da geração**

### Tela da pista (frentista) — `frentista.html`
Feita para celular na mão, ao sol: fundo escuro, números grandes, poucos toques.
Atalho **📷 Pista** no topo do painel administrativo.

1. **Entra** com o mesmo usuário do painel (o nível *Caixa* já basta).
   O posto vem do cadastro do usuário — o frentista não escolhe, então o
   abastecimento nunca cai no caixa do posto errado.
2. **Lê o QR** pela câmera traseira. Se a câmera falhar ou a tela do motorista
   estiver ruim, dá para **digitar o código** de 12 caracteres.
3. **Confere o cupom** antes de liberar a bomba: nome do motorista, CPF
   mascarado (`***.123.456-**`), combustível, preço de bomba riscado, preço com
   desconto em destaque e o saldo de litros do dia.
   Faixa verde "pode abastecer" ou vermelha com o motivo do bloqueio.
4. **Digita os litros** que marcaram na bomba (tem o atalho "usar o saldo
   inteiro"). A tela calcula na hora e mostra grande **quanto cobrar**.
5. **Confirma** e vê o comprovante com o valor, a economia do motorista e
   quantos litros ainda restam no cupom.

Endpoints usados: `GET /api/cupom/consultar` (novo — só consulta, não baixa
saldo) e `POST /api/cupom/usar`. **Os dois agora exigem login.**
Se a tela não mandar o valor, o servidor o calcula pelo preço congelado no
cupom — erro de digitação na pista não vira valor cobrado errado.
Cada abastecimento deixa rastro na auditoria: quem liberou, em que posto,
quantos litros e quanto foi cobrado.

### Painel administrativo
Três níveis de acesso:

| Nível | Pode |
|---|---|
| **Master** | Tudo: custo, margem mínima, preços, descontos, usuários. **Não consegue vender abaixo do custo.** |
| **Gerência** | Altera preços e descontos até o limite da margem mínima. Não vê nem altera o custo. |
| **Caixa** | Somente consulta: fechamento e relatórios. |

- **Preços e descontos por produto** — custo, preço de bomba, desconto (R$/L ou %),
  limite de litros, margem mínima. Nome do produto editável.
- **Trava de margem** — ninguém emite desconto que deixe o preço final abaixo do custo.
  A Gerência ainda respeita custo + margem mínima do produto.
- **Fechamento de caixa** — filtros por data, faixa de horário (ex: 14h às 18h),
  posto e produto; atalhos para os três turnos (6-14h, 14-22h, 22-6h).
  Lista hora, posto, combustível, cliente, litros, preço de bomba, desconto e valor cobrado.
  Impressão em paisagem com cabeçalho identificando período e quem emitiu.
- **Histórico de alterações (auditoria)** — quem mudou, quando, de quanto para quanto,
  incluindo as tentativas que a trava bloqueou.

### Regras de negócio importantes
- O preço e o desconto ficam **congelados no cupom** no momento da geração.
  Reajuste de preço à tarde não altera cupons emitidos de manhã.
- O caixa recusa cupom gerado em outro dia.
- Cupom com limite de litros; abastecimentos parciais descontam do saldo.

---

## Pendências e ideias

- [ ] Cadastrar os **custos reais** de cada produto no painel
      (sem custo cadastrado a Gerência não consegue dar desconto — comportamento proposital)
- [x] ~~Tela para o frentista registrar o abastecimento lendo o QR code~~ — pronta
- [ ] Criar um usuário nível **Caixa** para cada frentista (Painel → Usuários),
      já com o posto certo (CAJ ou SKY). Sem isso ninguém entra na tela da pista.
- [ ] Trocar o servidor de desenvolvimento do Flask por gunicorn (~10 min)
- [ ] Tela no painel para listar/exportar quem aceitou receber mensagens
      (a base do disparo de campanhas), separando avisos do app x parceiros
- [ ] **Push de promoção-relâmpago** — notificação curta com validade de horário
      ("até as 14h a gasolina sai a R$ X"). O service-worker já existe; falta o
      Web Push (chaves VAPID no backend, permissão no app) e uma tela no painel
      para escrever a mensagem, escolher a validade e disparar.
- [ ] Convênio com empresas vizinhas via RH (ideia original do projeto)
- [ ] Preço separado por posto, se algum dia CAJ e SKY divergirem

## Atenção

- O **PostgreSQL gratuito do Render expira 30 dias após a criação**
  (criado em 15/08/2026), com 14 dias de prazo para virar pago antes de ser apagado.
  Plano pago a partir de ~US$ 6/mês. Decidir antes de colocar motoristas de verdade.

---

## Detalhes técnicos que custaram tempo

Anotado para não repetir:

- O Render **ignora `runtime.txt`** (descontinuado). Use `.python-version`
  na raiz ou a variável `PYTHON_VERSION`. Sem isso ele usa Python 3.14,
  onde o `psycopg2-binary` não instala.
- O backend em produção roda **`backend/app-v2.py`** (não o `app.py`).
- Ao mudar arquivos JS, subir o número de versão em `?v=` nos HTML e o
  `CACHE_NAME` do `service-worker.js`, senão o navegador serve a versão velha.
- Comparações de preço arredondam para centavos antes de comparar —
  `6.09 - 0.37` em ponto flutuante dá `5.7199...` e reprovaria um valor válido.
- A câmera do navegador **só funciona em HTTPS** (ou `localhost`). Como o app
  roda no Vercel, já está resolvido — mas testar abrindo o arquivo direto do
  disco não vai ligar a câmera.
- `backend/teste_frentista.py` roda o fluxo da pista ponta a ponta num SQLite
  descartável: `python teste_frentista.py` dentro de `backend/`. Não encosta
  no banco de produção.
