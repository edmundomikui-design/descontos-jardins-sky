// ===== CONVITE DE INSTALAÇÃO =====
//
// Sem isto, quem abre o link fica no navegador para sempre: o Android às vezes
// sugere instalar, o iPhone nunca sugere. E notificação no iPhone só chega se
// o app estiver na tela de início — ou seja, instalar não é enfeite.
//
// A mesma página serve os dois apps. Basta a página definir, antes de carregar
// este arquivo:
//     window.INSTALAR = { titulo, texto, manifest, cor }

(function () {
    'use strict';

    const cfg = Object.assign({
        titulo: 'Instale o aplicativo',
        texto: 'Fica um ícone no seu celular, abre em tela cheia e funciona sem internet.',
        cor: '#2563eb'
    }, window.INSTALAR || {});

    const CHAVE_DISPENSA = 'cajsky_instalar_dispensado';
    const DIAS_ATE_PERGUNTAR_DE_NOVO = 7;

    let promptAdiado = null;   // evento do Chrome, guardado para o clique no botão

    // ---------- situação do aparelho ----------

    const jaInstalado = () =>
        window.matchMedia('(display-mode: standalone)').matches ||
        window.navigator.standalone === true;

    const ehIOS = () =>
        /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        // iPad recente se apresenta como Mac; o toque denuncia
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    function dispensadoRecentemente() {
        try {
            const quando = Number(localStorage.getItem(CHAVE_DISPENSA) || 0);
            if (!quando) return false;
            const dias = (Date.now() - quando) / 86400000;
            return dias < DIAS_ATE_PERGUNTAR_DE_NOVO;
        } catch (e) { return false; }
    }

    function marcarDispensado() {
        try { localStorage.setItem(CHAVE_DISPENSA, String(Date.now())); } catch (e) {}
    }

    // ---------- aparência ----------

    function injetarEstilo() {
        if (document.getElementById('estilo-instalar')) return;
        const s = document.createElement('style');
        s.id = 'estilo-instalar';
        s.textContent = `
        #faixa-instalar {
            position: fixed; left: 12px; right: 12px; bottom: 12px; z-index: 9999;
            background: #0f172a; color: #e2e8f0;
            border: 1px solid #24324d; border-radius: 16px;
            padding: 16px 18px;
            box-shadow: 0 12px 40px rgba(0,0,0,.4);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 480px; margin: 0 auto;
            animation: subir-instalar .28s ease-out;
        }
        @keyframes subir-instalar {
            from { transform: translateY(20px); opacity: 0; }
            to   { transform: translateY(0);    opacity: 1; }
        }
        #faixa-instalar .cabeca {
            display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
        }
        #faixa-instalar h3 { margin: 0; font-size: 16px; font-weight: 700; color: #f8fafc; }
        #faixa-instalar p  { margin: 6px 0 0; font-size: 14px; line-height: 1.45; color: #94a3b8; }
        #faixa-instalar .fechar {
            background: none; border: none; color: #64748b;
            font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px; flex-shrink: 0;
        }
        #faixa-instalar .acao {
            display: block; width: 100%; margin-top: 14px;
            padding: 14px; border: none; border-radius: 12px;
            background: ${cfg.cor}; color: #fff;
            font-size: 16px; font-weight: 700; font-family: inherit; cursor: pointer;
        }
        #faixa-instalar ol { margin: 12px 0 0; padding-left: 20px; color: #cbd5e1; font-size: 14px; }
        #faixa-instalar li { margin: 7px 0; line-height: 1.4; }
        #faixa-instalar .tecla {
            display: inline-block; background: #1e293b; color: #f1f5f9;
            border: 1px solid #334155; border-radius: 6px;
            padding: 1px 7px; font-weight: 600; font-size: 13px;
        }`;
        document.head.appendChild(s);
    }

    function fechar() {
        const f = document.getElementById('faixa-instalar');
        if (f) f.remove();
        marcarDispensado();
    }

    function montar(corpoHTML) {
        injetarEstilo();
        if (document.getElementById('faixa-instalar')) return;

        const faixa = document.createElement('div');
        faixa.id = 'faixa-instalar';
        faixa.innerHTML = `
            <div class="cabeca">
                <div>
                    <h3>${cfg.titulo}</h3>
                    <p>${cfg.texto}</p>
                </div>
                <button class="fechar" aria-label="Agora não">&times;</button>
            </div>
            ${corpoHTML}`;

        faixa.querySelector('.fechar').addEventListener('click', fechar);
        document.body.appendChild(faixa);
        return faixa;
    }

    // ---------- Android / Chrome / Edge ----------

    function mostrarBotao() {
        const faixa = montar('<button class="acao">Instalar agora</button>');
        if (!faixa) return;

        faixa.querySelector('.acao').addEventListener('click', async () => {
            if (!promptAdiado) return;
            promptAdiado.prompt();
            const { outcome } = await promptAdiado.userChoice;
            promptAdiado = null;
            faixa.remove();
            if (outcome !== 'accepted') marcarDispensado();
        });
    }

    // ---------- iPhone / iPad ----------
    // O Safari não oferece instalação automática: só resta ensinar o caminho.

    function mostrarPassosIOS() {
        montar(`
            <ol>
                <li>Toque em <span class="tecla">Compartilhar</span> na barra do Safari
                    (o quadrado com a seta para cima).</li>
                <li>Deslize e escolha <span class="tecla">Adicionar à Tela de Início</span>.</li>
                <li>Confirme em <span class="tecla">Adicionar</span>.</li>
            </ol>`);
    }

    // ---------- service worker ----------
    // Registrado na raiz para valer no site inteiro. Sem isso o Chrome
    // não considera o app instalável.

    function registrarServiceWorker() {
        if (!('serviceWorker' in navigator)) return;

        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then(() => {
                // Limpa o registro antigo em /js/, que controlava só aquela pasta
                navigator.serviceWorker.getRegistrations().then(regs => {
                    regs.forEach(r => {
                        if (r.scope.includes('/js/')) r.unregister();
                    });
                });
            })
            .catch(() => { /* http local, navegador antigo: segue sem cache */ });
    }

    // ---------- início ----------

    window.addEventListener('beforeinstallprompt', evento => {
        evento.preventDefault();       // segura o aviso padrão do Chrome
        promptAdiado = evento;
        if (!jaInstalado() && !dispensadoRecentemente()) mostrarBotao();
    });

    window.addEventListener('appinstalled', () => {
        const f = document.getElementById('faixa-instalar');
        if (f) f.remove();
        try { localStorage.removeItem(CHAVE_DISPENSA); } catch (e) {}
    });

    document.addEventListener('DOMContentLoaded', () => {
        registrarServiceWorker();

        if (jaInstalado() || dispensadoRecentemente()) return;

        // No iPhone não existe evento nenhum: espera um pouco para não
        // atropelar a tela e mostra o passo a passo.
        if (ehIOS()) setTimeout(mostrarPassosIOS, 2500);
    });
})();
