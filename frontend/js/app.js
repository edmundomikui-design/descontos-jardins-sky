// ===== CONFIGURAÇÃO =====
const API_BASE_URL = 'http://localhost:5000/api';
let clienteAtual = null;

// ===== INICIALIZAÇÃO =====
document.addEventListener('DOMContentLoaded', () => {
    // Registra PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('js/service-worker.js').catch(err => {
            console.log('Service Worker não registrado:', err);
        });
    }

    // Verifica se está na página de login ou dashboard
    if (window.location.pathname.includes('dashboard')) {
        verificarAutenticacao();
    } else {
        setupLoginForm();
    }

    // Atualiza data
    const agora = new Date().toLocaleDateString('pt-BR');
    const elementos = document.querySelectorAll('#cupom-data');
    elementos.forEach(el => el.textContent = agora);
});

// ===== AUTENTICAÇÃO =====

function toggleTab() {
    const loginTab = document.getElementById('login-tab');
    const cadastroTab = document.getElementById('cadastro-tab');

    loginTab.classList.toggle('active');
    cadastroTab.classList.toggle('active');

    // Limpa mensagens de erro
    document.getElementById('login-erro').textContent = '';
    document.getElementById('cadastro-erro').textContent = '';

    return false;
}

function setupLoginForm() {
    // Form de Login
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const email = document.getElementById('login-email').value;
            const senha = document.getElementById('login-senha').value;

            await fazerLogin(email, senha);
        });
    }

    // Form de Cadastro
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
            // Salva dados no localStorage
            localStorage.setItem('cliente_id', data.cliente_id);
            localStorage.setItem('cliente_nome', data.nome);
            localStorage.setItem('cliente_email', data.email);

            // Redireciona para dashboard
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
    // Validações
    if (!validarFormulario(dados)) {
        return;
    }

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

            // Limpa formulário e volta para login
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
        // Redireciona para login se não autenticado
        window.location.href = 'index.html';
        return;
    }

    clienteAtual = {
        id: clienteId,
        nome: clienteNome
    };

    // Atualiza nome na página
    const nomeElementos = document.querySelectorAll('#cliente-nome');
    nomeElementos.forEach(el => el.textContent = clienteNome);
}

async function gerarCupom() {
    try {
        const botao = event.target;
        botao.disabled = true;
        botao.textContent = '⏳ Gerando...';

        const response = await fetch(`${API_BASE_URL}/cupom/gerar`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cliente_id: clienteAtual.id })
        });

        const data = await response.json();

        if (response.ok) {
            // Esconde cupom vazio
            document.getElementById('cupom-vazio').style.display = 'none';

            // Mostra cupom gerado
            document.getElementById('cupom-gerado').style.display = 'block';
            document.getElementById('qrcode-img').src = data.qrcode_image;
            document.getElementById('qrcode-text').textContent = `Código: ${data.qrcode_data}`;

            // Atualiza informações de desconto
            const tipoLabel = data.desconto_tipo === 'percentual' ? '%' : 'R$';
            document.getElementById('desconto-tipo').textContent = `${data.desconto_tipo.charAt(0).toUpperCase() + data.desconto_tipo.slice(1)}`;
            document.getElementById('desconto-valor').textContent = `${data.desconto_valor}${tipoLabel}`;

            // Log de sucesso
            console.log('✅ Cupom gerado com sucesso!');
        } else {
            mostrarErro('cupom', data.erro || 'Erro ao gerar cupom');
            botao.disabled = false;
            botao.textContent = 'Gerar QR Code';
        }
    } catch (erro) {
        mostrarErro('cupom', 'Erro ao conectar ao servidor');
        console.error(erro);
        botao.disabled = false;
        botao.textContent = 'Gerar QR Code';
    }
}

function imprimirCupom() {
    window.print();
}

function exibirPerfil() {
    const modal = document.getElementById('perfil-modal');
    const dadosEl = document.getElementById('perfil-dados');

    // Aqui você buscaria os dados do servidor
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

// Fecha modal ao clicar fora
window.onclick = function (event) {
    const modal = document.getElementById('perfil-modal');
    if (event.target === modal) {
        modal.style.display = 'none';
    }
};

// ===== PWA MANIFEST =====
function criarManifest() {
    // Este arquivo deve ser criado manualmente ou via servidor
    const manifest = {
        name: "Descontos Jardins Sky",
        short_name: "Descontos",
        description: "Combustível com desconto na zona mais nobre de São Paulo",
        start_url: "/index.html",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#2196F3",
        icons: [
            {
                src: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='100' fill='%232196F3'>🚗</text></svg>",
                sizes: "192x192",
                type: "image/svg+xml"
            }
        ]
    };

    return manifest;
}
