// ============================================================
//  POSTOS PARTICIPANTES — fonte única dos endereços do app
// ============================================================
//
// Por que este arquivo existe:
// os endereços estavam escritos à mão dentro do dashboard.html, num bloco
// no fim da página. O motorista que abria o cupom via o QR code e não via
// para onde ir — para achar o endereço tinha que rolar a tela inteira.
// Agora o endereço vem daqui e aparece DENTRO do cupom, com botão de rota.
//
// Para incluir, tirar ou corrigir um posto, mexa só neste arquivo.
//
//   no_cupom: true   → aparece dentro do cupom, com o botão "Como chegar"
//   no_cupom: false  → aparece só na lista de "Postos participantes"
//
// Durante o piloto (agosto/2026) só o Jardins está no cupom, por decisão do
// Edmundo: um posto só para testar na prática. Para pôr o Sky também, troque
// o false por true na linha dele. É a única mudança necessária.

const POSTOS = [
    {
        id: 'jardins',
        nome: 'Posto Jardins',
        endereco: 'Rua Estados Unidos, 1930',
        referencia: 'esquina com a Rua da Consolação',
        cidade: 'São Paulo — SP',
        horario: '24 horas',
        // Texto que vai para o aplicativo de mapa. Leva cidade e estado
        // porque "Rua Estados Unidos, 1930" existe em mais de uma cidade do
        // Brasil e o mapa pode cair na errada. O bairro foi deixado de fora
        // de propósito: bairro errado atrapalha mais do que ajuda.
        // >>> CONFERIR NO CELULAR: toque em "Como chegar" uma vez e veja se
        //     o mapa cai na porta do posto. <<<
        busca_mapa: 'Rua Estados Unidos, 1930 - São Paulo, SP',
        no_cupom: true
    },
    {
        id: 'sky',
        nome: 'Posto Sky',
        endereco: 'Rua Estados Unidos, 1776',
        referencia: 'esquina com a Rua Bela Cintra',
        cidade: 'São Paulo — SP',
        horario: '24 horas',
        busca_mapa: 'Rua Estados Unidos, 1776 - São Paulo, SP',
        no_cupom: false
    }
];

// Postos que aparecem dentro do cupom.
function postosDoCupom() {
    return POSTOS.filter(p => p.no_cupom);
}

// "Como chegar": abre o mapa JÁ TRAÇANDO A ROTA a partir de onde a pessoa
// está, em vez de só mostrar o ponto. Um toque e o motorista sai dirigindo.
// Este endereço universal funciona em iPhone e em Android: se o celular tem
// o app do Google Maps instalado, abre nele; se não tem, abre no navegador.
function linkRota(posto) {
    return 'https://www.google.com/maps/dir/?api=1&destination=' +
        encodeURIComponent(posto.busca_mapa);
}

// Muito motorista de aplicativo dirige com o Waze aberto o dia inteiro.
// Ficar trocando de app no trânsito é atrito — então tem o atalho dele também.
function linkWaze(posto) {
    return 'https://waze.com/ul?q=' +
        encodeURIComponent(posto.busca_mapa) + '&navigate=yes';
}

// Bloco visual de um posto, para injetar dentro do cupom.
function htmlPostoNoCupom(posto) {
    return `
        <div class="posto-cupom">
            <div class="posto-cupom-nome">⛽ ${posto.nome}</div>
            <div class="posto-cupom-endereco">
                ${posto.endereco}<br>
                <span class="posto-cupom-ref">${posto.referencia} · aberto ${posto.horario}</span>
            </div>
            <div class="posto-cupom-botoes">
                <a class="botao-rota" target="_blank" rel="noopener"
                   href="${linkRota(posto)}">🧭 Como chegar</a>
                <a class="botao-waze" target="_blank" rel="noopener"
                   href="${linkWaze(posto)}">Waze</a>
            </div>
        </div>
    `;
}

// Desenha o bloco de postos dentro do cupom aberto. Chamado toda vez que um
// cupom é exibido — inclusive quando ele vem do cache, sem internet.
function montarPostosDoCupom() {
    const alvo = document.getElementById('cupom-postos');
    if (!alvo) return;

    const lista = postosDoCupom();
    if (!lista.length) { alvo.innerHTML = ''; return; }

    const titulo = lista.length > 1
        ? 'Onde usar este cupom'
        : 'Onde usar este cupom';

    alvo.innerHTML =
        `<h4>${titulo}</h4>` + lista.map(htmlPostoNoCupom).join('');
}
