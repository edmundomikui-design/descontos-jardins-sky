// ===== COMPROVAÇÃO DA CATEGORIA NO CADASTRO =====
//
// O desconto é para taxista e motorista de aplicativo. Cada categoria comprova
// de um jeito, porque as duas são diferentes:
//
//   Táxi   -> foto da licença de taxista. A placa é estável: acompanha a
//             permissão quando o taxista troca de carro.
//   App    -> print da tela de cadastro no aplicativo de motorista. Aqui o
//             carro muda o tempo todo, então a placa vale para hoje e o
//             motorista atualiza quando trocar.
//   Outro  -> convênio com empresa. Quem confere é o RH da empresa.
//
// Nada disso é prova inviolável, e não finge ser. É atrito e rastro com nome
// em cima — o suficiente para desencorajar quem ia burlar por conveniência.

let comprovanteEmBase64 = null;

const PERFIS = {
    'Táxi': {
        legenda: 'Comprovação de taxista',
        introducao: 'A licença confirma a categoria. A placa fica no seu cadastro e ' +
                    '<strong>é conferida pelo frentista na hora do abastecimento</strong>.',
        ajudaPlaca: 'A placa do táxi acompanha a permissão. Se você trocar de carro, ' +
                    'atualize aqui no aplicativo.',
        rotuloRegistro: 'Número do CONDUTAX (opcional)',
        ajudaRegistro: 'Se não souber de cabeça, pode deixar em branco — a licença já comprova.',
        rotuloFoto: 'Foto da sua licença de taxista:',
        textoBotao: '📷 Fotografar a licença',
        ajudaFoto: 'Fotografe o alvará ou a carteira de taxista, com o número legível. ' +
                   'Usamos só para confirmar sua categoria.',
        pedeConvenio: false
    },
    'Uber': {
        legenda: 'Comprovação de motorista de aplicativo',
        introducao: 'O print do seu perfil no aplicativo confirma a categoria. A placa é ' +
                    'a do carro que você está usando <strong>hoje</strong> e é conferida ' +
                    'pelo frentista na bomba.',
        ajudaPlaca: 'Trocou de carro? Atualize a placa no aplicativo antes de abastecer — ' +
                    'leva dois toques.',
        rotuloRegistro: 'Número do CONDUAPP (opcional)',
        ajudaRegistro: 'Cadastro municipal de motorista de aplicativo. Pode deixar em branco.',
        rotuloFoto: 'Print da tela de cadastro do seu aplicativo:',
        textoBotao: '🖼️ Enviar print do aplicativo',
        ajudaFoto: 'Abra o app de motorista (Uber, 99…), vá no seu perfil ou na tela de conta ' +
                   'e tire um print. Precisa aparecer o seu nome.',
        pedeConvenio: false
    },
    'Outro': {
        legenda: 'Convênio com empresa',
        introducao: 'Esta opção é só para funcionários de empresas com convênio ativo com ' +
                    'os postos CAJ e SKY.',
        ajudaPlaca: 'A placa é conferida pelo frentista no abastecimento.',
        rotuloRegistro: 'Matrícula na empresa (opcional)',
        ajudaRegistro: 'Se a sua empresa usa número de matrícula, informe aqui.',
        rotuloFoto: 'Comprovante de vínculo:',
        textoBotao: '📷 Enviar comprovante',
        ajudaFoto: 'Crachá, holerite com o nome da empresa ou carta do RH.',
        pedeConvenio: true
    }
};

// ---------- campos que mudam conforme a categoria ----------

function ajustarCamposOcupacao() {
    const ocupacao = document.getElementById('cadastro-ocupacao').value;
    const bloco = document.getElementById('bloco-veiculo');
    const perfil = PERFIS[ocupacao];

    if (!perfil) {
        bloco.style.display = 'none';
        return;
    }

    bloco.style.display = 'block';
    document.getElementById('legenda-veiculo').textContent = perfil.legenda;
    document.getElementById('ajuda-bloco-veiculo').innerHTML = perfil.introducao;
    document.getElementById('ajuda-placa').innerHTML = perfil.ajudaPlaca;
    document.getElementById('rotulo-registro').textContent = perfil.rotuloRegistro;
    document.getElementById('ajuda-registro').textContent = perfil.ajudaRegistro;
    document.getElementById('rotulo-foto').textContent = perfil.rotuloFoto;
    document.getElementById('ajuda-foto').innerHTML = perfil.ajudaFoto;

    const botao = document.getElementById('botao-foto');
    if (!comprovanteEmBase64) botao.textContent = perfil.textoBotao;

    document.getElementById('grupo-convenio').style.display =
        perfil.pedeConvenio ? 'block' : 'none';

    // Câmera direto só faz sentido para fotografar um documento físico.
    // Print de tela vem da galeria.
    const entrada = document.getElementById('cadastro-foto');
    if (ocupacao === 'Uber') entrada.removeAttribute('capture');
    else entrada.setAttribute('capture', 'environment');
}

// ---------- placa ----------

function formatarCampoPlaca(campo) {
    const limpo = campo.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 7);
    campo.value = limpo.length > 3 ? limpo.slice(0, 3) + limpo.slice(3) : limpo;
}

function placaValida(valor) {
    const p = (valor || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    return /^[A-Z]{3}[0-9]{4}$/.test(p) || /^[A-Z]{3}[0-9][A-Z][0-9]{2}$/.test(p);
}

// ---------- imagem ----------

// Foto de celular hoje tem 3 a 8 MB. Mandar isso inteiro para um banco no
// plano gratuito é pedir problema — e a rede do motorista costuma ser ruim.
// Reduz para 1000px de largura e qualidade 0,7: um documento continua legível
// e o arquivo cai para uns 100 KB.
function comprimirImagem(arquivo, larguraMaxima = 1000, qualidade = 0.7) {
    return new Promise((resolve, reject) => {
        const leitor = new FileReader();
        leitor.onerror = () => reject(new Error('Não consegui ler a imagem.'));
        leitor.onload = () => {
            const img = new Image();
            img.onerror = () => reject(new Error('Arquivo não é uma imagem válida.'));
            img.onload = () => {
                const escala = Math.min(1, larguraMaxima / img.width);
                const tela = document.createElement('canvas');
                tela.width = Math.round(img.width * escala);
                tela.height = Math.round(img.height * escala);
                tela.getContext('2d').drawImage(img, 0, 0, tela.width, tela.height);
                resolve(tela.toDataURL('image/jpeg', qualidade));
            };
            img.src = leitor.result;
        };
        leitor.readAsDataURL(arquivo);
    });
}

async function prepararComprovante(entrada) {
    const arquivo = entrada.files && entrada.files[0];
    if (!arquivo) return;

    const botao = document.getElementById('botao-foto');
    const textoOriginal = botao.textContent;
    botao.textContent = 'Preparando a imagem…';

    try {
        comprovanteEmBase64 = await comprimirImagem(arquivo);

        document.getElementById('previa-foto-img').src = comprovanteEmBase64;
        document.getElementById('previa-foto').hidden = false;
        botao.textContent = '✓ Comprovante pronto';
        botao.classList.add('foto-ok');
    } catch (e) {
        comprovanteEmBase64 = null;
        botao.textContent = textoOriginal;
        alert('Não consegui usar essa imagem. Tente outra.');
    }
}

function limparComprovante() {
    comprovanteEmBase64 = null;
    document.getElementById('cadastro-foto').value = '';
    document.getElementById('previa-foto').hidden = true;

    const botao = document.getElementById('botao-foto');
    botao.classList.remove('foto-ok');
    const perfil = PERFIS[document.getElementById('cadastro-ocupacao').value];
    botao.textContent = perfil ? perfil.textoBotao : '📷 Enviar comprovante';
}

// ---------- conferência antes de enviar ----------
// Devolve a mensagem do primeiro problema, ou null se estiver tudo certo.

function validarComprovacao(dados) {
    const perfil = PERFIS[dados.ocupacao];
    if (!perfil) return 'Escolha a sua ocupação.';

    if (!placaValida(dados.placa)) {
        return 'Informe a placa do carro no formato ABC1D23 (Mercosul) ou ABC1234 (antiga).';
    }
    if (!comprovanteEmBase64) {
        return 'Envie ' + perfil.rotuloFoto.toLowerCase().replace(/:$/, '') + '.';
    }
    if (perfil.pedeConvenio && !(dados.empresa_convenio || '').trim()) {
        return 'Informe a empresa do convênio.';
    }
    return null;
}
