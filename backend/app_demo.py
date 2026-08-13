#!/usr/bin/env python3
"""
Servidor de Demonstração - Descontos Jardins Sky
Versão simplificada usando apenas módulos built-in para teste local
"""

import json
import sqlite3
import uuid
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from hashlib import sha256
import base64
from io import StringIO

# ===== DATABASE SIMPLES =====
DB_PATH = "demo_descontos.db"

def init_db():
    """Inicializa banco de dados de demonstração"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY,
            cpf TEXT UNIQUE,
            nome TEXT,
            email TEXT UNIQUE,
            senha TEXT,
            desconto_tipo TEXT,
            desconto_valor REAL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY,
            cliente_id INTEGER,
            qrcode TEXT UNIQUE,
            data_geracao TEXT,
            status TEXT DEFAULT 'pendente'
        )
    ''')

    # Insere cliente de teste
    try:
        cursor.execute('''
            INSERT INTO clientes
            (cpf, nome, email, senha, desconto_tipo, desconto_valor)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            '12345678901',
            'Cliente Teste',
            'teste@email.com',
            sha256('123456'.encode()).hexdigest(),
            'percentual',
            5.0
        ))
        print("✅ Cliente de teste criado!")
    except:
        print("⚠️ Cliente de teste já existe")

    conn.commit()
    conn.close()

# ===== HANDLER HTTP =====
class PWAHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        """Processa POST requests"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        path = self.path

        if path == '/api/auth/login':
            self.handle_login(body)
        elif path == '/api/auth/cadastro':
            self.handle_cadastro(body)
        elif path == '/api/cupom/gerar':
            self.handle_gerar_cupom(body)
        elif path == '/api/cupom/usar':
            self.handle_usar_cupom(body)
        else:
            self.send_json({'erro': 'Endpoint não encontrado'}, 404)

    def do_GET(self):
        """Processa GET requests"""
        path = self.path

        if path == '/api/health':
            self.send_json({'status': 'OK', 'timestamp': datetime.now().isoformat()})
        elif path == '/api/admin/relatorio':
            self.send_json({
                'total_abastecimentos': 5,
                'total_litros': 150.5,
                'total_valor_desconto': 45.15,
                'abastecimentos': []
            })
        else:
            self.send_json({'erro': 'Endpoint não encontrado'}, 404)

    def handle_login(self, body):
        """Faz login"""
        try:
            data = json.loads(body)
            email = data.get('email')
            senha = data.get('senha')

            if email == 'teste@email.com' and sha256(senha.encode()).hexdigest() == sha256('123456'.encode()).hexdigest():
                self.send_json({
                    'cliente_id': 1,
                    'nome': 'Cliente Teste',
                    'email': email,
                    'mensagem': 'Login realizado com sucesso'
                }, 200)
            else:
                self.send_json({'erro': 'Email ou senha incorretos'}, 401)
        except Exception as e:
            self.send_json({'erro': str(e)}, 500)

    def handle_cadastro(self, body):
        """Cadastra novo cliente"""
        try:
            data = json.loads(body)
            self.send_json({
                'mensagem': 'Cadastro realizado! Confirme seu email.',
                'cliente_id': 2
            }, 201)
        except Exception as e:
            self.send_json({'erro': str(e)}, 500)

    def handle_gerar_cupom(self, body):
        """Gera QR code"""
        try:
            data = json.loads(body)
            cliente_id = data.get('cliente_id')

            # Gera código aleatório
            qrcode_data = str(uuid.uuid4())[:8]

            # Simula QR code em base64 (PNG simples)
            qr_base64 = "data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0id2hpdGUiLz48dGV4dCB4PSI1MCUiIHk9IjUwJSIgZm9udC1zaXplPSIyNCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZHk9Ii4zZW0iPkNEIzogIiArIHFyY29kZV9kYXRhICsgIjwvdGV4dD48L3N2Zz4="

            self.send_json({
                'cupom_id': 1,
                'qrcode_data': qrcode_data,
                'qrcode_image': qr_base64,
                'cliente_nome': 'Cliente Teste',
                'desconto_tipo': 'percentual',
                'desconto_valor': 5.0,
                'mensagem': 'QR code gerado com sucesso!'
            }, 200)
        except Exception as e:
            self.send_json({'erro': str(e)}, 500)

    def handle_usar_cupom(self, body):
        """Registra uso do cupom"""
        try:
            data = json.loads(body)
            litros = float(data.get('litros', 0))
            valor_original = float(data.get('valor_original', 0))
            valor_desconto = valor_original * 0.05
            valor_final = valor_original - valor_desconto

            self.send_json({
                'mensagem': 'Cupom utilizado com sucesso!',
                'litros': litros,
                'valor_original': valor_original,
                'valor_desconto': round(valor_desconto, 2),
                'valor_final': round(valor_final, 2)
            }, 200)
        except Exception as e:
            self.send_json({'erro': str(e)}, 500)

    def send_json(self, data, status=200):
        """Envia resposta JSON"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        """Suporta CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        """Customiza log"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

# ===== MAIN =====
if __name__ == '__main__':
    init_db()

    port = 5000
    server = HTTPServer(('localhost', port), PWAHandler)

    print(f"""
╔════════════════════════════════════════════╗
║   🚗 Descontos Jardins Sky - Demo Server   ║
╚════════════════════════════════════════════╝

✅ Servidor rodando em: http://localhost:{port}

📝 CREDENCIAIS DE TESTE:
   Email: teste@email.com
   Senha: 123456

🔗 ENDPOINTS DISPONÍVEIS:
   POST /api/auth/login
   POST /api/auth/cadastro
   POST /api/cupom/gerar
   POST /api/cupom/usar
   GET  /api/admin/relatorio
   GET  /api/health

💡 DICA: Abra http://localhost:8000 no navegador

⌨️  Pressione CTRL+C para parar
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n❌ Servidor encerrado")
        server.server_close()
