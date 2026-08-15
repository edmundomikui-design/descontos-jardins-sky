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
    ['tela-login', 'tela-leitura', 'tela-cupom', 'tela-ok']
        .forEach(t => el(t).hidden = (t !== id));
    window.scrollTo(0, 0);
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

// ---------- início ----------

window.addEventListener('DOMContentLoaded', () => {
    mostrarTela(restaurarSessao() ? 'tela-leitura' : 'tela-login');
});

// Se o frentista troca de app, a câmera fica presa: desliga ao sair da tela.
document.addEventListener('visibilitychange', () => {
    if (document.hidden && cameraLigada) pararCamera();
});
