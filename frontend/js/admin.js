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
