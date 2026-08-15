const { JSDOM } = require('jsdom');
const fs = require('fs');

const BASE = '/sessions/upbeat-clever-hopper/mnt/aplicativo CAJSKY/pwa-descontos/frontend';
const codigo = fs.readFileSync(BASE + '/js/instalar.js', 'utf8');

let falhas = [];
function checar(desc, ok, extra) {
  console.log(`  [${ok ? 'OK  ' : 'FALHOU'}] ${desc}` + (!ok && extra ? `  -> ${extra}` : ''));
  if (!ok) falhas.push(desc);
}

function montarJanela({ ua, standalone = false, storage = {} }) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><head></head><body></body></html>`,
    { runScripts: 'outside-only', url: 'https://exemplo.com/index.html', pretendToBeVisual: true }
  );
  const w = dom.window;
  // jsdom 30 ignora a opcao userAgent: define na mao
  Object.defineProperty(w.navigator, 'userAgent', { value: ua, configurable: true });
  w.matchMedia = q => ({ matches: standalone && q.includes('standalone'), addListener(){}, removeListener(){} });
  if (standalone) w.navigator.standalone = true;
  const mem = { ...storage };
  Object.defineProperty(w, 'localStorage', { value: {
    getItem: k => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: k => { delete mem[k]; }
  }, configurable: true });
  w.INSTALAR = { titulo: 'Instale o CAJ SKY', texto: 'texto de teste', cor: '#2563eb' };
  w.eval(codigo);
  return { w, dom };
}

function dispararDOMReady(w) {
  w.document.dispatchEvent(new w.Event('DOMContentLoaded', { bubbles: true }));
}

const UA_IOS = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const UA_ANDROID = 'Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36';

(async () => {
console.log('\n=== 1. iPhone no navegador: mostra o passo a passo ===');
{
  const { w } = montarJanela({ ua: UA_IOS });
  dispararDOMReady(w);
  await new Promise(r => setTimeout(r, 3000));
  const f = w.document.getElementById('faixa-instalar');
  checar('faixa aparece no iPhone', !!f);
  if (f) {
    checar('ensina o botão Compartilhar', f.textContent.includes('Compartilhar'));
    checar('ensina Adicionar à Tela de Início', f.textContent.includes('Adicionar à Tela de Início'));
    checar('não mostra botão automático (Safari não tem)', !f.querySelector('.acao'));
    checar('usa o texto da página', f.textContent.includes('Instale o CAJ SKY'));
  }
}

console.log('\n=== 2. iPhone com o app já instalado: fica quieto ===');
{
  const { w } = montarJanela({ ua: UA_IOS, standalone: true });
  dispararDOMReady(w);
  await new Promise(r => setTimeout(r, 3000));
  checar('nenhuma faixa quando já instalado', !w.document.getElementById('faixa-instalar'));
}

console.log('\n=== 3. Android: botão instalar de verdade ===');
{
  const { w } = montarJanela({ ua: UA_ANDROID });
  dispararDOMReady(w);
  let promptChamado = false;
  const evento = new w.Event('beforeinstallprompt');
  evento.prompt = () => { promptChamado = true; };
  evento.userChoice = Promise.resolve({ outcome: 'accepted' });
  w.dispatchEvent(evento);

  const f = w.document.getElementById('faixa-instalar');
  checar('faixa aparece no Android', !!f);
  const botao = f && f.querySelector('.acao');
  checar('tem botão Instalar agora', !!botao && botao.textContent.includes('Instalar'));
  if (botao) {
    botao.dispatchEvent(new w.Event('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 60));
    checar('clique dispara a instalação do Chrome', promptChamado);
    checar('faixa some após instalar', !w.document.getElementById('faixa-instalar'));
  }
}

console.log('\n=== 4. Dispensa é respeitada por 7 dias ===');
{
  const ontem = Date.now() - 1 * 86400000;
  const { w } = montarJanela({ ua: UA_IOS, storage: { cajsky_instalar_dispensado: String(ontem) } });
  dispararDOMReady(w);
  await new Promise(r => setTimeout(r, 3000));
  checar('não insiste 1 dia depois de dispensar', !w.document.getElementById('faixa-instalar'));
}

console.log('\n=== 5. Volta a convidar depois de 7 dias ===');
{
  const antigo = Date.now() - 10 * 86400000;
  const { w } = montarJanela({ ua: UA_IOS, storage: { cajsky_instalar_dispensado: String(antigo) } });
  dispararDOMReady(w);
  await new Promise(r => setTimeout(r, 3000));
  checar('convida de novo após 10 dias', !!w.document.getElementById('faixa-instalar'));
}

console.log('\n=== 6. Botão fechar dispensa e guarda a data ===');
{
  const { w } = montarJanela({ ua: UA_IOS });
  dispararDOMReady(w);
  await new Promise(r => setTimeout(r, 3000));
  const f = w.document.getElementById('faixa-instalar');
  f.querySelector('.fechar').dispatchEvent(new w.Event('click', { bubbles: true }));
  checar('faixa some ao fechar', !w.document.getElementById('faixa-instalar'));
  checar('data da dispensa foi guardada', !!w.localStorage.getItem('cajsky_instalar_dispensado'));
}

console.log('\n' + '='.repeat(55));
if (falhas.length) { console.log(`${falhas.length} falha(s):`); falhas.forEach(f => console.log('  - ' + f)); process.exit(1); }
console.log('Todas as verificações passaram.');
})();
