// ===== CONFIGURAÇÃO =====
const API_BASE_URL = 'https://descontos-jardins-sky-1.onrender.com/api';
let clienteAtual = null;
let produtosDisponiveis = [];
let cuponsGerados = {}; // { produto_id: cupom_data }

// ===== INICIALIZAÇÃO =====
document.addEventListener('DOMContentLoaded', () => {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('js/service-worker.js').catch(err => {
            console.log('Service Worker não registrado:', err);
        });
    }

    if (window.location.pathname.includes('dashboard')) {
        verificarAutenticacao();
        carregarProdutos();
    } else {
        setupLoginForm();
    }

    const agora = new Date().toLocaleDateString('pt-BR');
    document.querySelectorAll('[id*="cupom-data"]').forEach(el => el.textContent = agora);
});

// ===== AUTENTICAÇÃO =====
function toggleTab() {
    const loginTab = document.getElementById('login-tab');
    const cadastroTab = document.getElementById('cadastro-tab');

    loginTab.classList.toggle('active');
    cadastroTab.classList.toggle('active');

    document.getElementById('login-erro').textContent = '';
    document.getElementById('cadastro-erro').textContent = '';

    return false;
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
                confirmacao: document.getElementById('cadastro-confirmacao').value
            };
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

    try {
        const response = await fetch(`${API_BASE_URL}/auth/cadastro`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(dados)
        });

        const data = await response.json();

        if (response.ok) {
            mostrarErro('cadastro', '✅ Cadastro realizado com sucesso! Faça login.', 'sucesso');
            document.getElementById('cadastro-form').reset();
            setTimeout(() => {
                toggleTab();
            }, 1500);
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
        const response = await fetch(`${API_BASE_URL}/produtos`);
        const data = await response.json();

        if (response.ok) {
            produtosDisponiveis = data.produtos;
            renderizarProdutos();
        } else {
            console.error('Erro ao carregar produtos:', data.erro);
        }
    } catch (erro) {
        console.error('Erro ao conectar:', erro);
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
    const jaGerado = cuponsGerados[produto.id];
    const btnClass = jaGerado ? 'btn-produto gerado' : 'btn-produto';
    const btnTexto = jaGerado ? '✓ Gerado' : 'Gerar Cupom';

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
            <button class="${btnClass}" onclick="gerarCupomProduto(${produto.id})" ${jaGerado ? 'disabled' : ''}>
                ${btnTexto}
            </button>
        </div>
    `;
}

async function gerarCupomProduto(produtoId) {
    try {
        const botao = event.target;
        botao.disabled = true;
        botao.textContent = '⏳ Gerando...';

        const response = await fetch(`${API_BASE_URL}/cupom/gerar`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cliente_id: clienteAtual.id,
                produto_id: produtoId
            })
        });

        const data = await response.json();

        if (response.ok) {
            // Registra que cupom foi gerado para este produto
            cuponsGerados[produtoId] = true;

            // Mostra cupom gerado
            mostrarCupomGerado(data);

            // Atualiza lista de produtos
            renderizarProdutos();
        } else {
            mostrarErro('cupom', data.erro || 'Erro ao gerar cupom');
            botao.disabled = false;
            botao.textContent = 'Gerar Cupom';
        }
    } catch (erro) {
        mostrarErro('cupom', 'Erro ao conectar ao servidor');
        console.error(erro);
        event.target.disabled = false;
        event.target.textContent = 'Gerar Cupom';
    }
}

function mostrarCupomGerado(data) {
    const container = document.getElementById('cupom-gerado') || criarCupomGerado();

    document.getElementById('cupom-vazio').style.display = 'none';
    container.style.display = 'block';

    // Atualiza dados do cupom
    document.getElementById('qrcode-img').src = data.qrcode_image;
    document.getElementById('qrcode-text').textContent = `Código: ${data.qrcode_data}`;

    // Informações do produto
    document.getElementById('produto-nome').textContent = data.produto_nome;
    document.getElementById('produto-preco').textContent = `R$ ${data.preco_produto.toFixed(2)}`;
    document.getElementById('produto-unidade').textContent = `/${produtosDisponiveis.find(p => p.id == data.cupom_id)?.unidade || 'un'}`;

    // Desconto
    const tipoLabel = data.desconto_tipo === 'percentual' ? '%' : 'R$';
    document.getElementById('desconto-tipo').textContent = `${data.desconto_tipo === 'percentual' ? 'Percentual' : 'Reais'}`;
    document.getElementById('desconto-valor').textContent = `${data.desconto_valor}${tipoLabel}`;
    document.getElementById('desconto-aplicado').textContent = `- R$ ${data.desconto_aplicado.toFixed(2)}`;
    document.getElementById('preco-final').textContent = `R$ ${data.preco_final.toFixed(2)}`;
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
                <p>⚠️ Um cupom por produto/dia | Válido por 24h</p>
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
