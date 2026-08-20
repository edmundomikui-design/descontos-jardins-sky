// Service worker na RAIZ do site.
//
// Fica aqui de propósito: um service worker só enxerga arquivos da pasta dele
// para baixo. Em /js/ ele controlava apenas /js/*, e o Chrome não considerava
// o app instalável. Na raiz, ele controla o site inteiro.
//
// Estratégia: rede primeiro, cache como rede de segurança. O motorista na pista
// costuma estar com sinal ruim — melhor abrir a versão guardada do que uma
// tela de erro. Como a rede vem primeiro, ninguém fica preso numa versão velha.

// v13: entrou o "Esqueci minha senha". Sem trocar este número, quem já tem o
// app instalado continuaria abrindo o index.html e o app-v2.js guardados —
// ou seja, a tela de login sem o link novo.
const CACHE = 'cajsky-v13';

const ESSENCIAIS = [
    '/',
    '/index.html',
    '/dashboard.html',
    '/frentista.html',
    '/css/style.css',
    '/css/frentista.css',
    '/js/app-v2.js',
    '/js/frentista.js',
    '/js/instalar.js',
    '/manifest.json',
    '/manifest-pista.json',
    '/icons/cajsky-192.png',
    '/icons/pista-192.png'
];

self.addEventListener('install', evento => {
    evento.waitUntil(
        caches.open(CACHE)
            .then(cache => cache.addAll(ESSENCIAIS))
            .catch(() => { /* um arquivo ausente não pode derrubar a instalação */ })
    );
    self.skipWaiting();
});

self.addEventListener('activate', evento => {
    evento.waitUntil(
        caches.keys().then(nomes => Promise.all(
            nomes.filter(n => n !== CACHE).map(n => caches.delete(n))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', evento => {
    const req = evento.request;

    if (req.method !== 'GET') return;

    // Chamadas de API nunca entram no cache: preço e saldo de cupom
    // desatualizados dariam informação errada na pista.
    if (req.url.includes('/api/')) return;

    // A página de redefinir senha também fica de fora. Ela é aberta por um
    // link de e-mail com um token de uso único: guardar a resposta de um
    // token no cache é pedir para o link seguinte abrir a tela do anterior.
    if (req.url.includes('/redefinir-senha')) return;

    evento.respondWith(
        // "no-store" ignora qualquer cópia guardada pelo próprio navegador
        // (fora do nosso controle) e vai direto na rede. Sem isso, "rede
        // primeiro" às vezes não buscava nada novo de verdade — só repetia
        // uma cópia antiga que o navegador tinha guardado por conta própria.
        fetch(req, { cache: 'no-store' })
            .then(resposta => {
                if (resposta && resposta.status === 200 && resposta.type !== 'error') {
                    const copia = resposta.clone();
                    caches.open(CACHE).then(cache => cache.put(req, copia));
                }
                return resposta;
            })
            .catch(() => caches.match(req).then(
                guardado => guardado || caches.match('/index.html')
            ))
    );
});
