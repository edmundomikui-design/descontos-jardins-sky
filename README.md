# Descontos Jardins Sky - PWA

Progressive Web App para cadastro de motoristas e emissão de cupons com desconto em combustível.

## Postos Participantes
- **CAJ** - Centro Automotivo Jardins Ltda (Rua Estados Unidos, 1930)
- **SKY** - Sky Auto Posto Ltda (Rua Estados Unidos, 1776)

---

## Estrutura do Projeto

```
pwa-descontos/
├── backend/
│   ├── app.py              # Servidor Flask
│   ├── database.py         # Configuração SQLite
│   ├── requirements.txt    # Dependências Python
│   └── routes/             # (futuro) Organizar rotas
├── frontend/
│   ├── index.html          # Login/Cadastro
│   ├── dashboard.html      # Painel do cliente
│   ├── manifest.json       # PWA manifest
│   ├── css/
│   │   └── style.css       # Estilos
│   └── js/
│       └── app.js          # Lógica do frontend
└── README.md               # Este arquivo
```

---

## Instalação e Execução (Local)

### 1. Backend (Python)

```bash
# Navega até a pasta backend
cd pwa-descontos/backend

# Instala dependências
pip install -r requirements.txt

# Executa o servidor
python app.py
```

O servidor estará disponível em: `http://localhost:5000`

### 2. Frontend

```bash
# Opção A: Usar um servidor HTTP simples
cd pwa-descontos/frontend
python -m http.server 8000

# Opção B: Usar Live Server (VS Code)
# Instale a extensão "Live Server"
# Clique com direito no index.html > Open with Live Server
```

O frontend estará disponível em: `http://localhost:8000`

---

## Fluxo da Aplicação

### 1. Cadastro de Cliente
- Nome, CPF, Ocupação (Táxi/Uber/Outro)
- Telefone e Endereço
- Email e Senha
- Define tipo de desconto (% ou R$) e valor

### 2. Login
- Cliente acessa com email e senha
- Redirecionado para dashboard

### 3. Geração de QR Code
- Cliente gera 1 cupom por dia
- QR code único é exibido
- Cliente mostra no caixa

### 4. Uso do Cupom
- Caixa escaneia QR code
- Sistema registra automaticamente
- Desconto é aplicado

### 5. Relatórios (Admin)
- Visualiza abastecimentos por período
- Filtros: turno, data, posto
- Dados: litros, valores, cupons

---

## Tecnologias

| Componente | Tecnologia |
|-----------|-----------|
| Backend | Python + Flask |
| Frontend | HTML5 + CSS3 + JavaScript |
| Banco de Dados | SQLite |
| QR Code | qrcode.py |
| PWA | Service Worker |

---

## Endpoints da API

### Autenticação
- `POST /api/auth/cadastro` - Cadastrar novo cliente
- `POST /api/auth/login` - Fazer login

### Cupom
- `POST /api/cupom/gerar` - Gerar QR code
- `POST /api/cupom/usar` - Registrar uso do cupom

### Admin
- `GET /api/admin/relatorio` - Buscar relatórios

### Health
- `GET /api/health` - Verificar status do servidor

---

## Deploy

### Opção 1: Render.com (Recomendado)

1. Crie conta em https://render.com
2. Conecte seu repositório GitHub
3. Crie novo "Web Service" com Python
4. Configure:
   - Build: `pip install -r backend/requirements.txt`
   - Start: `python backend/app.py`
5. Deploy automático

**Custo:** Grátis (tier inicial) ou ~$7/mês (production)

### Opção 2: Heroku

1. Crie conta em https://heroku.com
2. Instale Heroku CLI
3. Configure:
   ```bash
   heroku login
   heroku create seu-app-name
   git push heroku main
   ```

**Custo:** ~$5/mês

### Opção 3: Sua própria VPS

- AWS EC2, DigitalOcean, Linode
- Instale Python + Nginx
- Use Gunicorn para servir app
- Nginx como reverse proxy

---

## Variáveis de Ambiente

Crie arquivo `.env` na pasta backend:

```env
FLASK_ENV=production
SECRET_KEY=sua-chave-secreta-aqui
DATABASE_URL=sqlite:///descontos_jardins_sky.db
CORS_ALLOWED_ORIGINS=https://descontos-jardins-sky.com.br
```

---

## Segurança

- ✅ Senhas criptografadas (bcrypt)
- ✅ Validação de CPF
- ✅ HTTPS obrigatório
- ✅ CORS configurado
- ✅ SQL Injection prevenido
- ✅ Rate limiting (futuro)

---

## Próximos Passos

- [ ] Integração com SMS (confirmação)
- [ ] Integração com email (confirmação)
- [ ] Painel administrativo completo
- [ ] Relatórios em Excel/PDF
- [ ] App nativo (futuro)
- [ ] Integração com sistema de bomba

---

## Suporte

Para dúvidas ou problemas:
- Email: edmundo.mikui@gmail.com
- Documentação: Ver arquivos .md da pasta raiz

---

**Versão:** 1.0.0  
**Data:** 12/08/2026  
**Desenvolvido por:** Claude IA para Edmundo
