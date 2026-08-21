// ===== TELA DA PISTA (FRENTISTA) — CAJ / SKY =====
// Fluxo: entrar → ler o QR → conferir o cupom → digitar os litros → confirmar.

const API = 'https://descontos-jardins-sky-1.onrender.com/api';

let sessao = null;      // { token, nome, nivel, posto }
let cupom = null;       // cupom consultado no momento
let leitor = null;      // instância do Html5Qrcode
let cameraLigada = false;
let enviando = false;   // trava contra duplo toque no Confirmar

// ---------- utilidades ----------

const el = id => document.getElementById(id);

const reais = v => 'R$ ' + (Number(v) || 0).toFixed(2).replace('.', ',');
const litros = v => (Number(v) || 0).toFixed(2).replace('.', ',') + ' L';

// ABC1D23 -> ABC 1D23, que é como a placa aparece no carro
const formatarPlaca = p => (!p || p.length !== 7) ? (p || '—') : p.slice(0, 3) + ' ' + p.slice(3);

function mostrarTela(id) {
    ['tela-login', 'tela-leitura', 'tela-cupom', 'tela-ok', 'tela-turno']
        .forEach(t => { const e = el(t); if (e) e.hidden = (t !== id); });
    window.scrollTo(0, 0);
}

// Texto que vem do banco (nome do motorista, produto) nunca entra na página
// sem passar por aqui — senão um nome com < ou > quebraria o comprovante.
function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function erro(id, mensagem) {
    const campo = el(id);
    campo.textContent = mensagem || '';
    campo.classList.toggle('visivel', Boolean(mensagem));
}

function vibrar(padrao) {
    if (navigator.vibrate) navigator.vibrate(padrao);
}

// Toda chamada passa por aqui: anexa o token e trata sessão expirada num lugar só.
async function chamar(caminho, opcoes = {}) {
    const resposta = await fetch(API + caminho, {
        ...opcoes,
        headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': sessao ? sessao.token : '',
            ...(opcoes.headers || {})
        }
    });

    let dados = {};
    try { dados = await resposta.json(); } catch (e) { /* resposta sem corpo */ }

    if (resposta.status === 401) {
        sair('Sua sessão expirou. Entre de novo.');
        throw new Error('sessao_expirada');
    }
    if (!resposta.ok) throw new Error(dados.erro || 'Não consegui falar com o servidor.');

    return dados;
}

// ---------- sessão ----------

function guardarSessao(s) {
    sessao = s;
    try { localStorage.setItem('cajsky_pista', JSON.stringify(s)); } catch (e) {}
    el('topo-nome').textContent = s.nome;
    el('topo-posto').textContent = s.posto || '—';
}

function restaurarSessao() {
    try {
        const bruto = localStorage.getItem('cajsky_pista');
        if (!bruto) return false;
        const s = JSON.parse(bruto);
        // o token do backend vale 12h; se expirou, o 401 devolve para o login
        if (!s || !s.token) return false;
        guardarSessao(s);
        return true;
    } catch (e) { return false; }
}

async function entrar(evento) {
    evento.preventDefault();
    erro('login-erro', '');

    const botao = el('btn-entrar');
    botao.disabled = true;
    botao.textContent = 'Entrando…';

    try {
        const resposta = await fetch(API + '/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                usuario: el('login-usuario').value.trim(),
                senha: el('login-senha').value
            })
        });
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || 'Não consegui entrar.');

        guardarSessao({
            token: dados.token,
            nome: dados.nome || dados.usuario,
            nivel: dados.nivel,
            posto: dados.poster_id
        });

        el('login-senha').value = '';
        mostrarTela('tela-leitura');
    } catch (e) {
        erro('login-erro', e.message);
    } finally {
        botao.disabled = false;
        botao.textContent = 'Entrar';
    }
    return false;
}

function sair(mensagem) {
    pararCamera();
    sessao = null;
    cupom = null;
    try { localStorage.removeItem('cajsky_pista'); } catch (e) {}
    mostrarTela('tela-login');
    erro('login-erro', mensagem || '');
}

// ---------- leitura do QR ----------

function alternarCamera() {
    if (cameraLigada) pararCamera(); else ligarCamera();
}

async function ligarCamera() {
    erro('leitura-erro', '');

    if (typeof Html5Qrcode === 'undefined') {
        erro('leitura-erro', 'A leitura por câmera não carregou. Digite o código abaixo do QR.');
        return;
    }

    el('area-camera').classList.add('ativa');
    el('btn-camera').textContent = '✕ Desligar a câmera';
    cameraLigada = true;

    try {
        leitor = new Html5Qrcode('leitor', { verbose: false });
        await leitor.start(
            { facingMode: 'environment' },              // câmera traseira
            { fps: 10, qrbox: { width: 240, height: 240 } },
            texto => aoLerQR(texto),
            () => { /* quadro sem QR: silêncio, senão pisca erro o tempo todo */ }
        );
    } catch (e) {
        pararCamera();
        erro('leitura-erro',
            'Não consegui abrir a câmera. Autorize o acesso nas permissões do navegador ' +
            'ou digite o código abaixo do QR.');
    }
}

async function pararCamera() {
    cameraLigada = false;
    el('area-camera').classList.remove('ativa');
    el('btn-camera').textContent = '📷 Ligar a câmera';

    if (leitor) {
        try { await leitor.stop(); await leitor.clear(); } catch (e) {}
        leitor = null;
    }
}

async function aoLerQR(texto) {
    vibrar(60);
    await pararCamera();       // trava a leitura para não disparar duas vezes
    await consultar(texto.trim());
}

function consultarManual(evento) {
    evento.preventDefault();
    const codigo = el('codigo-manual').value.trim();
    if (!codigo) {
        erro('leitura-erro', 'Digite o código que aparece embaixo do QR.');
        return false;
    }
    consultar(codigo);
    return false;
}

// ---------- conferência do cupom ----------

async function consultar(codigo) {
    erro('leitura-erro', '');

    try {
        const dados = await chamar('/cupom/consultar?qrcode=' + encodeURIComponent(codigo));
        cupom = dados;
        el('codigo-manual').value = '';
        exibirCupom(dados);
    } catch (e) {
        if (e.message !== 'sessao_expirada') erro('leitura-erro', e.message);
    }
}

function exibirCupom(c) {
    const agora = new Date();
    el('cupom-hora').textContent =
        String(agora.getHours()).padStart(2, '0') + ':' +
        String(agora.getMinutes()).padStart(2, '0');

    // A placa é a única conferência que não depende de sistema nenhum:
    // ou bate com o carro na bomba, ou não bate.
    el('cupom-placa').textContent = formatarPlaca(c.placa);
    el('cupom-ocupacao').textContent = c.ocupacao || '';

    const alerta = el('alerta-placa');
    if (c.placa_em_varios_cadastros) {
        alerta.textContent = '⚠ Esta placa está em ' + c.placa_qtd_cadastros +
            ' cadastros. Pode ser táxi dividido por turno — confira o rosto do motorista.';
        alerta.hidden = false;
    } else {
        alerta.hidden = true;
    }

    el('cupom-cliente').textContent = c.cliente_nome || '—';
    el('cupom-cpf').textContent = 'CPF ' + (c.cliente_cpf || '—');
    el('cupom-produto').textContent =
        (c.produto_icone ? c.produto_icone + ' ' : '') + (c.produto_nome || '—');
    el('cupom-preco-bomba').textContent = reais(c.preco_bomba);
    el('cupom-preco-final').textContent = reais(c.preco_com_desconto);
    el('cupom-restante').textContent = litros(c.quantidade_restante);
    el('atalho-saldo').textContent = litros(c.quantidade_restante);

    const faixa = el('faixa-status');
    const bloco = el('bloco-abastecimento');

    if (c.valido) {
        faixa.className = 'faixa ok';
        faixa.textContent = '✓ Cupom válido — pode abastecer';
        bloco.hidden = false;
        vibrar(60);
    } else {
        faixa.className = 'faixa bloqueio';
        faixa.textContent = '✕ ' + (c.motivo || 'Cupom não pode ser usado.');
        bloco.hidden = true;
        vibrar([80, 60, 80]);
    }

    el('litros').value = '';
    erro('cupom-erro', '');
    recalcular();
    mostrarTela('tela-cupom');
}

function usarSaldoTotal() {
    if (!cupom) return;
    el('litros').value = Number(cupom.quantidade_restante).toFixed(2);
    recalcular();
}

function recalcular() {
    if (!cupom) return;

    const qtd = parseFloat(String(el('litros').value).replace(',', '.')) || 0;
    const bruto = qtd * cupom.preco_bomba;
    const desconto = Math.min(qtd * cupom.desconto_por_unidade, bruto);

    el('calc-bruto').textContent = reais(bruto);
    el('calc-desconto').textContent = '− ' + reais(desconto);
    el('calc-final').textContent = reais(bruto - desconto);

    // Só libera o botão com quantidade válida e dentro do saldo — o backend
    // valida de novo, mas aqui o frentista já vê o problema na hora.
    const dentroDoSaldo = qtd > 0 && qtd <= cupom.quantidade_restante + 0.001;
    el('btn-confirmar').disabled = !dentroDoSaldo || enviando;

    if (qtd > cupom.quantidade_restante + 0.001) {
        erro('cupom-erro', 'Passou do saldo do cupom: restam ' +
            litros(cupom.quantidade_restante) + '. Cobre a diferença sem desconto.');
    } else {
        erro('cupom-erro', '');
    }
}

// ---------- confirmação ----------

async function confirmar() {
    if (!cupom || enviando) return;

    const qtd = parseFloat(String(el('litros').value).replace(',', '.')) || 0;
    if (qtd <= 0) return;

    enviando = true;
    const botao = el('btn-confirmar');
    botao.disabled = true;
    botao.textContent = 'Registrando…';

    try {
        const dados = await chamar('/cupom/usar', {
            method: 'POST',
            body: JSON.stringify({
                qrcode: cupom.qrcode,
                produto_id: cupom.produto_id,
                quantidade: qtd,
                valor_sem_desconto: Number((qtd * cupom.preco_bomba).toFixed(2))
            })
        });
        exibirComprovante(dados);
    } catch (e) {
        if (e.message !== 'sessao_expirada') erro('cupom-erro', e.message);
    } finally {
        enviando = false;
        botao.textContent = 'Confirmar abastecimento';
        recalcular();
    }
}

function exibirComprovante(d) {
    vibrar([60, 40, 60]);
    ultimoComprovante = d;   // guardado para o botão de imprimir

    el('ok-valor').textContent = reais(d.valor_final);
    el('ok-cliente').textContent = d.cliente || '—';
    el('ok-produto').textContent = d.produto || '—';
    el('ok-litros').textContent = litros(d.quantidade);
    el('ok-economia').textContent = reais(d.valor_desconto);
    el('ok-posto').textContent = (d.posto || '—') + ' · ' + (d.hora || '');

    el('ok-titulo').textContent = d.cupom_status === 'completo'
        ? 'Cupom encerrado'
        : 'Abastecimento registrado';

    el('ok-saldo').textContent = d.cupom_status === 'completo'
        ? 'O motorista já usou todo o limite de hoje.'
        : 'Ainda restam ' + litros(d.quantidade_restante) + ' neste cupom hoje.';

    mostrarTela('tela-ok');
}

function voltarParaLeitura() {
    cupom = null;
    el('codigo-manual').value = '';
    erro('leitura-erro', '');
    mostrarTela('tela-leitura');
}

// ============================================================
// IMPRESSÃO
// ============================================================
//
// O conteúdo é montado numa área escondida da própria página e o navegador
// imprime só ela (ver @media print no CSS). Abrir outra janela seria mais
// simples de escrever, mas o bloqueador de pop-up do celular barraria a
// impressão sem dizer o motivo — e o frentista ficaria clicando à toa.

let ultimoComprovante = null;   // guardado para o botão de imprimir

const CABECALHO_POSTOS = {
    CAJ: 'Posto CAJ — R. Estados Unidos, 1930 — Jardins, São Paulo',
    SKY: 'Posto SKY — R. Estados Unidos, 1776 — Jardins, São Paulo',
};

function cabecalhoImpressao(titulo, posto, extra) {
    const endereco = CABECALHO_POSTOS[posto] || 'Postos CAJ e SKY — Jardins, São Paulo';
    return `
    <div class="p-cabecalho">
        <div>
            <div class="p-marca">⛽ CAJ SKY</div>
            <div class="p-sub">${esc(endereco)}</div>
        </div>
        <div class="p-meta">
            <strong>${esc(titulo)}</strong><br>
            ${esc(extra || '')}
        </div>
    </div>`;
}

function imprimirHtml(html, classeExtra) {
    const area = el('area-impressao');
    area.className = classeExtra || '';
    area.innerHTML = html;
    // Um respiro antes de imprimir: sem isso, o navegador às vezes dispara a
    // impressão com a área ainda vazia e sai folha em branco.
    setTimeout(() => window.print(), 120);
}

function imprimirComprovante() {
    if (!ultimoComprovante) return;
    const d = ultimoComprovante;
    const agora = new Date().toLocaleString('pt-BR');

    imprimirHtml(`
    <div class="p-comprovante">
        ${cabecalhoImpressao('Comprovante de desconto', d.posto, agora)}

        <div class="p-titulo">Abastecimento com desconto CAJ SKY</div>

        <div class="p-linha"><span>Motorista</span><strong>${esc(d.cliente)}</strong></div>
        <div class="p-linha"><span>Placa</span><strong>${esc(formatarPlaca(d.placa))}</strong></div>
        <div class="p-linha"><span>Combustível</span><strong>${esc(d.produto)}</strong></div>
        <div class="p-linha"><span>Quantidade</span><strong>${litros(d.quantidade)}</strong></div>
        <div class="p-linha"><span>Cupom</span><strong>${esc(d.cupom || '—')}</strong></div>
        <div class="p-linha"><span>Atendente</span><strong>${esc(sessao ? sessao.nome : '')}</strong></div>

        <div class="p-totais">
            <div class="p-linha"><span>Valor sem desconto</span><span>${reais(d.valor_original)}</span></div>
            <div class="p-linha"><span>Desconto CAJ SKY</span><span>− ${reais(d.valor_desconto)}</span></div>
            <div class="p-linha forte"><span>Valor pago</span><span>${reais(d.valor_final)}</span></div>
        </div>

        <div class="p-rodape">
            Você economizou ${reais(d.valor_desconto)} neste abastecimento.<br>
            Este documento não substitui o cupom fiscal.
        </div>

        <div class="p-corte">✂ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -</div>
    </div>`);
}

// ============================================================
// MEU TURNO
// ============================================================

let turnoAtual = null;

async function abrirTurno() {
    pararCamera();
    mostrarTela('tela-turno');
    erro('turno-erro', '');
    el('turno-lista').innerHTML = '<p class="turno-vazio">Carregando…</p>';
    el('turno-por-produto').innerHTML = '';

    try {
        turnoAtual = await chamar('/frentista/turno');
        desenharTurno(turnoAtual);
    } catch (e) {
        if (e.message !== 'sessao_expirada') erro('turno-erro', e.message);
    }
}

function desenharTurno(d) {
    const t = d.totais;

    el('turno-operador').textContent = d.operador || '—';
    el('turno-qtd').textContent = t.abastecimentos;
    el('turno-litros').textContent = litros(t.litros);
    el('turno-bruto').textContent = reais(t.bruto);
    el('turno-desconto').textContent = '− ' + reais(t.desconto);
    el('turno-liquido').textContent = reais(t.liquido);

    const desde = d.turno_desde
        ? 'Desde o fechamento anterior (' + formatarQuando(d.turno_desde) + ')'
        : 'Desde o seu primeiro abastecimento';
    el('turno-periodo').textContent = desde + ' até agora.';

    // Sem movimento não há o que fechar — e o botão fica desligado para o
    // frentista não achar que o sistema travou.
    const btn = el('btn-fechar-turno');
    btn.disabled = t.abastecimentos === 0;
    btn.style.opacity = t.abastecimentos === 0 ? '.5' : '1';

    if (!t.abastecimentos) {
        el('turno-lista').innerHTML =
            '<p class="turno-vazio">Nenhum abastecimento neste turno ainda.</p>';
        el('turno-por-produto').innerHTML = '';
        return;
    }

    el('turno-por-produto').innerHTML = `
        <p class="turno-sub">Por combustível</p>
        <table class="turno-tabela">
            <tbody>
            ${d.por_produto.map(p => `
                <tr>
                    <td>${esc(p.produto)} <span style="color:#94a3b8">(${p.vezes}×)</span></td>
                    <td class="num">${litros(p.quantidade)}</td>
                    <td class="num">${reais(p.liquido)}</td>
                </tr>`).join('')}
            </tbody>
        </table>`;

    el('turno-lista').innerHTML = `
        <p class="turno-sub">Abastecimentos</p>
        <table class="turno-tabela">
            <thead>
                <tr><th>Hora</th><th>Motorista / placa</th><th class="num">Litros</th>
                    <th class="num">Recebido</th></tr>
            </thead>
            <tbody>
            ${d.itens.map(i => `
                <tr>
                    <td>${esc(i.hora)}</td>
                    <td>${esc(i.cliente)}<br>
                        <span style="color:#94a3b8">${esc(formatarPlaca(i.placa))}</span></td>
                    <td class="num">${litros(i.quantidade)}</td>
                    <td class="num">${reais(i.liquido)}</td>
                </tr>`).join('')}
            </tbody>
        </table>`;
}

function formatarQuando(iso) {
    if (!iso) return '—';
    const [data, hora] = String(iso).split(' ');
    const [a, m, dia] = data.split('-');
    return `${dia}/${m}/${a}` + (hora ? ' ' + hora.slice(0, 5) : '');
}

// Quanto maior a lista, menor a fonte — para o turno inteiro caber numa folha.
//
// Os degraus não são chute: foram medidos imprimindo em A4 com margem de 12mm
// e conferindo quantas páginas saíam. O relatório tem um custo fixo de altura
// (cabeçalho, resumo por combustível, totais e assinaturas) que come quase
// metade da folha — por isso os limites são mais baixos do que a intuição diz.
//
// Medido de verdade: até 35 abastecimentos cabem numa folha. Acima disso,
// nem a menor fonte resolve — e aí o relatório quebra em duas páginas de
// propósito. Relatório ilegível é pior do que relatório em duas folhas.
function classeCompacta(quantidade) {
    if (quantidade <= 13) return '';
    if (quantidade <= 20) return 'compacto-1';
    if (quantidade <= 27) return 'compacto-2';
    return 'compacto-3';
}

function htmlRelatorioTurno(d, fechado) {
    const t = d.totais;
    const titulo = fechado ? 'Fechamento de turno' : 'Turno em andamento';
    const periodo = (d.turno_desde ? formatarQuando(d.turno_desde) : 'início')
        + ' até ' + (fechado ? formatarQuando(d.fechado_em) : d.agora);

    return `
    ${cabecalhoImpressao(titulo, d.poster_id, d.agora)}

    <div class="p-titulo">${esc(titulo)} — ${esc(d.operador)}</div>
    <div class="p-sub" style="margin-bottom:10px;">
        Período: ${esc(periodo)}
        ${d.poster_id ? ' · Posto ' + esc(d.poster_id) : ''}
        ${fechado && d.fechamento_id ? ' · Fechamento nº ' + d.fechamento_id : ''}
    </div>

    <table class="p-tabela">
        <thead>
            <tr>
                <th>#</th><th>Hora</th><th>Motorista</th><th>Placa</th>
                <th>Combustível</th><th class="num">Litros</th>
                <th class="num">Sem desc.</th><th class="num">Desconto</th>
                <th class="num">Recebido</th>
            </tr>
        </thead>
        <tbody>
        ${d.itens.map((i, n) => `
            <tr>
                <td>${n + 1}</td>
                <td>${esc(i.hora)}</td>
                <td>${esc(i.cliente)}</td>
                <td>${esc(formatarPlaca(i.placa))}</td>
                <td>${esc(i.produto)}</td>
                <td class="num">${(i.quantidade).toFixed(2).replace('.', ',')}</td>
                <td class="num">${(i.bruto).toFixed(2).replace('.', ',')}</td>
                <td class="num">${(i.desconto).toFixed(2).replace('.', ',')}</td>
                <td class="num">${(i.liquido).toFixed(2).replace('.', ',')}</td>
            </tr>`).join('')}
        </tbody>
    </table>

    <div class="p-titulo" style="font-size:12px;">Resumo por combustível</div>
    <table class="p-tabela">
        <thead>
            <tr><th>Combustível</th><th class="num">Vezes</th>
                <th class="num">Litros</th><th class="num">Recebido</th></tr>
        </thead>
        <tbody>
        ${d.por_produto.map(p => `
            <tr>
                <td>${esc(p.produto)}</td>
                <td class="num">${p.vezes}</td>
                <td class="num">${(p.quantidade).toFixed(2).replace('.', ',')}</td>
                <td class="num">${(p.liquido).toFixed(2).replace('.', ',')}</td>
            </tr>`).join('')}
        </tbody>
    </table>

    <div class="p-totais">
        <div class="p-linha"><span>Abastecimentos</span><span>${t.abastecimentos}</span></div>
        <div class="p-linha"><span>Litros vendidos</span><span>${(t.litros).toFixed(2).replace('.', ',')} L</span></div>
        <div class="p-linha"><span>Valor sem desconto</span><span>${reais(t.bruto)}</span></div>
        <div class="p-linha"><span>Desconto CAJ SKY concedido</span><span>− ${reais(t.desconto)}</span></div>
        <div class="p-linha forte"><span>TOTAL RECEBIDO NO TURNO</span><span>${reais(t.liquido)}</span></div>
    </div>

    <div class="p-assinaturas">
        <div>${esc(d.operador)} — atendente</div>
        <div>Conferido por</div>
    </div>

    <div class="p-rodape">
        Relatório gerado pelo sistema CAJ SKY em ${esc(d.agora)}.
        ${fechado ? 'Turno encerrado.' : 'Turno ainda em andamento — os valores podem mudar.'}
    </div>`;
}

function imprimirTurno() {
    if (!turnoAtual || !turnoAtual.totais.abastecimentos) {
        erro('turno-erro', 'Não há abastecimentos para imprimir neste turno.');
        return;
    }
    imprimirHtml(htmlRelatorioTurno(turnoAtual, false),
                 classeCompacta(turnoAtual.itens.length));
}

// ---------- fechamento ----------

function confirmarFechamento() {
    if (!turnoAtual || !turnoAtual.totais.abastecimentos) return;
    const t = turnoAtual.totais;
    const ok = confirm(
        `Fechar o turno?\n\n` +
        `${t.abastecimentos} abastecimento(s)\n` +
        `${litros(t.litros)}\n` +
        `Total recebido: ${reais(t.liquido)}\n\n` +
        `O relatório será impresso e você sairá do sistema.`);
    if (ok) fecharTurno();
}

async function fecharTurno() {
    const botao = el('btn-fechar-turno');
    botao.disabled = true;
    botao.textContent = 'Fechando…';
    erro('turno-erro', '');

    try {
        const d = await chamar('/frentista/fechar-turno', { method: 'POST' });

        // Imprime primeiro, ainda com a sessão na mão. Se a impressora
        // falhar, o turno já está fechado no servidor de qualquer forma —
        // e o relatório pode ser reimpresso pelo painel, pela gerência.
        imprimirHtml(htmlRelatorioTurno(d, true), classeCompacta(d.itens.length));

        // Dá tempo de a janela de impressão abrir antes de derrubar a tela.
        setTimeout(() => {
            sair('Turno fechado. Entre de novo para começar o próximo.');
        }, 1500);
    } catch (e) {
        if (e.message !== 'sessao_expirada') erro('turno-erro', e.message);
        botao.disabled = false;
        botao.textContent = '🔒 Fechar turno';
    }
}

// ---------- início ----------

window.addEventListener('DOMContentLoaded', () => {
    mostrarTela(restaurarSessao() ? 'tela-leitura' : 'tela-login');
});

// Se o frentista troca de app, a câmera fica presa: desliga ao sair da tela.
document.addEventListener('visibilitychange', () => {
    if (document.hidden && cameraLigada) pararCamera();
});
