// ===== CONFIGURAÇÃO =====
const API_BASE_URL = 'https://descontos-jardins-sky-1.onrender.com/api';
let clienteAtual = null;
let produtosDisponiveis = [];
let cuponsGerados = {}; // { produto_id: cupom_data }
let produtoSelecionado = null;

// Helper: escreve texto sem quebrar se o elemento não existir
function setTxt(id, valor) {
    const el = document.getElementById(id);
    if (el) el.textContent = valor;
    else console.warn('Elemento não encontrado:', id);
}

// ===== INICIALIZAÇÃO =====
document.addEventListener('DOMContentLoaded', () => {
    // O service worker agora é registrado por js/instalar.js, a partir da raiz
    // (/sw.js). Em /js/ ele só controlava aquela pasta e o Chrome não
    // considerava o app instalável.

    if (window.location.pathname.includes('dashboard')) {
        verificarAutenticacao();
        mostrarPlacaAtual();
        // Cadastro em análise não vê lista de produtos: o botão "Gerar Cupom"
        // só daria erro, e erro sem explicação parece app quebrado.
        if (mostrarAvisoDeSituacao()) {
            carregarProdutos().then(carregarCuponsAtivos);
        }
    } else {
        setupLoginForm();
        setupEsqueciForm();
    }

    const agora = new Date().toLocaleDateString('pt-BR');
    document.querySelectorAll('[id*="cupom-data"]').forEach(el => el.textContent = agora);
});

// ===== SITUAÇÃO DO CADASTRO =====
//
// Funcionário de empresa conveniada entra na fila da gerência antes de poder
// gerar cupom. Devolve true quando o cadastro está liberado.

function mostrarAvisoDeSituacao() {
    const situacao = (localStorage.getItem('cliente_status') || 'ativo').toLowerCase();
    if (situacao === 'ativo') return true;

    const empresa = localStorage.getItem('cliente_empresa') || 'sua empresa';
    const motivo = localStorage.getItem('cliente_motivo_recusa') || '';
    const lista = document.getElementById('produtos-lista');
    if (!lista) return false;

    if (situacao === 'pendente') {
        lista.innerHTML = `
            <div style="padding:22px; text-align:center;">
                <div style="font-size:40px; margin-bottom:10px;">⏳</div>
                <h3 style="margin:0 0 10px; color:#e65100;">Cadastro em análise</h3>
                <p style="color:#555; line-height:1.5; margin:0 0 12px;">
                    A gerência está conferindo seu vínculo com a <strong>${empresa}</strong>.
                    Assim que for aprovado, seus cupons ficam liberados aqui.
                </p>
                <p style="color:#888; font-size:13px; margin:0;">
                    A análise costuma sair em 1 dia útil. Não precisa se cadastrar de novo.
                </p>
            </div>`;
    } else {
        lista.innerHTML = `
            <div style="padding:22px; text-align:center;">
                <div style="font-size:40px; margin-bottom:10px;">🚫</div>
                <h3 style="margin:0 0 10px; color:#c62828;">Cadastro não liberado</h3>
                <p style="color:#555; line-height:1.5; margin:0 0 12px;">
                    ${motivo || 'Seu cadastro não foi aprovado pela gerência.'}
                </p>
                <p style="color:#888; font-size:13px; margin:0;">
                    Se achar que houve engano, procure a gerência dos postos CAJ ou SKY.
                </p>
            </div>`;
    }

    const cupomCard = document.getElementById('cupom-card');
    if (cupomCard) cupomCard.style.display = 'none';
    return false;
}

// ===== CARRO EM USO =====
// Motorista de aplicativo troca de carro toda hora. Se a placa fosse fixa no
// cadastro, a conferência do frentista falharia justamente para quem mais usa
// o app — e ele levaria a culpa por uma regra nossa.

function mostrarPlacaAtual() {
    const placa = localStorage.getItem('cliente_placa') || '';
    const campo = document.getElementById('placa-atual');
    if (campo) campo.textContent = placa ? placa.slice(0, 3) + ' ' + placa.slice(3) : '—';
}

function abrirTrocaPlaca() {
    const linha = document.getElementById('linha-troca-placa');
    linha.hidden = !linha.hidden;
    if (!linha.hidden) {
        document.getElementById('placa-atual-campo').value =
            localStorage.getItem('cliente_placa') || '';
        document.getElementById('placa-atual-campo').focus();
    }
}

async function salvarPlaca() {
    const campo = document.getElementById('placa-atual-campo');
    const msg = document.getElementById('msg-placa');
    const placa = campo.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase();

    if (!placaValida(placa)) {
        msg.textContent = 'Placa inválida. Use ABC1D23 ou ABC1234.';
        msg.className = 'msg-placa erro';
        return;
    }

    try {
        const resposta = await fetch(`${API_BASE_URL}/cliente/placa`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cliente_id: localStorage.getItem('cliente_id'),
                placa: placa
            })
        });
        const dados = await resposta.json();

        if (!resposta.ok) throw new Error(dados.erro || 'Não consegui salvar.');

        localStorage.setItem('cliente_placa', dados.placa);
        mostrarPlacaAtual();
        document.getElementById('linha-troca-placa').hidden = true;
        msg.textContent = '✓ Placa atualizada. É esta que o frentista vai conferir.';
        msg.className = 'msg-placa ok';
    } catch (e) {
        msg.textContent = e.message;
        msg.className = 'msg-placa erro';
    }
}

// ===== AUTENTICAÇÃO =====

// São três abas na mesma tela agora: entrar, cadastrar e esqueci a senha.
// Alternar por toggle só funcionava com duas — com três, uma delas ficava
// visível junto com a outra.
function mostrarAba(id) {
    ['login-tab', 'cadastro-tab', 'esqueci-tab'].forEach(aba => {
        const el = document.getElementById(aba);
        if (el) el.classList.toggle('active', aba === id);
    });
    ['login-erro', 'cadastro-erro', 'esqueci-msg'].forEach(m => {
        const el = document.getElementById(m);
        if (el) { el.textContent = ''; el.style.color = ''; }
    });
    return false;
}

function toggleTab() {
    const login = document.getElementById('login-tab');
    return mostrarAba(login && login.classList.contains('active')
        ? 'cadastro-tab' : 'login-tab');
}

function abrirEsqueciSenha() {
    // Já digitou o e-mail no login e a senha não entrou? Aproveita o que
    // ele escreveu em vez de pedir de novo.
    const doLogin = document.getElementById('login-email');
    const doEsqueci = document.getElementById('esqueci-email');
    if (doLogin && doEsqueci && doLogin.value) doEsqueci.value = doLogin.value;
    mostrarAba('esqueci-tab');
    if (doEsqueci) doEsqueci.focus();
    return false;
}

function voltarAoLogin() {
    return mostrarAba('login-tab');
}

function setupEsqueciForm() {
    const form = document.getElementById('esqueci-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = (document.getElementById('esqueci-email').value || '').trim();
        const botao = document.getElementById('btn-esqueci');
        const msg = document.getElementById('esqueci-msg');

        botao.disabled = true;
        botao.textContent = 'Enviando…';
        msg.style.color = '';
        msg.textContent = '';

        try {
            const r = await fetch(`${API_BASE_URL}/auth/esqueci-senha`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email })
            });
            const d = await r.json();

            if (!r.ok) {
                msg.textContent = d.erro || 'Não foi possível enviar agora.';
            } else {
                msg.style.color = '#15803d';
                msg.textContent = d.mensagem;
                // Some o formulário: se continuasse na tela, ele apertaria de
                // novo achando que não foi, e a trava do servidor engoliria o
                // segundo pedido sem mandar nada.
                form.style.display = 'none';
            }
        } catch (err) {
            msg.textContent = 'Não consegui falar com o servidor. Tente de novo em alguns segundos.';
        } finally {
            botao.disabled = false;
            botao.textContent = 'Enviar link';
        }
    });
}

function setupLoginForm() {
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('login-email').value;
            const senha = document.getElementById('login-senha').value;
            await fazerLogin(email, senha);
        });
    }

    const cadastroForm = document.getElementById('cadastro-form');
    if (cadastroForm) {
        cadastroForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const dados = {
                nome: document.getElementById('cadastro-nome').value,
                cpf: document.getElementById('cadastro-cpf').value,
                ocupacao: document.getElementById('cadastro-ocupacao').value,
                tel: document.getElementById('cadastro-tel').value,
                endereco: document.getElementById('cadastro-endereco').value,
                email: document.getElementById('cadastro-email').value,
                senha: document.getElementById('cadastro-senha').value,
                confirmacao: document.getElementById('cadastro-confirmacao').value,
                aceita_promocoes: document.getElementById('cadastro-promocoes').checked,
                aceita_parceiros: document.getElementById('cadastro-parceiros').checked,
                placa: document.getElementById('cadastro-placa').value,
                registro_numero: document.getElementById('cadastro-registro').value,
                // A empresa agora é escolhida numa lista fechada: o campo
                // guarda o id do convênio, não o nome digitado.
                empresa_convenio_id: document.getElementById('cadastro-empresa').value || null,
                foto_comprovante: comprovanteEmBase64
            };

            if (dados.ocupacao === 'Outro' && !dados.empresa_convenio_id) {
                mostrarAviso('Escolha a sua empresa na lista de convênios. Se ela não aparece, ' +
                             'sua empresa ainda não tem convênio com os postos CAJ e SKY.');
                return;
            }

            await fazerCadastro(dados);
        });
    }
}

async function fazerLogin(email, senha) {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, senha })
        });

        const data = await response.json();

        if (response.ok) {
            localStorage.setItem('cliente_id', data.cliente_id);
            localStorage.setItem('cliente_nome', data.nome);
            localStorage.setItem('cliente_email', data.email);
            localStorage.setItem('cliente_placa', data.placa || '');
            localStorage.setItem('cliente_ocupacao', data.ocupacao || '');
            localStorage.setItem('cliente_status', data.status || 'ativo');
            localStorage.setItem('cliente_empresa', data.empresa_convenio || '');
            localStorage.setItem('cliente_motivo_recusa', data.motivo_recusa || '');
            window.location.href = 'dashboard.html';
        } else {
            mostrarErro('login', data.erro || 'Erro ao fazer login');
        }
    } catch (erro) {
        mostrarErro('login', 'Erro ao conectar ao servidor');
        console.error(erro);
    }
}

async function fazerCadastro(dados) {
    if (!validarFormulario(dados)) return;

    if (dados.senha !== dados.confirmacao) {
        mostrarErro('cadastro', 'As senhas não correspondem');
        return;
    }

    // Placa e comprovante da categoria (js/comprovacao.js)
    const problema = validarComprovacao(dados);
    if (problema) {
        mostrarErro('cadastro', problema);
        return;
    }

    if (!dados.aceita_promocoes) {
        mostrarErro('cadastro', 'É preciso aceitar os avisos do aplicativo (cupons e preços do dia) para se cadastrar');
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/auth/cadastro`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(dados)
        });

        const data = await response.json();

        if (response.ok) {
            // Convênio de empresa não sai liberado na hora: quem confere o
            // vínculo é a gerência. Dizer "cadastro realizado" e o cupom não
            // sair depois seria pior do que avisar agora.
            if (data.aguardando_aprovacao) {
                mostrarErro('cadastro',
                    '✅ Cadastro enviado para análise. A gerência vai conferir seu vínculo ' +
                    'com a empresa e liberar seu acesso. Você já pode fazer login para ' +
                    'acompanhar.', 'sucesso');
            } else {
                mostrarErro('cadastro', '✅ Cadastro realizado com sucesso! Faça login.', 'sucesso');
            }
            document.getElementById('cadastro-form').reset();
            limparComprovante();
            document.getElementById('bloco-veiculo').style.display = 'none';
            setTimeout(() => {
                toggleTab();
            }, data.aguardando_aprovacao ? 4000 : 1500);
        } else {
            mostrarErro('cadastro', data.erro || 'Erro ao cadastrar');
        }
    } catch (erro) {
        mostrarErro('cadastro', 'Erro ao conectar ao servidor');
        console.error(erro);
    }
}

function validarFormulario(dados) {
    if (!dados.nome || !dados.cpf || !dados.email || !dados.senha) {
        mostrarErro('cadastro', 'Preencha todos os campos obrigatórios');
        return false;
    }

    const cpf = dados.cpf.replace(/\D/g, '');
    if (cpf.length !== 11) {
        mostrarErro('cadastro', 'CPF deve ter 11 dígitos');
        return false;
    }

    if (dados.senha.length < 6) {
        mostrarErro('cadastro', 'Senha deve ter no mínimo 6 caracteres');
        return false;
    }

    return true;
}

function mostrarErro(tipo, mensagem, classe = 'erro') {
    const elemento = document.getElementById(`${tipo}-erro`);
    if (!elemento) {
        alert(mensagem);
        return;
    }
    if (elemento) {
        elemento.textContent = mensagem;
        elemento.className = 'erro-msg show';

        if (classe === 'sucesso') {
            elemento.style.backgroundColor = '#e8f5e9';
            elemento.style.color = '#2e7d32';
        } else {
            elemento.style.backgroundColor = '#ffebee';
            elemento.style.color = '#c62828';
        }

        setTimeout(() => {
            elemento.classList.remove('show');
        }, 4000);
    }
}

// ===== DASHBOARD =====
function verificarAutenticacao() {
    const clienteId = localStorage.getItem('cliente_id');
    const clienteNome = localStorage.getItem('cliente_nome');

    if (!clienteId) {
        window.location.href = 'index.html';
        return;
    }

    clienteAtual = {
        id: clienteId,
        nome: clienteNome
    };

    document.querySelectorAll('#cliente-nome').forEach(el => el.textContent = clienteNome);
}

// ===== PRODUTOS (NOVO) =====
async function carregarProdutos() {
    try {
        console.log('Iniciando carregamento de produtos...');

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 segundos

        const response = await fetch(`${API_BASE_URL}/produtos`, { signal: controller.signal });
        clearTimeout(timeoutId);

        const data = await response.json();

        if (response.ok) {
            console.log('Produtos carregados:', data.produtos.length);
            produtosDisponiveis = data.produtos;
            renderizarProdutos();
        } else {
            console.error('Erro ao carregar produtos:', data.erro);
            document.getElementById('produtos-lista').innerHTML = '<p style="color: red;">Erro ao carregar produtos</p>';
        }
    } catch (erro) {
        console.error('Erro ao conectar:', erro.message);
        document.getElementById('produtos-lista').innerHTML = '<p style="color: red;">Falha ao conectar. Recarregue a página.</p>';
    }
}

function renderizarProdutos() {
    const container = document.getElementById('produtos-lista');

    if (!produtosDisponiveis.length) {
        container.innerHTML = '<p style="text-align: center; color: #999;">Nenhum produto disponível</p>';
        return;
    }

    let html = '';

    // Agrupa por tipo
    const combustiveis = produtosDisponiveis.filter(p => p.tipo === 'combustivel');
    const oleos = produtosDisponiveis.filter(p => p.tipo === 'oleo');

    // Combustíveis
    if (combustiveis.length) {
        html += '<div style="margin-bottom: 20px;"><h4 style="color: #666; font-size: 12px; text-transform: uppercase; margin-bottom: 10px;">⛽ Combustíveis</h4>';
        combustiveis.forEach(p => {
            html += criarProdutoHTML(p);
        });
        html += '</div>';
    }

    // Óleos
    if (oleos.length) {
        html += '<div><h4 style="color: #666; font-size: 12px; text-transform: uppercase; margin-bottom: 10px;">🛢️ Óleos</h4>';
        oleos.forEach(p => {
            html += criarProdutoHTML(p);
        });
        html += '</div>';
    }

    container.innerHTML = html;
}

function criarProdutoHTML(produto) {
    const cupom = cuponsGerados[produto.id];
    const btnClass = cupom ? 'btn-produto gerado' : 'btn-produto';
    const acao = cupom
        ? `verCupom(${produto.id})`
        : `gerarCupomProduto(${produto.id}, this)`;
    const btnTexto = cupom ? '👁️ Ver cupom' : 'Gerar Cupom';

    return `
        <div class="produto-item">
            <div class="produto-info">
                <div class="produto-nome">${produto.icone} ${produto.nome}</div>
                <div class="produto-tipo">${produto.tipo}</div>
            </div>
            <div class="produto-preco">
                <div class="preco-valor">R$ ${produto.preco.toFixed(2)}</div>
                <div class="preco-unidade">/${produto.unidade}</div>
            </div>
            <button class="${btnClass}" onclick="${acao}">
                ${btnTexto}
            </button>
        </div>
    `;
}

// ===== RECUPERAÇÃO DO CUPOM DO DIA =====
async function carregarCuponsAtivos() {
    const clienteId = (clienteAtual && clienteAtual.id) || localStorage.getItem('cliente_id');
    if (!clienteId) return;

    try {
        const response = await fetch(`${API_BASE_URL}/cupom/ativos?cliente_id=${clienteId}`);
        const data = await response.json();

        if (!response.ok) {
            console.warn('[cupom] não foi possível recuperar cupons:', data.erro);
            usarCacheLocal();
            return;
        }

        cuponsGerados = {};
        (data.cupons || []).forEach(c => { cuponsGerados[c.produto_id] = c; });

        localStorage.setItem('cupons_do_dia', JSON.stringify({
            data: data.data,
            cupons: data.cupons || []
        }));

        console.log('[cupom] cupons de hoje recuperados:', (data.cupons || []).length);
        renderizarProdutos();

        const primeiro = (data.cupons || [])[0];
        if (primeiro) {
            produtoSelecionado = produtosDisponiveis.find(p => p.id == primeiro.produto_id) || null;
            mostrarCupomGerado(primeiro);
        }
    } catch (erro) {
        console.warn('[cupom] offline, usando cache local:', erro.message);
        usarCacheLocal();
    }
}

function salvarCacheLocal() {
    localStorage.setItem('cupons_do_dia', JSON.stringify({
        data: new Date().toISOString().slice(0, 10),
        cupons: Object.values(cuponsGerados)
    }));
}

// Sem internet no posto: usa o cupom guardado no celular (se for de hoje)
function usarCacheLocal() {
    try {
        const bruto = localStorage.getItem('cupons_do_dia');
        if (!bruto) return;

        const cache = JSON.parse(bruto);
        const hoje = new Date().toISOString().slice(0, 10);
        if (cache.data !== hoje) {
            localStorage.removeItem('cupons_do_dia');
            return;
        }

        cuponsGerados = {};
        (cache.cupons || []).forEach(c => { cuponsGerados[c.produto_id] = c; });
        renderizarProdutos();

        const primeiro = (cache.cupons || [])[0];
        if (primeiro) mostrarCupomGerado(primeiro);
    } catch (e) {
        console.warn('[cupom] cache local inválido');
    }
}

function verCupom(produtoId) {
    const cupom = cuponsGerados[produtoId];
    if (!cupom) return;
    produtoSelecionado = produtosDisponiveis.find(p => p.id == produtoId) || null;
    mostrarCupomGerado(cupom);
}

// Detecta iPhone/iPad. O Safari no iOS ignora o atributo "download" dos
// links (sempre ignorou — não é bug nosso), e isso piora quando o app está
// instalado na tela de início (modo standalone, sem barra de navegador para
// abrir uma nova aba). Por isso o iPhone tem um caminho de salvar diferente,
// abaixo.
function ehIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

// No iPhone o único jeito 100% confiável de salvar uma imagem é o usuário
// apertar e segurar nela e escolher "Salvar Imagem" — isso é um gesto do
// próprio iOS, funciona em qualquer versão e dentro do app instalado.
// Então em vez de tentar forçar um download (que o Safari ignora), mostramos
// a imagem grande em tela cheia com essa instrução.
function mostrarQRParaSalvarIOS(srcImagem) {
    if (document.getElementById('overlay-salvar-qr')) return;

    const overlay = document.createElement('div');
    overlay.id = 'overlay-salvar-qr';
    overlay.style.cssText = `
        position: fixed; inset: 0; z-index: 99999;
        background: rgba(0,0,0,0.92);
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        padding: 24px; text-align: center;
    `;
    overlay.innerHTML = `
        <p style="color:#fff; font-size:16px; font-weight:600; margin-bottom:16px; max-width:320px;">
            Toque e segure a imagem abaixo e escolha "Salvar Imagem"
        </p>
        <img src="${srcImagem}" alt="QR Code para salvar"
             style="width:260px; height:260px; border-radius:12px; background:#fff; padding:12px;">
        <button id="fechar-overlay-qr" style="margin-top:24px; padding:12px 28px; border:none; border-radius:10px; background:#fff; color:#111; font-weight:700; font-size:15px;">
            Fechar
        </button>
    `;
    document.body.appendChild(overlay);
    document.getElementById('fechar-overlay-qr').addEventListener('click', () => overlay.remove());
}

// Salva a imagem do QR code na galeria/downloads do celular
function salvarQRCode() {
    const img = document.getElementById('qrcode-img');
    if (!img || !img.src) {
        mostrarAviso('Nenhum cupom aberto para salvar.');
        return;
    }

    if (ehIOS()) {
        mostrarQRParaSalvarIOS(img.src);
        return;
    }

    const nomeProduto = (document.getElementById('cupom-produto-nome')?.textContent || 'cupom')
        .replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase();
    const hoje = new Date().toISOString().slice(0, 10);

    const link = document.createElement('a');
    link.href = img.src;
    link.download = `cupom-${nomeProduto}-${hoje}.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Compartilha o QR code por WhatsApp (ou qualquer app) usando o menu nativo
// de compartilhamento do celular. Se o celular não suportar compartilhar
// imagem direto, cai no caminho de salvar (que já trata iPhone à parte) e
// avisa o cliente para anexar manualmente.
async function compartilharQRCode() {
    const img = document.getElementById('qrcode-img');
    if (!img || !img.src) {
        mostrarAviso('Nenhum cupom aberto para compartilhar.');
        return;
    }

    const nomeProduto = (document.getElementById('cupom-produto-nome')?.textContent || 'cupom')
        .replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase();
    const hoje = new Date().toISOString().slice(0, 10);
    const nomeArquivo = `cupom-${nomeProduto}-${hoje}.png`;

    try {
        const resposta = await fetch(img.src);
        const blob = await resposta.blob();
        const arquivo = new File([blob], nomeArquivo, { type: 'image/png' });

        if (navigator.canShare && navigator.canShare({ files: [arquivo] })) {
            await navigator.share({
                files: [arquivo],
                title: 'Meu cupom CAJ SKY',
                text: 'Aqui está meu QR code de desconto CAJ SKY'
            });
        } else {
            // Celular não permite compartilhar arquivo direto (comum no
            // iPhone quando o app está instalado na tela de início).
            salvarQRCode();
            if (ehIOS()) {
                mostrarAviso('Toque e segure a imagem que apareceu na tela para salvá-la. Depois é só abrir o WhatsApp e anexar a foto salva.');
            } else {
                mostrarAviso('Seu celular não permite enviar a imagem direto pelo WhatsApp. Salvei o QR code — agora é só abrir o WhatsApp e anexar a imagem salva.');
            }
        }
    } catch (erro) {
        if (erro.name !== 'AbortError') {
            mostrarAviso('Não consegui compartilhar. Tente salvar o QR code e enviar manualmente pelo WhatsApp.');
        }
    }
}

async function gerarCupomProduto(produtoId, botao) {
    botao = botao || (typeof event !== 'undefined' && event ? event.target : null);
    const restaurarBotao = () => {
        if (botao) {
            botao.disabled = false;
            botao.textContent = 'Gerar Cupom';
        }
    };

    try {
        console.log('[cupom] clique no produto', produtoId);
        produtoSelecionado = produtosDisponiveis.find(p => p.id == produtoId) || null;

        if (botao) {
            botao.disabled = true;
            botao.textContent = '⏳ Gerando...';
        }

        const clienteId = (clienteAtual && clienteAtual.id) || localStorage.getItem('cliente_id');
        if (!clienteId) {
            mostrarAviso('Sessão expirada. Faça login novamente.');
            restaurarBotao();
            setTimeout(() => (window.location.href = 'index.html'), 1500);
            return;
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 60000); // Render pode demorar no 1º acesso

        const response = await fetch(`${API_BASE_URL}/cupom/gerar`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cliente_id: Number(clienteId),
                produto_id: produtoId
            }),
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        const texto = await response.text();
        let data = {};
        try { data = JSON.parse(texto); } catch (e) { data = { erro: texto.slice(0, 200) }; }

        console.log('[cupom] status', response.status, data);

        if (response.ok) {
            data.produto_id = data.produto_id || produtoId;
            cuponsGerados[produtoId] = data;
            salvarCacheLocal();
            mostrarCupomGerado(data);
            renderizarProdutos();
        } else {
            mostrarAviso(data.erro || `Erro ${response.status} ao gerar cupom`);
            restaurarBotao();
        }
    } catch (erro) {
        console.error('[cupom] falha:', erro);
        const msg = erro.name === 'AbortError'
            ? 'O servidor demorou demais para responder. Tente novamente em alguns segundos.'
            : `Erro ao conectar ao servidor: ${erro.message}`;
        mostrarAviso(msg);
        restaurarBotao();
    }
}

// Aviso sempre visível (aparece no topo da lista de produtos)
function mostrarAviso(mensagem) {
    let box = document.getElementById('cupom-erro');
    if (!box) {
        const lista = document.getElementById('produtos-lista');
        if (!lista) { alert(mensagem); return; }
        box = document.createElement('div');
        box.id = 'cupom-erro';
        lista.parentNode.insertBefore(box, lista);
    }
    box.style.cssText = 'display:block;background:#ffebee;color:#c62828;padding:12px;border-radius:8px;margin:10px 0;font-size:14px;';
    box.textContent = mensagem;
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => { box.style.display = 'none'; }, 8000);
}

function mostrarCupomGerado(data) {
    const container = document.getElementById('cupom-gerado') || criarCupomGerado();

    const vazio = document.getElementById('cupom-vazio');
    if (vazio) vazio.style.display = 'none';

    const card = document.getElementById('cupom-card');
    if (card) card.style.display = 'block';
    container.style.display = 'block';

    // QR code
    const img = document.getElementById('qrcode-img');
    if (img) img.src = data.qrcode_image;
    // Sem "Código:" na frente: o frentista digita o que está escrito, e o
    // rótulo colado no código já causou confusão em telas pequenas.
    setTxt('qrcode-text', data.qrcode_data);

    // Informações do produto
    setTxt('produto-nome', data.produto_nome);
    setTxt('produto-preco', `R$ ${Number(data.preco_produto).toFixed(2)}`);
    setTxt('produto-unidade', `/${produtoSelecionado?.unidade || 'un'}`);

    // Desconto
    const tipoLabel = data.desconto_tipo === 'percentual' ? '%' : 'R$';
    setTxt('desconto-tipo', data.desconto_tipo === 'percentual' ? 'Percentual' : 'Reais');
    setTxt('desconto-valor', `${data.desconto_valor}${tipoLabel}`);
    // Economia: backend v2 devolve economia_total + quantidade_permitida
    const economia = data.desconto_aplicado ?? data.economia_total;
    if (economia != null) {
        const limite = data.quantidade_permitida
            ? ` (até ${data.quantidade_permitida}${produtoSelecionado?.unidade || 'L'})`
            : '';
        setTxt('desconto-aplicado', `- R$ ${Number(economia).toFixed(2)}${limite}`);
    }

    const precoFinal = data.preco_final ?? data.preco_unitario_com_desconto;
    if (precoFinal != null) {
        setTxt('preco-final', `R$ ${Number(precoFinal).toFixed(2)}`);
    }

    // ===== PREÇO FINAL EM DESTAQUE (o que o motorista quer ver) =====
    const un = produtoSelecionado?.unidade || data.unidade || 'L';
    const precoCheio = Number(data.preco_produto ?? 0);
    const descUnidade = Number(data.desconto_por_unidade ?? 0);

    if (precoFinal != null) {
        setTxt('preco-final-destaque', `R$ ${Number(precoFinal).toFixed(2)}`);
        setTxt('preco-final-unidade', `por ${un === 'L' ? 'litro' : un}`);
        setTxt('preco-destaque-produto', `Você paga em ${data.produto_nome || 'combustível'}`);

        const riscado = document.getElementById('preco-tabela-riscado');
        if (riscado && precoCheio > 0 && descUnidade > 0) {
            riscado.innerHTML = `de <s>R$ ${precoCheio.toFixed(2)}</s>`;
        } else if (riscado) {
            riscado.textContent = '';
        }

        const badge = document.getElementById('preco-economia-badge');
        if (badge) {
            badge.textContent = descUnidade > 0
                ? `economia de R$ ${descUnidade.toFixed(2)}/${un}`
                : '';
        }
    }

    // Saldo do cupom (quando já houve abastecimento parcial)
    const unidade = produtoSelecionado?.unidade || data.unidade || 'L';
    if (data.quantidade_restante != null) {
        setTxt('cupom-saldo', `Saldo disponível: ${data.quantidade_restante}${unidade} de ${data.quantidade_permitida}${unidade}`);
    } else if (data.quantidade_permitida != null) {
        setTxt('cupom-saldo', `Limite: ${data.quantidade_permitida}${unidade}`);
    }

    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function voltarProdutos() {
    const card = document.getElementById('cupom-card');
    if (card) card.style.display = 'none';
    const lista = document.getElementById('produtos-lista');
    if (lista) lista.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function criarCupomGerado() {
    if (document.getElementById('cupom-gerado')) {
        return document.getElementById('cupom-gerado');
    }

    const cupomCard = document.getElementById('cupom-card');
    cupomCard.style.display = 'block';

    const html = `
        <div id="cupom-gerado" class="cupom-gerado" style="display: none;">
            <div class="qrcode-container">
                <img id="qrcode-img" src="" alt="QR Code" style="width: 200px; height: 200px; border: 2px solid #e0e0e0; border-radius: 8px;">
                <p id="qrcode-text" class="qrcode-text"></p>
            </div>

            <div class="cupom-info" style="background: #e8f5e9; border-left: 4px solid #4CAF50;">
                <h4>Produto:</h4>
                <p style="font-size: 16px; font-weight: 600; color: #1b5e20; margin: 8px 0;" id="produto-nome"></p>
                <div style="display: flex; justify-content: space-between; margin-top: 12px;">
                    <span>Preço:</span>
                    <span style="font-weight: 600;" id="produto-preco"></span><span id="produto-unidade"></span>
                </div>
            </div>

            <div class="cupom-info">
                <h4>Desconto:</h4>
                <div class="info-row">
                    <span class="label">Tipo:</span>
                    <span class="valor" id="desconto-tipo">-</span>
                </div>
                <div class="info-row">
                    <span class="label">Valor:</span>
                    <span class="valor" id="desconto-valor">-</span>
                </div>
                <div class="info-row">
                    <span class="label">Economia:</span>
                    <span class="valor" id="desconto-aplicado">-</span>
                </div>
                <div class="info-row" style="margin-top: 12px; border-top: 1px solid #e0e0e0; padding-top: 12px; font-weight: 600;">
                    <span class="label">Preço final:</span>
                    <span class="valor" id="preco-final">-</span>
                </div>
            </div>

            <div class="cupom-limites">
                <p>⚠️ Um cupom por produto/dia | Um abastecimento por cupom —
                   aproveite todo o limite de uma vez, o que sobrar não fica para depois</p>
            </div>

            <button onclick="imprimirCupom()" class="btn btn-secondary">
                📄 Imprimir Cupom
            </button>
        </div>
    `;

    const div = document.createElement('div');
    div.innerHTML = html;
    cupomCard.appendChild(div.firstElementChild);

    return document.getElementById('cupom-gerado');
}

function imprimirCupom() {
    window.print();
}

function exibirPerfil() {
    const modal = document.getElementById('perfil-modal');
    const dadosEl = document.getElementById('perfil-dados');

    dadosEl.innerHTML = `
        <div style="text-align: left;">
            <p><strong>Nome:</strong> ${clienteAtual.nome}</p>
            <p><strong>Email:</strong> ${localStorage.getItem('cliente_email')}</p>
            <p><strong>ID:</strong> ${clienteAtual.id}</p>
        </div>
    `;

    modal.style.display = 'flex';
}

function fecharPerfil() {
    document.getElementById('perfil-modal').style.display = 'none';
}

function logout() {
    if (confirm('Tem certeza que deseja fazer logout?')) {
        localStorage.clear();
        window.location.href = 'index.html';
    }
}

window.onclick = function (event) {
    const modal = document.getElementById('perfil-modal');
    if (event.target === modal) {
        modal.style.display = 'none';
    }
};
