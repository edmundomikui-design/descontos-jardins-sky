// ===== PAINEL ADMINISTRATIVO - Jardins Sky =====

const API = 'https://descontos-jardins-sky-1.onrender.com/api';

let sessao = null;      // { token, usuario, nome, nivel }
let produtosAdmin = [];

// ===================== INICIALIZAÇÃO =====================
document.addEventListener('DOMContentLoaded', async () => {
    const salvo = localStorage.getItem('admin_sessao');
    if (salvo) {
        try {
            sessao = JSON.parse(salvo);
            abrirPainel();
            return;
        } catch (e) {
            localStorage.removeItem('admin_sessao');
        }
    }

    // Verifica se já existe administrador (primeiro acesso)
    try {
        const r = await fetch(`${API}/admin/existe`);
        const d = await r.json();
        if (!d.existe) {
            document.getElementById('aviso-primeiro-acesso').style.display = 'block';
            document.getElementById('btn-entrar').textContent = 'Criar acesso Master';
            document.getElementById('form-login').dataset.modo = 'setup';
        }
    } catch (e) {
        mostrarErroLogin('Não foi possível falar com o servidor. Ele pode estar iniciando — aguarde 1 minuto e recarregue.');
    }
});

// ===================== AUTENTICAÇÃO =====================
async function fazerLogin(evento) {
    evento.preventDefault();

    const usuario = document.getElementById('login-usuario').value.trim();
    const senha = document.getElementById('login-senha').value;
    const modo = document.getElementById('form-login').dataset.modo;
    const botao = document.getElementById('btn-entrar');

    botao.disabled = true;
    botao.textContent = 'Aguarde...';

    try {
        if (modo === 'setup') {
            const r = await fetch(`${API}/admin/setup`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ usuario, senha })
            });
            const d = await r.json();

            if (!r.ok) throw new Error(d.erro || 'Erro ao criar administrador');

            document.getElementById('form-login').dataset.modo = '';
            document.getElementById('aviso-primeiro-acesso').style.display = 'none';
            mostrarErroLogin('✅ Acesso Master criado! Entre agora com esse usuário e senha.', true);
            botao.disabled = false;
            botao.textContent = 'Entrar';
            return false;
        }

        const r = await fetch(`${API}/admin/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ usuario, senha })
        });
        const d = await r.json();

        if (!r.ok) throw new Error(d.erro || 'Erro ao entrar');

        sessao = { token: d.token, usuario: d.usuario, nome: d.nome, nivel: d.nivel };
        localStorage.setItem('admin_sessao', JSON.stringify(sessao));
        abrirPainel();
    } catch (erro) {
        mostrarErroLogin(erro.message);
    } finally {
        botao.disabled = false;
        if (document.getElementById('form-login').dataset.modo !== 'setup') {
            botao.textContent = 'Entrar';
        }
    }

    return false;
}

function mostrarErroLogin(mensagem, sucesso = false) {
    const el = document.getElementById('login-erro');
    el.textContent = mensagem;
    el.className = sucesso ? 'msg-erro sucesso' : 'msg-erro';
    el.style.display = 'block';
}

function sair() {
    localStorage.removeItem('admin_sessao');
    location.reload();
}

function abrirPainel() {
    document.getElementById('tela-login').style.display = 'none';
    document.getElementById('painel').style.display = 'block';

    const nivel = sessao.nivel;
    const ehMaster = nivel === 'master';
    const podeAlterar = nivel === 'master' || nivel === 'gerencia';

    document.getElementById('badge-usuario').textContent =
        `${sessao.nome || sessao.usuario} · ${rotuloNivel(nivel)}`;
    document.getElementById('badge-usuario').className = `badge badge-${nivel}`;

    // Caixa não altera nada; Gerência não mexe em usuários
    document.querySelectorAll('.somente-master').forEach(el => {
        el.style.display = ehMaster ? '' : 'none';
    });
    document.querySelectorAll('.somente-gerencia').forEach(el => {
        el.style.display = podeAlterar ? '' : 'none';
    });

    document.getElementById('caixa-data').value = new Date().toISOString().slice(0, 10);
    carregarCaixa();
}

// Requisição autenticada
async function api(caminho, opcoes = {}) {
    const r = await fetch(`${API}${caminho}`, {
        ...opcoes,
        headers: {
            'Content-Type': 'application/json',
            'X-Admin-Token': sessao.token,
            ...(opcoes.headers || {})
        }
    });

    const d = await r.json().catch(() => ({}));

    if (r.status === 401) {
        alert('Sua sessão expirou. Faça login novamente.');
        sair();
        throw new Error('Sessão expirada');
    }

    if (!r.ok) throw new Error(d.erro || `Erro ${r.status}`);
    return d;
}

function aviso(mensagem, tipo = 'ok') {
    const el = document.getElementById('msg-global');
    el.textContent = mensagem;
    el.className = `msg-global visivel ${tipo}`;
    setTimeout(() => { el.className = 'msg-global'; }, 5000);
}

// ===================== ABAS =====================
function trocarAba(nome) {
    document.querySelectorAll('.aba').forEach(b => b.classList.toggle('ativa', b.dataset.aba === nome));
    document.querySelectorAll('.conteudo').forEach(s => s.style.display = 'none');
    document.getElementById(`aba-${nome}`).style.display = 'block';

    if (nome === 'precos') carregarProdutosAdmin();
    if (nome === 'auditoria') carregarAuditoria();
    if (nome === 'usuarios') carregarUsuarios();
    if (nome === 'caixa') carregarCaixa();
    if (nome === 'suspeitas') carregarSuspeitas();
    if (nome === 'convenios') carregarConvenios();
    if (nome === 'cupons') abrirCuponsDoDia();
    else pararAutoCupons();   // não fica batendo na API numa aba que ninguém vê
}

// ===================== CUPONS DO DIA (tela do caixa) =====================
//
// Fica aberta o dia todo no guichê. Duas funções: ler o cupom do motorista e
// dar baixa, e mostrar o movimento do dia.
//
// A trava contra reuso NÃO está aqui — está no saldo gravado no banco, que o
// /api/cupom/usar confere a cada baixa. Esta tela é para enxergar e agir
// rápido, não para vigiar.

let cuponsDoDia = [];
let timerCupons = null;
let cupomAberto = null;

function abrirCuponsDoDia() {
    const campoData = document.getElementById('cupons-data');
    if (campoData && !campoData.value) {
        campoData.value = new Date().toISOString().slice(0, 10);
    }
    carregarCuponsDoDia();
    alternarAutoCupons();
    focarLeitura();
}

function focarLeitura() {
    const campo = document.getElementById('cupom-codigo');
    if (campo) { campo.focus(); campo.select(); }
}

function pararAutoCupons() {
    if (timerCupons) { clearInterval(timerCupons); timerCupons = null; }
}

function alternarAutoCupons() {
    pararAutoCupons();
    const ligado = document.getElementById('cupons-auto');
    const abaAtiva = document.getElementById('aba-cupons');
    if (ligado && ligado.checked && abaAtiva && abaAtiva.style.display !== 'none') {
        // 20s: rápido o bastante para o caixa acompanhar, devagar o bastante
        // para não castigar o servidor no plano gratuito.
        timerCupons = setInterval(() => carregarCuponsDoDia(true), 20000);
    }
}

async function carregarCuponsDoDia(silencioso = false) {
    const dia = document.getElementById('cupons-data').value ||
                new Date().toISOString().slice(0, 10);
    const alvo = document.getElementById('cupons-conteudo');

    try {
        const d = await api(`/admin/cupons-do-dia?data=${encodeURIComponent(dia)}`);
        cuponsDoDia = d.cupons || [];
        renderizarResumoCupons(d.resumo || {});
        renderizarCuponsDoDia();

        const agora = new Date();
        document.getElementById('cupons-atualizado').textContent =
            'Atualizado às ' + String(agora.getHours()).padStart(2, '0') + ':' +
            String(agora.getMinutes()).padStart(2, '0') + ':' +
            String(agora.getSeconds()).padStart(2, '0');
    } catch (e) {
        if (!silencioso) alvo.innerHTML = `<p class="vazio">Não consegui carregar: ${escapar(e.message)}</p>`;
    }
}

function renderizarResumoCupons(r) {
    document.getElementById('cupons-resumo').innerHTML = `
        <div class="tile"><span class="rotulo">Cupons gerados</span><strong>${r.total || 0}</strong></div>
        <div class="tile"><span class="rotulo">Sem uso</span><strong>${r.emitidos || 0}</strong></div>
        <div class="tile"><span class="rotulo">Parciais</span><strong>${r.parciais || 0}</strong></div>
        <div class="tile"><span class="rotulo">Esgotados</span><strong>${r.esgotados || 0}</strong></div>
        <div class="tile"><span class="rotulo">Litros abastecidos</span><strong>${(r.litros_abastecidos || 0).toFixed(2)}</strong></div>
        <div class="tile"><span class="rotulo">Desconto concedido</span><strong>R$ ${(r.desconto_concedido || 0).toFixed(2)}</strong></div>`;
}

const ROTULO_SITUACAO = {
    emitido:  { texto: 'Sem uso',  cor: '#546e7a' },
    parcial:  { texto: 'Parcial',  cor: '#ef6c00' },
    esgotado: { texto: 'Esgotado', cor: '#2e7d32' }
};

function renderizarCuponsDoDia() {
    const alvo = document.getElementById('cupons-conteudo');
    const filtro = document.getElementById('cupons-filtro').value;
    const busca = (document.getElementById('cupons-busca').value || '').trim().toLowerCase();

    let lista = cuponsDoDia;
    if (filtro) lista = lista.filter(c => c.situacao === filtro);
    if (busca) {
        lista = lista.filter(c =>
            (c.cliente_nome || '').toLowerCase().includes(busca) ||
            (c.placa || '').toLowerCase().includes(busca) ||
            (c.codigo || '').toLowerCase().includes(busca));
    }

    if (!lista.length) {
        alvo.innerHTML = '<p class="vazio">' +
            (cuponsDoDia.length ? 'Nenhum cupom com esse filtro.'
                                : 'Nenhum cupom gerado neste dia ainda.') + '</p>';
        return;
    }

    alvo.innerHTML = `
        <table class="tabela">
            <thead>
                <tr>
                    <th>Situação</th><th>Motorista</th><th>Placa</th><th>Produto</th>
                    <th>Usado</th><th>Saldo</th><th>Último uso</th><th>Código</th><th></th>
                </tr>
            </thead>
            <tbody>
                ${lista.map(c => {
                    const s = ROTULO_SITUACAO[c.situacao] || ROTULO_SITUACAO.emitido;
                    const postos = c.postos && c.postos.length ? c.postos.join(', ') : '';
                    const ultimo = c.ultima_hora
                        ? `${escapar(c.ultima_hora)}${postos ? ' · ' + escapar(postos) : ''}`
                        : '—';
                    return `
                    <tr>
                        <td><span class="badge" style="background:${s.cor}; color:#fff;">${s.texto}</span></td>
                        <td>${escapar(c.cliente_nome || '—')}
                            ${c.empresa_convenio ? `<br><small>${escapar(c.empresa_convenio)}</small>` : ''}</td>
                        <td>${escapar(c.placa || '—')}</td>
                        <td>${escapar((c.produto_icone || '') + ' ' + (c.produto_nome || '—'))}</td>
                        <td>${c.quantidade_utilizada.toFixed(2)} ${escapar(c.unidade)}</td>
                        <td><strong>${c.situacao === 'esgotado'
                            ? '—'
                            : c.quantidade_restante.toFixed(2) + ' ' + escapar(c.unidade)}</strong></td>
                        <td>${ultimo}</td>
                        <td><code style="font-size:11px;">${escapar(c.codigo)}</code></td>
                        <td>${c.situacao !== 'esgotado'
                            ? `<button class="btn" onclick="consultarCodigo('${escapar(c.codigo)}')">Dar baixa</button>`
                            : ''}</td>
                    </tr>`;
                }).join('')}
            </tbody>
        </table>`;
}

// ---------- leitura e baixa ----------

function lerCupomCaixa(evento) {
    evento.preventDefault();
    const codigo = document.getElementById('cupom-codigo').value.trim();
    if (!codigo) {
        document.getElementById('cupom-leitura-erro').textContent = 'Digite ou leia o código.';
        return false;
    }
    consultarCodigo(codigo);
    return false;
}

async function consultarCodigo(codigo) {
    document.getElementById('cupom-leitura-erro').textContent = '';
    document.getElementById('baixa-erro').textContent = '';

    try {
        const c = await api('/cupom/consultar?qrcode=' + encodeURIComponent(codigo));
        cupomAberto = c;
        document.getElementById('cupom-codigo').value = '';
        exibirCupomCaixa(c);
    } catch (e) {
        cupomAberto = null;
        document.getElementById('card-cupom-aberto').style.display = 'none';
        document.getElementById('cupom-leitura-erro').textContent = e.message;
        focarLeitura();
    }
}

function exibirCupomCaixa(c) {
    const card = document.getElementById('card-cupom-aberto');
    const faixa = document.getElementById('cupom-faixa');
    const bloco = document.getElementById('bloco-baixa');
    card.style.display = 'block';

    const alertaPlaca = c.placa_em_varios_cadastros
        ? `<p style="color:#c62828; margin:8px 0;">⚠ Esta placa está em ${c.placa_qtd_cadastros}
             cadastros. Pode ser táxi dividido por turno — confira o motorista.</p>`
        : '';

    document.getElementById('cupom-detalhe').innerHTML = `
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-top:10px;">
            <div><small>Motorista</small><br><strong>${escapar(c.cliente_nome || '—')}</strong></div>
            <div><small>Placa</small><br><strong style="font-size:19px;">${escapar(c.placa || '—')}</strong></div>
            <div><small>Produto</small><br><strong>${escapar((c.produto_icone || '') + ' ' + (c.produto_nome || '—'))}</strong></div>
            <div><small>Preço na bomba</small><br><strong>R$ ${(c.preco_bomba || 0).toFixed(2)}</strong></div>
            <div><small>Preço com desconto</small><br><strong style="color:#2e7d32;">R$ ${(c.preco_com_desconto || 0).toFixed(2)}</strong></div>
            <div><small>Saldo do cupom</small><br><strong style="font-size:19px;">${(c.quantidade_restante || 0).toFixed(2)} ${escapar(c.unidade || 'L')}</strong></div>
        </div>
        ${alertaPlaca}`;

    if (c.valido) {
        faixa.className = 'faixa ok';
        faixa.textContent = c.uso_unico
            ? '✓ Cupom válido — atenção: vale para UM abastecimento só'
            : '✓ Cupom válido — pode abastecer';
        bloco.style.display = 'block';

        // O caixa precisa avisar o motorista ANTES de encher, senão a
        // reclamação vem depois — e com razão.
        const nota = document.getElementById('nota-uso-unico');
        if (nota) {
            nota.hidden = !c.uso_unico;
            nota.textContent = c.uso_unico
                ? `⚠ Avise o motorista: o que sobrar dos ${(c.quantidade_restante || 0).toFixed(2)} ` +
                  `${c.unidade || 'L'} não fica para depois. O cupom encerra nesta baixa.`
                : '';
        }

        document.getElementById('baixa-saldo').textContent =
            (c.quantidade_restante || 0).toFixed(2) + ' ' + (c.unidade || 'L');
        const campo = document.getElementById('baixa-litros');
        campo.value = '';
        campo.max = c.quantidade_restante;
        campo.focus();
    } else {
        faixa.className = 'faixa erro';
        faixa.textContent = '✖ ' + (c.motivo || 'Cupom não pode ser usado');
        bloco.style.display = 'none';
    }
}

function usarSaldoTotalCaixa() {
    if (!cupomAberto) return;
    document.getElementById('baixa-litros').value = cupomAberto.quantidade_restante;
}

function fecharCupomCaixa() {
    cupomAberto = null;
    document.getElementById('card-cupom-aberto').style.display = 'none';
    focarLeitura();
}

async function confirmarBaixaCaixa() {
    if (!cupomAberto) return;

    const erroEl = document.getElementById('baixa-erro');
    const botao = document.getElementById('btn-baixa');
    erroEl.textContent = '';

    const litros = parseFloat(document.getElementById('baixa-litros').value);
    const valor = parseFloat(document.getElementById('baixa-valor').value) || 0;

    if (!litros || litros <= 0) {
        erroEl.textContent = 'Informe quantos litros foram abastecidos.';
        return;
    }
    if (litros > cupomAberto.quantidade_restante + 0.001) {
        erroEl.textContent = `Excede o saldo: restam ${cupomAberto.quantidade_restante.toFixed(2)} ${cupomAberto.unidade || 'L'}.`;
        return;
    }

    botao.disabled = true;
    botao.textContent = 'Registrando…';

    try {
        const d = await api('/cupom/usar', {
            method: 'POST',
            body: JSON.stringify({
                qrcode: cupomAberto.qrcode,
                produto_id: cupomAberto.produto_id,
                quantidade: litros,
                valor_sem_desconto: valor
            })
        });

        aviso(`Baixa registrada: ${litros.toFixed(2)} ${cupomAberto.unidade || 'L'} · ` +
              `saldo restante ${(d.quantidade_restante ?? 0).toFixed(2)}`);
        fecharCupomCaixa();
        carregarCuponsDoDia();
    } catch (e) {
        erroEl.textContent = e.message;
    } finally {
        botao.disabled = false;
        botao.textContent = '✅ Dar baixa';
    }
}

// ===================== CONVÊNIOS COM EMPRESAS =====================
//
// A empresa deixou de ser texto livre no cadastro do cliente. Aqui a gerência
// controla as duas travas: quem entra na lista (convênio assinado) e quem
// passa da fila (vínculo conferido).

function escapar(txt) {
    return String(txt == null ? '' : txt)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function formatarCnpjCampo(valor) {
    const n = String(valor || '').replace(/\D/g, '').slice(0, 14);
    if (n.length <= 2) return n;
    if (n.length <= 5) return `${n.slice(0,2)}.${n.slice(2)}`;
    if (n.length <= 8) return `${n.slice(0,2)}.${n.slice(2,5)}.${n.slice(5)}`;
    if (n.length <= 12) return `${n.slice(0,2)}.${n.slice(2,5)}.${n.slice(5,8)}/${n.slice(8)}`;
    return `${n.slice(0,2)}.${n.slice(2,5)}.${n.slice(5,8)}/${n.slice(8,12)}-${n.slice(12)}`;
}

async function carregarConvenios() {
    await Promise.all([carregarPendentes(), carregarEmpresas()]);
}

async function carregarPendentes() {
    const alvo = document.getElementById('pendentes-conteudo');
    try {
        const d = await api('/admin/cadastros-pendentes');
        atualizarSeloPendentes(d.total || 0);
        window.pendentesCache = d.pendentes || [];

        if (!d.pendentes || !d.pendentes.length) {
            alvo.innerHTML = '<p class="vazio">Nenhum cadastro aguardando. Tudo em dia.</p>';
            return;
        }

        const souMaster = (d.meu_nivel || sessao.nivel) === 'master';

        alvo.innerHTML = d.pendentes.map(p => {
            const sinalEmail = p.email_corporativo
                ? '<span style="color:#0ca30c;">✓ e-mail corporativo confere</span>'
                : `<span style="color:#e65100;">⚠ e-mail não é da empresa` +
                  `${p.empresa_dominio ? ' (esperado @' + escapar(p.empresa_dominio) + ')' : ''}</span>`;
            const sinalFoto = p.tem_comprovante
                ? `<button class="btn" onclick="verComprovante(${p.id})">Ver comprovante</button>`
                : '<span style="color:#c62828;">sem comprovante</span>';

            // Exceção ao e-mail corporativo é alçada do Master. Se quem está
            // olhando é gerência, o botão some e a tela diz o porquê — botão
            // que existe e dá erro ao clicar é pior do que botão nenhum.
            const travadoParaMim = p.exige_master && !souMaster;

            const avisoMaster = p.exige_master
                ? `<p style="background:#fff3e0; border-left:4px solid #FF9800; padding:8px 10px;
                          border-radius:6px; margin:8px 0; font-size:13px; color:#e65100;">
                     🔒 Exceção: sem e-mail corporativo.
                     ${travadoParaMim
                        ? 'Só o administrador Master pode liberar este cadastro.'
                        : 'Confira bem o comprovante antes de aprovar — a prova de vínculo mais forte não veio.'}
                   </p>`
                : '';

            const botoesDecisao = travadoParaMim
                ? `<span style="color:#888; font-size:13px; align-self:center;">
                     Aguardando o Master
                   </span>`
                : `<button class="btn btn-primary" onclick="decidirCadastro(${p.id}, 'aprovar', '${escapar(p.nome)}')">
                       ✅ Aprovar
                   </button>`;

            return `
            <div class="card" style="margin-bottom:12px;${p.exige_master ? ' border-left:4px solid #FF9800;' : ''}">
                <h4 style="margin:0 0 6px;">${escapar(p.nome)}</h4>
                <p style="margin:2px 0; font-size:14px;">
                    <strong>${escapar(p.empresa || '—')}</strong>
                    ${p.empresa_cnpj ? ` · CNPJ ${escapar(p.empresa_cnpj)}` : ''}
                </p>
                <p style="margin:2px 0; font-size:13px; color:#555;">
                    ${escapar(p.email)} · ${escapar(p.tel || '')} · placa ${escapar(p.placa || '—')}
                </p>
                <p style="margin:6px 0; font-size:13px;">${sinalEmail}</p>
                ${avisoMaster}
                <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;">
                    ${sinalFoto}
                    ${botoesDecisao}
                    <button class="btn" style="background:#c62828; color:#fff;"
                            onclick="decidirCadastro(${p.id}, 'recusar', '${escapar(p.nome)}')">
                        ✖ Recusar
                    </button>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        alvo.innerHTML = `<p class="vazio">Não consegui carregar: ${escapar(e.message)}</p>`;
    }
}

function atualizarSeloPendentes(total) {
    const selo = document.getElementById('selo-pendentes');
    if (!selo) return;
    selo.textContent = total;
    selo.hidden = !total;
}

async function decidirCadastro(clienteId, decisao, nome) {
    let motivo = null;

    if (decisao === 'recusar') {
        motivo = prompt(`Por que o cadastro de ${nome} está sendo recusado?\n\n` +
                        `A pessoa vai ver esse texto no aplicativo.`);
        if (motivo === null) return;
        if (!motivo.trim()) {
            aviso('É preciso escrever o motivo da recusa.', 'erro');
            return;
        }
    } else {
        const p = (window.pendentesCache || []).find(x => x.id === clienteId);
        const extra = p && p.exige_master
            ? `\n\n⚠ ATENÇÃO: esta pessoa NÃO usou o e-mail corporativo` +
              `${p.empresa_dominio ? ' (@' + p.empresa_dominio + ')' : ''}. ` +
              `Você está abrindo uma exceção — confira o comprovante de vínculo.`
            : '';
        if (!confirm(`Aprovar o cadastro de ${nome}?\n\n` +
                     `Ele passa a gerar cupons com desconto imediatamente.${extra}`)) {
            return;
        }
    }

    try {
        const d = await api(`/admin/cadastros/${clienteId}/decidir`, {
            method: 'POST',
            body: JSON.stringify({ decisao, motivo })
        });
        aviso(d.mensagem);
        carregarConvenios();
    } catch (e) {
        aviso(e.message, 'erro');
    }
}

async function cadastrarEmpresaConvenio() {
    const nome = document.getElementById('empresa-nome').value.trim();
    const cnpj = document.getElementById('empresa-cnpj').value.trim();
    const dominio = document.getElementById('empresa-dominio').value.trim();
    const limite = document.getElementById('empresa-limite').value;

    if (nome.length < 3) return aviso('Informe o nome da empresa.', 'erro');
    if (cnpj.replace(/\D/g, '').length !== 14) return aviso('CNPJ precisa ter 14 números.', 'erro');

    try {
        const d = await api('/admin/empresas-convenio', {
            method: 'POST',
            body: JSON.stringify({
                nome, cnpj,
                dominio_email: dominio || null,
                limite_funcionarios: Number(limite) || 0
            })
        });
        aviso(d.mensagem);
        ['empresa-nome', 'empresa-cnpj', 'empresa-dominio'].forEach(
            id => document.getElementById(id).value = '');
        document.getElementById('empresa-limite').value = 0;
        carregarEmpresas();
    } catch (e) {
        aviso(e.message, 'erro');
    }
}

async function carregarEmpresas() {
    const alvo = document.getElementById('empresas-conteudo');
    try {
        const d = await api('/admin/empresas-convenio');

        if (!d.empresas || !d.empresas.length) {
            alvo.innerHTML = '<p class="vazio">Nenhuma empresa cadastrada. ' +
                'Enquanto não houver, a opção "Outro — convênio" não lista nada no aplicativo.</p>';
            return;
        }

        alvo.innerHTML = `
            <table class="tabela">
                <thead>
                    <tr>
                        <th>Empresa</th><th>CNPJ</th><th>E-mail exigido</th>
                        <th>Aprovados</th><th>Na fila</th><th>Limite</th><th></th>
                    </tr>
                </thead>
                <tbody>
                    ${d.empresas.map(e => `
                        <tr style="${e.ativo ? '' : 'opacity:.5;'}">
                            <td>${escapar(e.nome)}${e.ativo ? '' : ' <small>(encerrado)</small>'}</td>
                            <td>${escapar(e.cnpj)}</td>
                            <td>${e.dominio_email ? '@' + escapar(e.dominio_email) : '—'}</td>
                            <td>${e.aprovados}</td>
                            <td>${e.pendentes}</td>
                            <td>${e.limite_funcionarios || 'sem teto'}</td>
                            <td>
                                <button class="btn" onclick="alternarConvenio(${e.id}, ${e.ativo ? 0 : 1}, '${escapar(e.nome)}')">
                                    ${e.ativo ? 'Encerrar' : 'Reativar'}
                                </button>
                            </td>
                        </tr>`).join('')}
                </tbody>
            </table>`;
    } catch (e) {
        alvo.innerHTML = `<p class="vazio">Não consegui carregar: ${escapar(e.message)}</p>`;
    }
}

async function alternarConvenio(empresaId, ativo, nome) {
    const acao = ativo ? 'reativar' : 'encerrar';
    if (!confirm(`Deseja ${acao} o convênio da ${nome}?\n\n` +
                 (ativo ? 'Ela volta a aparecer no cadastro do aplicativo.'
                        : 'Ela some do cadastro do aplicativo. Quem já está aprovado continua usando.'))) {
        return;
    }
    try {
        const d = await api(`/admin/empresas-convenio/${empresaId}`, {
            method: 'POST',
            body: JSON.stringify({ ativo })
        });
        aviso(d.mensagem);
        carregarEmpresas();
    } catch (e) {
        aviso(e.message, 'erro');
    }
}

// ===================== FECHAMENTO DE CAIXA =====================
function periodoRapido(inicio, fim) {
    document.getElementById('caixa-hora-inicio').value = inicio;
    document.getElementById('caixa-hora-fim').value = fim;
    carregarCaixa();
}

async function carregarCaixa() {
    const data = document.getElementById('caixa-data').value;
    const posto = document.getElementById('caixa-posto').value;
    const horaInicio = document.getElementById('caixa-hora-inicio').value;
    const horaFim = document.getElementById('caixa-hora-fim').value;
    const produto = document.getElementById('caixa-produto').value;

    document.getElementById('resumo-geral').innerHTML = '<p class="carregando">Carregando...</p>';
    document.getElementById('turnos-container').innerHTML = '';

    try {
        const params = new URLSearchParams({ data });
        if (posto) params.append('poster_id', posto);
        if (horaInicio) params.append('hora_inicio', horaInicio);
        if (horaFim) params.append('hora_fim', horaFim);
        if (produto) params.append('produto_id', produto);

        const d = await api(`/admin/caixa?${params}`);
        renderizarCaixa(d);
        preencherFiltroProdutos();
    } catch (erro) {
        document.getElementById('resumo-geral').innerHTML =
            `<p class="msg-erro">${erro.message}</p>`;
    }
}

// preenche o seletor de produtos uma única vez
async function preencherFiltroProdutos() {
    const select = document.getElementById('caixa-produto');
    if (select.dataset.carregado) return;

    try {
        const r = await fetch(`${API}/produtos`);
        const d = await r.json();
        select.innerHTML = '<option value="">Todos</option>' +
            d.produtos.map(p => `<option value="${p.id}">${p.icone || ''} ${p.nome}</option>`).join('');
        select.dataset.carregado = '1';
    } catch (e) { /* silencioso */ }
}

function imprimirRelatorio() {
    const data = document.getElementById('caixa-data').value;
    const ini = document.getElementById('caixa-hora-inicio').value;
    const fim = document.getElementById('caixa-hora-fim').value;
    const posto = document.getElementById('caixa-posto').value;

    const periodo = ini || fim
        ? `Período: ${ini || '00:00'} às ${fim || '23:59'}`
        : 'Período: dia inteiro';

    // cabeçalho que só aparece na impressão
    let cabecalho = document.getElementById('cabecalho-impressao');
    if (!cabecalho) {
        cabecalho = document.createElement('div');
        cabecalho.id = 'cabecalho-impressao';
        document.getElementById('painel').prepend(cabecalho);
    }

    cabecalho.innerHTML = `
        <h2>Relatório de Caixa — Jardins Sky</h2>
        <p>Data: <strong>${(data || '').split('-').reverse().join('/')}</strong> ·
           ${periodo} ·
           Posto: <strong>${posto || 'CAJ e SKY'}</strong></p>
        <p class="emitido">Emitido por ${sessao.nome || sessao.usuario} em ${new Date().toLocaleString('pt-BR')}</p>
    `;

    window.print();
}

function reais(v) {
    return (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function litros(v) {
    return `${(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} L`;
}

function renderizarCaixa(d) {
    const t = d.total;

    document.getElementById('resumo-geral').innerHTML = `
        <div class="cartoes">
            <div class="cartao">
                <span class="rotulo">Abastecimentos</span>
                <strong class="valor">${t.abastecimentos}</strong>
            </div>
            <div class="cartao">
                <span class="rotulo">Litros vendidos</span>
                <strong class="valor">${litros(t.litros)}</strong>
            </div>
            <div class="cartao destaque">
                <span class="rotulo">Recebido no caixa</span>
                <strong class="valor">${reais(t.valor_recebido)}</strong>
            </div>
            <div class="cartao">
                <span class="rotulo">Desconto concedido</span>
                <strong class="valor negativo">- ${reais(t.desconto_concedido)}</strong>
            </div>
        </div>
        <p class="nota">Data: <strong>${d.data.split('-').reverse().join('/')}</strong> · Turno agora: ${d.turno_atual}</p>
    `;

    const f = d.filtros || {};
    const janela = (f.hora_inicio || f.hora_fim)
        ? `das ${(f.hora_inicio || '00:00:00').slice(0, 5)} às ${(f.hora_fim || '23:59:59').slice(0, 5)}`
        : 'dia inteiro';
    document.getElementById('titulo-abastecimentos').textContent =
        `Abastecimentos — ${janela} (${d.detalhes.length})`;

    if (!d.turnos.length) {
        document.getElementById('turnos-container').innerHTML = '';
        document.getElementById('tabela-detalhes').innerHTML =
            '<p class="vazio">Nenhum abastecimento neste período.</p>';
        return;
    }

    document.getElementById('turnos-container').innerHTML = d.turnos.map(t => `
        <div class="card turno">
            <div class="turno-topo">
                <h3>${t.turno}</h3>
                <div class="turno-numeros">
                    <span><strong>${t.abastecimentos}</strong> abast.</span>
                    <span><strong>${litros(t.litros)}</strong></span>
                    <span class="recebido"><strong>${reais(t.valor_recebido)}</strong></span>
                </div>
            </div>

            <table class="tabela">
                <thead>
                    <tr><th>Produto</th><th>Abast.</th><th>Litros</th><th>Recebido</th></tr>
                </thead>
                <tbody>
                    ${t.por_produto.map(p => `
                        <tr>
                            <td>${p.icone || ''} ${p.produto}</td>
                            <td>${p.abastecimentos}</td>
                            <td>${litros(p.litros)}</td>
                            <td>${reais(p.valor_recebido)}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>

            <div class="por-posto">
                ${t.por_posto.map(p => `
                    <span class="chip">${p.posto}: ${litros(p.litros)} · ${reais(p.valor_recebido)}</span>
                `).join('')}
            </div>

            <div class="linha-desconto">
                Bruto ${reais(t.valor_bruto)} − desconto ${reais(t.desconto_concedido)} =
                <strong>${reais(t.valor_recebido)}</strong>
            </div>
        </div>
    `).join('');

    const somaLitros = d.detalhes.reduce((s, r) => s + (r.quantidade || 0), 0);
    const somaPago = d.detalhes.reduce((s, r) => s + (r.valor_final || 0), 0);
    const somaDesc = d.detalhes.reduce((s, r) => s + (r.valor_desconto || 0), 0);

    document.getElementById('tabela-detalhes').innerHTML = `
        <table class="tabela">
            <thead>
                <tr><th>Hora</th><th>Posto</th><th>Combustível</th><th>Cliente</th>
                    <th class="num">Litros</th><th class="num">Preço bomba</th>
                    <th class="num">Desconto</th><th class="num">Valor cobrado</th></tr>
            </thead>
            <tbody>
                ${d.detalhes.map(r => `
                    <tr>
                        <td><strong>${(r.hora || '').slice(0, 5)}</strong></td>
                        <td>${r.posto || ''}</td>
                        <td>${r.produto || ''}</td>
                        <td>${r.cliente || ''}</td>
                        <td class="num">${litros(r.quantidade)}</td>
                        <td class="num">${reais(r.valor_original)}</td>
                        <td class="num negativo">- ${reais(r.valor_desconto)}</td>
                        <td class="num"><strong>${reais(r.valor_final)}</strong></td>
                    </tr>
                `).join('')}
            </tbody>
            <tfoot>
                <tr>
                    <td colspan="4"><strong>Total do período</strong></td>
                    <td class="num"><strong>${litros(somaLitros)}</strong></td>
                    <td class="num"></td>
                    <td class="num negativo"><strong>- ${reais(somaDesc)}</strong></td>
                    <td class="num total-caixa"><strong>${reais(somaPago)}</strong></td>
                </tr>
            </tfoot>
        </table>
    `;
}

// ===================== PREÇOS E DESCONTOS =====================
async function carregarProdutosAdmin() {
    const container = document.getElementById('produtos-admin');
    container.innerHTML = '<p class="carregando">Carregando produtos...</p>';

    try {
        const d = await api('/admin/produtos');
        produtosAdmin = d.produtos;
        renderizarProdutosAdmin();
    } catch (erro) {
        container.innerHTML = `<p class="msg-erro">${erro.message}</p>`;
    }
}

function renderizarProdutosAdmin() {
    const grupos = {
        combustivel: produtosAdmin.filter(p => p.tipo === 'combustivel'),
        oleo: produtosAdmin.filter(p => p.tipo === 'oleo')
    };

    const ehMaster = sessao.nivel === 'master';

    const linha = p => `
        <div class="produto-linha" data-id="${p.id}"
             data-custo="${p.preco_custo || 0}" data-margem="${p.margem_minima ?? 10}">
            <div class="produto-titulo">
                <span class="icone">${p.icone || ''}</span>
                <input type="text" class="in-nome" value="${p.nome.replace(/"/g, '&quot;')}"
                       title="Clique para renomear o produto" ${ehMaster ? '' : 'readonly'}>
            </div>

            <div class="campo campo-custo">
                <label>Preço de custo ${ehMaster ? '' : '🔒'}</label>
                <div class="input-prefixo">
                    <span>R$</span>
                    <input type="number" step="0.01" min="0" class="in-custo" value="${p.preco_custo || 0}"
                           oninput="recalcularLinha(${p.id})" ${ehMaster ? '' : 'readonly'}>
                </div>
            </div>

            <div class="campo">
                <label>Preço de bomba</label>
                <div class="input-prefixo">
                    <span>R$</span>
                    <input type="number" step="0.01" min="0" class="in-preco" value="${p.preco_atual}"
                           oninput="recalcularLinha(${p.id})">
                </div>
            </div>

            <div class="campo">
                <label>Desconto</label>
                <div class="input-duplo">
                    <input type="number" step="0.01" min="0" class="in-desconto" value="${p.desconto_valor}"
                           oninput="recalcularLinha(${p.id})">
                    <select class="in-tipo" onchange="recalcularLinha(${p.id})">
                        <option value="fixo" ${p.desconto_tipo === 'fixo' ? 'selected' : ''}>R$ por ${p.unidade}</option>
                        <option value="percentual" ${p.desconto_tipo === 'percentual' ? 'selected' : ''}>% do preço</option>
                    </select>
                </div>
                <small class="dica-desconto" id="dica-${p.id}"></small>
            </div>

            <div class="campo">
                <label>Limite (${p.unidade})</label>
                <input type="number" step="1" min="0" class="in-limite" value="${p.limite_litros}">
            </div>

            ${ehMaster ? `
            <div class="campo">
                <label>Margem mín. (%)</label>
                <input type="number" step="1" min="0" class="in-margem" value="${p.margem_minima ?? 10}"
                       oninput="recalcularLinha(${p.id})" title="Piso que a Gerência precisa respeitar">
            </div>` : ''}

            <div class="campo resultado">
                <label>Cliente paga</label>
                <strong class="preco-final" id="final-${p.id}">${reais(p.preco_final)}</strong>
                <span class="por-unidade">por ${p.unidade}</span>
                <span class="margem-info" id="margem-${p.id}"></span>
            </div>

            <div class="campo campo-ativo">
                <label class="switch">
                    <input type="checkbox" class="in-ativo" ${p.ativo ? 'checked' : ''}>
                    <span>Ativo</span>
                </label>
            </div>
        </div>
    `;

    document.getElementById('produtos-admin').innerHTML = `
        <div class="card">
            <h3>⛽ Combustíveis</h3>
            ${grupos.combustivel.map(linha).join('')}
        </div>
        <div class="card">
            <h3>🛢️ Óleos</h3>
            ${grupos.oleo.map(linha).join('')}
        </div>
    `;

    produtosAdmin.forEach(p => recalcularLinha(p.id));
}

// ===================== AUDITORIA =====================
async function carregarAuditoria() {
    const container = document.getElementById('lista-auditoria');
    container.innerHTML = '<p class="carregando">Carregando histórico...</p>';

    try {
        const params = new URLSearchParams();
        const ini = document.getElementById('audit-inicio').value;
        const fim = document.getElementById('audit-fim').value;
        const acao = document.getElementById('audit-acao').value;
        if (ini) params.append('data_inicio', ini);
        if (fim) params.append('data_fim', fim);
        if (acao) params.append('acao', acao);

        const d = await api(`/admin/auditoria?${params}`);

        if (!d.registros.length) {
            container.innerHTML = '<p class="vazio">Nenhuma alteração registrada neste período.</p>';
            return;
        }

        container.innerHTML = `
            <table class="tabela">
                <thead>
                    <tr><th>Quando</th><th>Quem</th><th>Produto</th><th>O que mudou</th>
                        <th>De</th><th>Para</th><th>Situação</th></tr>
                </thead>
                <tbody>
                    ${d.registros.map(r => `
                        <tr class="${r.acao === 'BLOQUEIO' ? 'linha-bloqueio' : ''}">
                            <td>${formatarDataHora(r.data_hora)}</td>
                            <td>${r.usuario} <span class="badge badge-${r.nivel}">${rotuloNivel(r.nivel)}</span></td>
                            <td>${r.produto || '-'}</td>
                            <td>${r.campo_rotulo || '-'}</td>
                            <td>${r.valor_anterior ?? '-'}</td>
                            <td><strong>${r.valor_novo ?? '-'}</strong></td>
                            <td>${r.acao === 'BLOQUEIO'
                                ? `<span class="tag-bloqueio" title="${(r.detalhe || '').replace(/"/g, '&quot;')}">⛔ Bloqueado</span>`
                                : '<span class="tag-ok">✅ Aplicado</span>'}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
            <p class="ajuda">Passe o mouse sobre "Bloqueado" para ver o motivo.</p>
        `;
    } catch (erro) {
        container.innerHTML = `<p class="msg-erro">${erro.message}</p>`;
    }
}

function formatarDataHora(dh) {
    if (!dh) return '';
    const [data, hora] = dh.split(' ');
    return `${data.split('-').reverse().join('/')} ${(hora || '').slice(0, 5)}`;
}

function rotuloNivel(n) {
    return n === 'master' ? 'Master' : n === 'gerencia' ? 'Gerência' : 'Caixa';
}

function recalcularLinha(id) {
    const linha = document.querySelector(`.produto-linha[data-id="${id}"]`);
    if (!linha) return;

    const preco = parseFloat(linha.querySelector('.in-preco').value) || 0;
    const desconto = parseFloat(linha.querySelector('.in-desconto').value) || 0;
    const tipo = linha.querySelector('.in-tipo').value;
    const custo = parseFloat(linha.querySelector('.in-custo')?.value ?? linha.dataset.custo) || 0;
    const margemMin = parseFloat(linha.querySelector('.in-margem')?.value ?? linha.dataset.margem) || 0;

    const porUnidade = tipo === 'percentual' ? preco * (desconto / 100) : desconto;
    const final = Math.round((preco - porUnidade) * 100) / 100;

    const el = document.getElementById(`final-${id}`);
    el.textContent = reais(final);

    const dica = document.getElementById(`dica-${id}`);
    if (dica) {
        dica.textContent = tipo === 'percentual' && desconto > 0
            ? `= ${reais(porUnidade)} de desconto`
            : '';
    }

    // ===== INDICADOR DE MARGEM =====
    const info = document.getElementById(`margem-${id}`);
    const ehMaster = sessao.nivel === 'master';
    const piso = Math.round(custo * (1 + margemMin / 100) * 100) / 100;
    let estado = 'ok';

    if (final < 0) {
        estado = 'bloqueado';
        info.textContent = '⛔ preço negativo';
    } else if (!custo) {
        info.textContent = ehMaster ? 'informe o custo' : '⛔ custo não cadastrado';
        estado = ehMaster ? 'neutro' : 'bloqueado';
    } else if (final < custo) {
        estado = 'bloqueado';
        info.textContent = `⛔ abaixo do custo (prejuízo de ${reais(custo - final)})`;
    } else if (!ehMaster && final < piso) {
        estado = 'bloqueado';
        info.textContent = `⛔ mínimo do seu nível: ${reais(piso)}`;
    } else {
        const lucro = final - custo;
        const perc = (lucro / custo) * 100;
        estado = (!ehMaster && final < piso * 1.02) || perc < margemMin ? 'alerta' : 'ok';
        info.textContent = `margem ${reais(lucro)} (${perc.toFixed(1)}%)`;
    }

    el.classList.toggle('erro', estado === 'bloqueado');
    info.className = `margem-info ${estado}`;
    linha.classList.toggle('linha-bloqueada', estado === 'bloqueado');

    atualizarBotaoSalvar();
}

function atualizarBotaoSalvar() {
    const botao = document.getElementById('btn-salvar-produtos');
    if (!botao) return;

    const bloqueadas = document.querySelectorAll('.produto-linha.linha-bloqueada').length;
    botao.disabled = bloqueadas > 0;
    botao.textContent = bloqueadas > 0
        ? `⛔ ${bloqueadas} produto(s) com margem inválida`
        : '💾 Salvar alterações';
}

async function salvarProdutos() {
    const botao = document.getElementById('btn-salvar-produtos');
    botao.disabled = true;
    botao.textContent = 'Salvando...';

    try {
        const payload = [...document.querySelectorAll('.produto-linha')].map(linha => ({
            id: parseInt(linha.dataset.id),
            nome: linha.querySelector('.in-nome').value.trim(),
            preco_atual: parseFloat(linha.querySelector('.in-preco').value) || 0,
            ...(linha.querySelector('.in-custo') && !linha.querySelector('.in-custo').readOnly ? {
                preco_custo: parseFloat(linha.querySelector('.in-custo').value) || 0
            } : {}),
            ...(linha.querySelector('.in-margem') ? {
                margem_minima: parseFloat(linha.querySelector('.in-margem').value) || 0
            } : {}),
            desconto_valor: parseFloat(linha.querySelector('.in-desconto').value) || 0,
            desconto_tipo: linha.querySelector('.in-tipo').value,
            limite_litros: parseFloat(linha.querySelector('.in-limite').value) || 0,
            ativo: linha.querySelector('.in-ativo').checked ? 1 : 0
        }));

        const d = await api('/admin/produtos/atualizar', {
            method: 'POST',
            body: JSON.stringify({ produtos: payload })
        });

        aviso(`✅ ${d.mensagem}`);
        carregarProdutosAdmin();
    } catch (erro) {
        aviso(`❌ ${erro.message}`, 'erro');
    } finally {
        botao.disabled = false;
        botao.textContent = '💾 Salvar alterações';
    }
}

// ===================== USUÁRIOS =====================
async function carregarUsuarios() {
    const container = document.getElementById('lista-usuarios');
    container.innerHTML = '<p class="carregando">Carregando...</p>';

    try {
        const d = await api('/admin/usuarios');
        container.innerHTML = `
            <table class="tabela">
                <thead><tr><th>Usuário</th><th>Nome</th><th>Nível</th><th>Situação</th><th></th></tr></thead>
                <tbody>
                    ${d.usuarios.map(u => `
                        <tr>
                            <td><strong>${u.usuario}</strong></td>
                            <td>${u.nome}</td>
                            <td><span class="badge badge-${u.nivel}">${rotuloNivel(u.nivel)}</span></td>
                            <td>${u.ativo ? '✅ Ativo' : '🚫 Desativado'}</td>
                            <td>
                                <button class="btn-mini" onclick="redefinirSenhaUsuario(${u.id}, '${String(u.usuario).replace(/'/g, "\\'")}')">
                                    🔑 Redefinir senha
                                </button>
                                <button class="btn-mini" onclick="alternarUsuario(${u.id}, ${u.ativo ? 0 : 1})">
                                    ${u.ativo ? 'Desativar' : 'Reativar'}
                                </button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    } catch (erro) {
        container.innerHTML = `<p class="msg-erro">${erro.message}</p>`;
    }
}

// Sem isto, funcionário que esquece a senha fica trancado para sempre: a tela
// de "trocar senha" só troca a do próprio usuário logado, e não havia como o
// Master resolver — só desativar e criar outro.
async function redefinirSenhaUsuario(usuarioId, nomeUsuario) {
    const nova = prompt(
        `Nova senha para "${nomeUsuario}":\n\n` +
        `Mínimo 8 caracteres. Anote e entregue a ele — ninguém consegue ver a senha depois.`);

    if (nova === null) return;
    if (nova.trim().length < 8) {
        aviso('A senha precisa ter ao menos 8 caracteres.', 'erro');
        return;
    }

    try {
        await api(`/admin/usuarios/${usuarioId}`, {
            method: 'POST',
            body: JSON.stringify({ senha_nova: nova.trim() })
        });
        aviso(`Senha de ${nomeUsuario} redefinida. Ele precisa entrar de novo com a senha nova.`);
    } catch (erro) {
        aviso(`❌ ${erro.message}`, 'erro');
    }
}

async function criarUsuario(evento) {
    evento.preventDefault();

    try {
        const d = await api('/admin/usuarios', {
            method: 'POST',
            body: JSON.stringify({
                nome: document.getElementById('novo-nome').value.trim(),
                usuario: document.getElementById('novo-usuario').value.trim(),
                senha: document.getElementById('nova-senha').value,
                nivel: document.getElementById('novo-nivel').value
            })
        });

        aviso(`✅ ${d.mensagem}`);
        document.getElementById('form-usuario').reset();
        carregarUsuarios();
    } catch (erro) {
        aviso(`❌ ${erro.message}`, 'erro');
    }

    return false;
}

async function alternarUsuario(id, ativo) {
    try {
        const d = await api(`/admin/usuarios/${id}`, {
            method: 'POST',
            body: JSON.stringify({ ativo })
        });
        aviso(`✅ ${d.mensagem}`);
        carregarUsuarios();
    } catch (erro) {
        aviso(`❌ ${erro.message}`, 'erro');
    }
}

async function trocarSenha(evento) {
    evento.preventDefault();

    try {
        const d = await api('/admin/senha', {
            method: 'POST',
            body: JSON.stringify({
                senha_atual: document.getElementById('senha-atual').value,
                senha_nova: document.getElementById('senha-nova').value
            })
        });

        alert(d.mensagem);
        sair();
    } catch (erro) {
        aviso(`❌ ${erro.message}`, 'erro');
    }

    return false;
}

// ===================== PADRÕES SUSPEITOS =====================
//
// A foto do comprovante trava quem se declara motorista sem ser. O que ela
// não pega é a fraude de dentro: frentista que cadastra amigos e libera
// desconto para eles. Isso nunca aparece num abastecimento isolado — só no
// padrão ao longo dos dias. Daí esta tela.
//
// Tudo aqui é indício, não prova. O texto foi escrito para lembrar disso,
// porque acusar um funcionário por engano custa mais caro que o desconto.

function escapar(texto) {
    const d = document.createElement('div');
    d.textContent = texto == null ? '' : String(texto);
    return d.innerHTML;
}

function placaBonita(p) {
    return (!p || p.length !== 7) ? (p || '—') : `${p.slice(0, 3)} ${p.slice(3)}`;
}

async function carregarSuspeitas() {
    const dias = document.getElementById('suspeitas-dias').value;
    const alvo = document.getElementById('suspeitas-conteudo');
    const resumo = document.getElementById('suspeitas-resumo');

    alvo.innerHTML = '<p class="carregando">Analisando...</p>';
    resumo.innerHTML = '';

    try {
        const d = await api(`/admin/suspeitas?dias=${dias}`);

        resumo.className = 'resumo-suspeitas ' + (d.total_alertas ? 'com-alerta' : 'limpo');
        resumo.innerHTML = d.total_alertas
            ? `<strong>${d.total_alertas} ponto(s)</strong> merecem uma olhada nos últimos ${d.periodo_dias} dias.`
            : `Nenhum padrão fora do comum nos últimos ${d.periodo_dias} dias.`;

        alvo.innerHTML =
            blocoPlacasRepetidas(d.placas_repetidas) +
            blocoMesmoFrentista(d.sempre_mesmo_frentista) +
            blocoRajada(d.cadastros_em_rajada) +
            blocoTrocasPlaca(d.trocas_de_placa) +
            blocoBeneficiados(d.maiores_beneficiados);
    } catch (e) {
        alvo.innerHTML = `<p class="msg-erro visivel">${escapar(e.message)}</p>`;
    }
}

function caixaSuspeita(titulo, explicacao, corpo, vazio) {
    if (!corpo) {
        return `<div class="caixa-suspeita vazia">
            <h3>${titulo}</h3><p class="ajuda">${vazio}</p></div>`;
    }
    return `<div class="caixa-suspeita">
        <h3>${titulo}</h3>
        <p class="ajuda">${explicacao}</p>
        ${corpo}
    </div>`;
}

function blocoPlacasRepetidas(lista) {
    const corpo = (lista || []).map(p => `
        <div class="item-suspeita">
            <div class="placa-item">${placaBonita(p.placa)}</div>
            <div class="detalhe-item">
                <strong>${p.quantidade} cadastros</strong> usam esta placa
                <ul>${p.clientes.map(c => `
                    <li>
                        ${escapar(c.nome)} — ${escapar(c.ocupacao || '')}
                        <span class="cinza">(cadastrado em ${escapar(c.cadastrado_em)})</span>
                        <button class="link-comprovante" onclick="verComprovante(${c.id})">ver comprovante</button>
                    </li>`).join('')}
                </ul>
            </div>
        </div>`).join('');

    return caixaSuspeita(
        '🚗 Mesma placa em vários cadastros',
        'Táxi dividido por turno é normal e aparece aqui também. O que chama atenção ' +
        'é a mesma placa em três ou mais contas, ou em contas criadas no mesmo dia.',
        corpo,
        'Nenhuma placa repetida.'
    );
}

function blocoMesmoFrentista(lista) {
    const corpo = (lista || []).length ? `
        <table class="tabela">
            <thead><tr>
                <th>Motorista</th><th>Placa</th><th>Abastecimentos</th><th>Sempre com</th>
            </tr></thead>
            <tbody>${lista.map(l => `
                <tr>
                    <td>${escapar(l.cliente_nome)}</td>
                    <td class="mono">${placaBonita(l.placa)}</td>
                    <td>${l.abastecimentos}</td>
                    <td><strong>${escapar(l.frentista)}</strong></td>
                </tr>`).join('')}
            </tbody>
        </table>` : '';

    return caixaSuspeita(
        '👥 Motorista que só abastece com um frentista',
        'Quem abastece de verdade cai em turnos diferentes ao longo do mês. ' +
        'Cinco ou mais abastecimentos sempre com a mesma pessoa é o padrão de um combinado — ' +
        'mas também pode ser só rotina de horário. Vale conversar antes de concluir.',
        corpo,
        'Ninguém abastecendo sempre com o mesmo frentista.'
    );
}

function blocoRajada(lista) {
    const corpo = (lista || []).length ? `
        <table class="tabela">
            <thead><tr><th>Dia</th><th>Cadastros criados</th></tr></thead>
            <tbody>${lista.map(l => `
                <tr><td>${escapar(l.dia)}</td><td>${l.quantidade}</td></tr>`).join('')}
            </tbody>
        </table>` : '';

    return caixaSuspeita(
        '📋 Cadastros em rajada',
        'Vários cadastros no mesmo dia pode ser divulgação que deu certo — ou um mutirão ' +
        'de amigos. Cruze com o dia em que você fez alguma ação de divulgação.',
        corpo,
        'Nenhum dia com volume fora do normal.'
    );
}

function blocoTrocasPlaca(lista) {
    const corpo = (lista || []).length ? `
        <table class="tabela">
            <thead><tr><th>Quando</th><th>Cliente</th><th>De</th><th>Para</th></tr></thead>
            <tbody>${lista.map(l => `
                <tr>
                    <td>${escapar(l.quando)}</td>
                    <td>${escapar(l.cliente)}</td>
                    <td class="mono">${placaBonita(l.de)}</td>
                    <td class="mono">${placaBonita(l.para)}</td>
                </tr>`).join('')}
            </tbody>
        </table>` : '';

    return caixaSuspeita(
        '🔁 Trocas de placa',
        'Motorista de aplicativo troca de carro e é esperado que troque a placa. ' +
        'O que destoa é trocar toda semana, ou trocar minutos antes de abastecer.',
        corpo,
        'Nenhuma troca de placa no período.'
    );
}

function blocoBeneficiados(lista) {
    const corpo = (lista || []).length ? `
        <table class="tabela">
            <thead><tr>
                <th>Motorista</th><th>Placa</th><th>Categoria</th>
                <th>Dias</th><th>Litros</th><th>Desconto total</th>
            </tr></thead>
            <tbody>${lista.map(l => `
                <tr>
                    <td>${escapar(l.cliente_nome)}
                        <button class="link-comprovante" onclick="verComprovante(${l.cliente_id})">comprovante</button>
                    </td>
                    <td class="mono">${placaBonita(l.placa)}</td>
                    <td>${escapar(l.ocupacao || '')}</td>
                    <td>${l.dias_com_abastecimento}</td>
                    <td>${l.litros.toFixed(2).replace('.', ',')}</td>
                    <td><strong>R$ ${l.desconto_total.toFixed(2).replace('.', ',')}</strong></td>
                </tr>`).join('')}
            </tbody>
        </table>` : '';

    return caixaSuspeita(
        '💰 Quem mais recebeu desconto',
        'Não é alerta — é o custo do programa por pessoa. Taxista que roda todo dia ' +
        'aparece no topo com razão. Estranho é quem aparece no topo com poucos dias rodados.',
        corpo,
        'Sem abastecimentos no período.'
    );
}

// ===================== COMPROVANTE DO CADASTRO =====================

async function verComprovante(clienteId) {
    const janela = document.getElementById('janela-comprovante');
    const alvo = document.getElementById('comprovante-conteudo');

    alvo.innerHTML = '<p class="carregando">Carregando...</p>';
    janela.hidden = false;

    try {
        const d = await api(`/admin/cliente/${clienteId}/comprovante`);

        const rotulos = {
            licenca_taxi: 'Licença de taxista',
            perfil_app: 'Perfil no aplicativo de motorista',
            convenio: 'Comprovante de vínculo com a empresa'
        };

        alvo.innerHTML = `
            <h3>${escapar(d.nome)}</h3>
            <p class="ajuda">
                ${escapar(d.ocupacao || '')} ·
                placa <span class="mono">${placaBonita(d.placa)}</span>
                ${d.empresa_convenio ? ' · ' + escapar(d.empresa_convenio) : ''}
                ${d.registro_numero ? ' · registro ' + escapar(d.registro_numero) : ''}
            </p>
            <p class="tipo-comprovante">
                ${escapar(rotulos[d.tipo_comprovante] || 'Comprovante')}
                ${d.enviado_em ? '<span class="cinza"> — enviado em ' + escapar(String(d.enviado_em).slice(0, 10)) + '</span>' : ''}
            </p>
            ${d.imagem
                ? `<img src="${d.imagem}" alt="Comprovante" class="imagem-comprovante">`
                : '<p class="msg-erro visivel">Este cadastro não tem comprovante — foi feito antes desta exigência.</p>'}`;
    } catch (e) {
        alvo.innerHTML = `<p class="msg-erro visivel">${escapar(e.message)}</p>`;
    }
}

function fecharComprovante(evento) {
    if (evento && evento.target !== evento.currentTarget) return;
    document.getElementById('janela-comprovante').hidden = true;
    document.getElementById('comprovante-conteudo').innerHTML = '';
}
