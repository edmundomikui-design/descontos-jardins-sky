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

    const ehMaster = sessao.nivel === 'master';
    document.getElementById('badge-usuario').textContent =
        `${sessao.nome || sessao.usuario} · ${ehMaster ? 'Master' : 'Caixa'}`;
    document.getElementById('badge-usuario').className = `badge ${ehMaster ? 'badge-master' : 'badge-caixa'}`;

    // Caixa não vê as abas de alteração
    document.querySelectorAll('.somente-master').forEach(el => {
        el.style.display = ehMaster ? '' : 'none';
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
    if (nome === 'usuarios') carregarUsuarios();
    if (nome === 'caixa') carregarCaixa();
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

    const linha = p => `
        <div class="produto-linha" data-id="${p.id}">
            <div class="produto-titulo">
                <span class="icone">${p.icone || ''}</span>
                <input type="text" class="in-nome" value="${p.nome.replace(/"/g, '&quot;')}"
                       title="Clique para renomear o produto">
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

            <div class="campo resultado">
                <label>Cliente paga</label>
                <strong class="preco-final" id="final-${p.id}">${reais(p.preco_final)}</strong>
                <span class="por-unidade">por ${p.unidade}</span>
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
}

function recalcularLinha(id) {
    const linha = document.querySelector(`.produto-linha[data-id="${id}"]`);
    if (!linha) return;

    const preco = parseFloat(linha.querySelector('.in-preco').value) || 0;
    const desconto = parseFloat(linha.querySelector('.in-desconto').value) || 0;
    const tipo = linha.querySelector('.in-tipo').value;

    const porUnidade = tipo === 'percentual' ? preco * (desconto / 100) : desconto;
    const final = preco - porUnidade;

    const el = document.getElementById(`final-${id}`);
    el.textContent = reais(final);
    el.classList.toggle('erro', final < 0);

    const dica = document.getElementById(`dica-${id}`);
    if (dica) {
        dica.textContent = tipo === 'percentual' && desconto > 0
            ? `= ${reais(porUnidade)} de desconto`
            : '';
    }
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
                            <td><span class="badge ${u.nivel === 'master' ? 'badge-master' : 'badge-caixa'}">
                                ${u.nivel === 'master' ? 'Master' : 'Caixa'}</span></td>
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
