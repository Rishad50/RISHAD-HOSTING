from flask import Flask, render_template, request, redirect, url_for, jsonify
import json
import os
import subprocess
import uuid
from datetime import datetime
import sys
import shutil
import threading
import time
import zipfile
import psutil
import re

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

SERVERS_FILE = 'servers.json'
BOTS_DIR = 'bots'
CPU_HISTORY = {}
RUNNING_PROCESSES = {}

os.makedirs(BOTS_DIR, exist_ok=True)

# ============================================
# ডাটাবেস ও হেল্পার ফাংশন
# ============================================

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        default_data = {}
        save_servers(default_data)
        return default_data
    with open(SERVERS_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_servers(data):
    with open(SERVERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_server_dir(server_id):
    server_dir = os.path.join(BOTS_DIR, server_id)
    os.makedirs(server_dir, exist_ok=True)
    return server_dir

def create_default_files(server_dir):
    main_py = os.path.join(server_dir, 'main.py')
    if not os.path.exists(main_py):
        with open(main_py, 'w', encoding='utf-8') as f:
            f.write('''# JUBAYER HOSTING - Bot
import time

print("\\033[92m" + "=" * 40)
print("  Bot is running on JUBAYER HOSTING")
print("  Termux Console Ready!")
print("=" * 40 + "\\033[0m")

counter = 0
while True:
    counter += 1
    print(f"\\033[94m[{time.strftime('%H:%M:%S')}]\\033[0m \\033[93mHeartbeat #{counter}\\033[0m | \\033[92mActive\\033[0m")
    time.sleep(10)
''')
    
    req_file = os.path.join(server_dir, 'requirements.txt')
    if not os.path.exists(req_file):
        with open(req_file, 'w', encoding='utf-8') as f:
            f.write('# Add your pip packages here\n')

def get_or_create_default_server():
    servers = load_servers()
    if 'default' not in servers:
        server_dir = get_server_dir('default')
        create_default_files(server_dir)
        servers['default'] = {
            'server_id': 'default',
            'name': 'Default Server',
            'type': 'python',
            'ram': '1GB',
            'disk': '1GB',
            'status': 'stopped',
            'pid': None,
            'created': str(datetime.now()),
            'main_file': 'main.py',
            'requirements_file': 'requirements.txt',
            'cpu_limit': 80,
            'rate_limit_exceeded': False,
            'stopped_by_user': False
        }
        save_servers(servers)
    return servers['default']

# ============================================
# রেট লিমিট ও প্রসেস মনিটর
# ============================================

class RateLimiter:
    def check_rate(self, server_id, limit_percent):
        if server_id not in CPU_HISTORY:
            CPU_HISTORY[server_id] = []
        servers = load_servers()
        server = servers.get(server_id)
        if not server or server.get('status') != 'running':
            return False, 0
        pid = server.get('pid')
        if not pid: 
            return False, 0
        try:
            proc = psutil.Process(pid)
            cpu = proc.cpu_percent(interval=0.5)
            now = time.time()
            CPU_HISTORY[server_id].append({'time': now, 'cpu': cpu})
            CPU_HISTORY[server_id] = [h for h in CPU_HISTORY[server_id] if now - h['time'] < 30]
            recent = [h['cpu'] for h in CPU_HISTORY[server_id] if now - h['time'] < 10]
            if recent:
                avg_cpu = sum(recent) / len(recent)
                if avg_cpu > limit_percent:
                    return True, avg_cpu
        except Exception: 
            pass
        return False, 0

rate_limiter = RateLimiter()

def run_bot(server_id, main_file='main.py', requirements_file='requirements.txt'):
    server_dir = get_server_dir(server_id)
    main_path = os.path.join(server_dir, main_file)
    log_file = os.path.join(server_dir, 'output.log')
    python_exe = sys.executable
    
    def log(msg):
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"{msg}\n")
                f.flush()
        except Exception:
            pass
    
    if not os.path.exists(main_path):
        return None, f"ERROR: {main_file} not found!"
    
    if os.path.exists(log_file):
        try: 
            os.remove(log_file)
        except Exception: 
            open(log_file, 'w').close()
    
    ts = lambda: datetime.now().strftime('%I:%M:%S %p')
    servers = load_servers()
    server = servers.get(server_id, {})
    cpu_limit = server.get('cpu_limit', 80)
    
    log(f"\033[90m[{ts()}] Starting server process...\033[0m")
    
    if requirements_file and requirements_file.strip():
        req_path = os.path.join(server_dir, requirements_file.strip())
        if os.path.exists(req_path):
            with open(req_path, 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f.read().split('\n') if l.strip() and not l.strip().startswith('#')]
            if lines:
                log(f"\033[93m[{ts()}] Installing requirements...\033[0m")
                try:
                    proc = subprocess.Popen(
                        [python_exe, '-m', 'pip', 'install', '-r', os.path.abspath(req_path), '--disable-pip-version-check'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    for line in iter(proc.stdout.readline, ''):
                        if line.strip(): 
                            log(line.rstrip())
                    proc.wait()
                except Exception as e:
                    log(f"\033[91m[{ts()}] pip error: {str(e)}\033[0m")
    
    try:
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        env['TERM'] = 'xterm-256color'
        
        proc = subprocess.Popen(
            [python_exe, '-u', os.path.abspath(main_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=server_dir,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        RUNNING_PROCESSES[server_id] = proc
        
        def rate_monitor():
            while proc.poll() is None:
                time.sleep(5)
                exceeded, avg_cpu = rate_limiter.check_rate(server_id, cpu_limit)
                if exceeded:
                    log(f"\n\033[91m[{ts()}] CPU Limit exceeded ({avg_cpu:.1f}% > {cpu_limit}%), stopping...\033[0m")
                    proc.terminate()
                    servers = load_servers()
                    if server_id in servers:
                        servers[server_id]['status'] = 'stopped'
                        servers[server_id]['pid'] = None
                        servers[server_id]['rate_limit_exceeded'] = True
                        save_servers(servers)
                    RUNNING_PROCESSES.pop(server_id, None)
                    break
        
        threading.Thread(target=rate_monitor, daemon=True).start()
        
        def stream_output():
            try:
                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break
                    if any(c in line for c in ['\x1b[2J', '\x1b[H', '[H[2J', '\033[2J', '\x1b[3J']):
                        with open(log_file, 'w', encoding='utf-8') as f:
                            f.write("")
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(line)
                        f.flush()
            except Exception: 
                pass
            finally:
                RUNNING_PROCESSES.pop(server_id, None)
        
        threading.Thread(target=stream_output, daemon=True).start()
        return proc.pid, None
    except Exception as e:
        log(f"\033[91m[{ts()}] Error: {str(e)}\033[0m")
        return None, str(e)

def stop_bot_process(pid):
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
        return True
    except Exception:
        return False

# ============================================
# পেজ রাউটস
# ============================================

@app.route('/')
@app.route('/servers')
def servers_dashboard():
    get_or_create_default_server()
    return render_template('server.html')

@app.route('/<server_id>')
def server_panel(server_id):
    servers = load_servers()
    if server_id not in servers:
        server_dir = get_server_dir(server_id)
        create_default_files(server_dir)
        servers[server_id] = {
            'server_id': server_id,
            'name': server_id,
            'type': 'python',
            'ram': '1GB',
            'disk': '1GB',
            'status': 'stopped',
            'pid': None,
            'created': str(datetime.now()),
            'main_file': 'main.py',
            'requirements_file': 'requirements.txt',
            'cpu_limit': 80,
            'rate_limit_exceeded': False,
            'stopped_by_user': False
        }
        save_servers(servers)
    return render_template('home.html', current_server=servers[server_id])

# ============================================
# মাল্টি-সার্ভার API
# ============================================

@app.route('/api/servers', methods=['GET'])
def api_get_servers():
    get_or_create_default_server()
    servers = load_servers()
    server_list = []
    for s_id, s_data in servers.items():
        server_list.append({
            'server_id': s_id,
            'name': s_data.get('name', s_id),
            'status': s_data.get('status', 'stopped'),
            'type': s_data.get('type', 'python'),
            'created': s_data.get('created', '')
        })
    return jsonify({'servers': server_list})

@app.route('/api/create_server', methods=['POST'])
def api_create_server():
    data = request.get_json() or {}
    raw_name = data.get('name', '').strip()
    if not raw_name:
        return jsonify({'status': 'error', 'message': 'Server name is required'}), 400
    
    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_name.lower())[:20]
    if not clean_id:
        clean_id = f"server_{int(time.time())}"
    
    servers = load_servers()
    if clean_id in servers:
        clean_id = f"{clean_id}_{int(time.time()) % 1000}"
        
    server_dir = get_server_dir(clean_id)
    create_default_files(server_dir)
    
    servers[clean_id] = {
        'server_id': clean_id,
        'name': raw_name,
        'type': 'python',
        'ram': '1GB',
        'disk': '1GB',
        'status': 'stopped',
        'pid': None,
        'created': str(datetime.now()),
        'main_file': 'main.py',
        'requirements_file': 'requirements.txt',
        'cpu_limit': 80,
        'rate_limit_exceeded': False,
        'stopped_by_user': False
    }
    save_servers(servers)
    return jsonify({'status': 'success', 'server_id': clean_id, 'message': 'Created successfully'})

@app.route('/api/delete_server/<server_id>', methods=['DELETE', 'POST'])
def api_delete_server(server_id):
    if server_id == 'default':
        return jsonify({'status': 'error', 'message': 'Default server cannot be deleted'}), 400
    servers = load_servers()
    if server_id not in servers:
        return jsonify({'status': 'error', 'message': 'Server not found'}), 404
        
    if servers[server_id].get('pid'):
        stop_bot_process(servers[server_id]['pid'])
    RUNNING_PROCESSES.pop(server_id, None)
    
    server_dir = os.path.join(BOTS_DIR, server_id)
    if os.path.exists(server_dir):
        try:
            shutil.rmtree(server_dir)
        except Exception:
            pass
            
    del servers[server_id]
    save_servers(servers)
    return jsonify({'status': 'success', 'message': 'Deleted successfully'})

# ============================================
# বট কন্ট্রোল ও লগ API
# ============================================

@app.route('/api/start/<server_id>', methods=['POST'])
def api_start_server(server_id):
    servers = load_servers()
    server = servers.get(server_id)
    if not server: 
        return jsonify({'status': 'error', 'message': 'Server not found'}), 404
    if server.get('status') == 'running': 
        return jsonify({'status': 'error', 'message': 'Already running!'})
    
    server['rate_limit_exceeded'] = False
    server['stopped_by_user'] = False
    
    pid, error = run_bot(server_id, server.get('main_file', 'main.py'), server.get('requirements_file', 'requirements.txt'))
    
    if pid:
        server['status'] = 'running'
        server['pid'] = pid
        server['started_at'] = str(datetime.now())
        save_servers(servers)
        return jsonify({'status': 'success', 'message': 'Started!'})
    return jsonify({'status': 'error', 'message': error or 'Failed'}), 500

@app.route('/api/stop/<server_id>', methods=['POST'])
def api_stop(server_id):
    servers = load_servers()
    server = servers.get(server_id)
    if not server: 
        return jsonify({'status': 'error', 'message': 'Server not found'}), 404
    
    if server.get('pid'):
        stop_bot_process(server['pid'])
    
    RUNNING_PROCESSES.pop(server_id, None)
    server['status'] = 'stopped'
    server['pid'] = None
    server['stopped_by_user'] = True
    save_servers(servers)
    
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n\033[91m[{datetime.now().strftime('%I:%M:%S %p')}] Server stopped\033[0m\n")
    except Exception: 
        pass
    return jsonify({'status': 'success', 'message': 'Stopped'})

@app.route('/api/restart/<server_id>', methods=['POST'])
def api_restart(server_id):
    api_stop(server_id)
    time.sleep(1)
    return api_start_server(server_id)

@app.route('/api/logs/<server_id>')
def api_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            logs = f.read()
    else: 
        logs = ""
    return jsonify({'logs': logs, 'output': logs})

@app.route('/api/clear_logs/<server_id>', methods=['POST'])
def api_clear_logs(server_id):
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    try:
        if os.path.exists(log_file):
            open(log_file, 'w', encoding='utf-8').close()
        return jsonify({'status': 'success', 'message': 'Cleared'})
    except Exception: 
        return jsonify({'status': 'error', 'message': 'Failed to clear logs'}), 500

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.get_json() or {}
    cmd = data.get('cmd', '')
    server_id = data.get('server_id', 'default')
    log_file = os.path.join(get_server_dir(server_id), 'output.log')
    
    if server_id in RUNNING_PROCESSES:
        proc = RUNNING_PROCESSES[server_id]
        if proc.poll() is None:
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
                return jsonify({'status': 'success', 'output': f'Sent input: {cmd}\n'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=get_server_dir(server_id), timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        output = (result.stdout + result.stderr)[:4000]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n\033[93m$ {cmd}\033[0m\n{output}\n")
        return jsonify({'status': 'success', 'output': output})
    except Exception as e: 
        return jsonify({'status': 'error', 'message': str(e)})

# ============================================
# ফাইল ম্যানেজার API
# ============================================

def format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f} KB"
    else:
        return f"{size_bytes/(1024*1024):.1f} MB"

@app.route('/api/files/<server_id>')
def api_files(server_id):
    folder = request.args.get('path', '') or request.args.get('folder', '')
    server_dir = get_server_dir(server_id)
    target_dir = os.path.normpath(os.path.join(server_dir, folder.lstrip('/\\'))) if folder else server_dir

    if not os.path.exists(target_dir): 
        return jsonify({'files': []})
    
    files = []
    try:
        for item in sorted(os.listdir(target_dir)):
            item_path = os.path.join(target_dir, item)
            is_dir = os.path.isdir(item_path)
            size = os.path.getsize(item_path) if not is_dir else 0
            files.append({
                'name': item,
                'is_dir': is_dir,
                'size': size,
                'size_formatted': format_file_size(size) if not is_dir else '-',
                'modified': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%Y-%m-%d %H:%M')
            })
    except Exception:
        pass
    return jsonify({'files': files})

@app.route('/api/file/<server_id>', methods=['GET'])
def api_get_file(server_id):
    file_rel_path = request.args.get('path', '') or request.args.get('filename', '')
    filepath = os.path.normpath(os.path.join(get_server_dir(server_id), file_rel_path.lstrip('/\\')))
    if os.path.exists(filepath) and os.path.isfile(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f: 
                return jsonify({'content': f.read()})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/file/<server_id>', methods=['POST'])
def api_save_file(server_id):
    data = request.get_json() or {}
    file_rel_path = data.get('path', '') or data.get('filename', '')
    filepath = os.path.normpath(os.path.join(get_server_dir(server_id), file_rel_path.lstrip('/\\')))
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f: 
        f.write(data.get('content', ''))
    return jsonify({'success': True, 'message': 'Saved successfully'})

@app.route('/api/file/<server_id>', methods=['DELETE'])
def api_delete_file(server_id):
    data = request.get_json() or {}
    file_rel_path = data.get('path', '') or data.get('filename', '')
    filepath = os.path.normpath(os.path.join(get_server_dir(server_id), file_rel_path.lstrip('/\\')))
    if os.path.exists(filepath):
        if os.path.isdir(filepath): 
            shutil.rmtree(filepath)
        else: 
            os.remove(filepath)
        return jsonify({'success': True, 'message': 'Deleted successfully'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/upload/<server_id>', methods=['POST'])
def api_upload(server_id):
    if 'file' not in request.files: 
        return jsonify({'error': 'No file uploaded'}), 400
    folder = request.form.get('path', '') or request.form.get('folder', '')
    server_dir = get_server_dir(server_id)
    target_dir = os.path.normpath(os.path.join(server_dir, folder.lstrip('/\\'))) if folder else server_dir
    os.makedirs(target_dir, exist_ok=True)

    uploaded_files = request.files.getlist('file')
    for file in uploaded_files:
        if file.filename:
            file.save(os.path.join(target_dir, file.filename))
    return jsonify({'success': True, 'message': 'Uploaded successfully'})

@app.route('/api/create_folder/<server_id>', methods=['POST'])
def api_create_folder(server_id):
    data = request.get_json() or {}
    folder_rel = data.get('path', '') or data.get('folder_name', '')
    target = os.path.normpath(os.path.join(get_server_dir(server_id), folder_rel.lstrip('/\\')))
    os.makedirs(target, exist_ok=True)
    return jsonify({'success': True, 'message': 'Folder created'})

@app.route('/api/rename/<server_id>', methods=['POST'])
def api_rename(server_id):
    d = request.get_json() or {}
    server_dir = get_server_dir(server_id)
    old_rel = d.get('old_path', '')
    new_rel = d.get('new_path', '')
    old_path = os.path.normpath(os.path.join(server_dir, old_rel.lstrip('/\\')))
    new_path = os.path.normpath(os.path.join(server_dir, new_rel.lstrip('/\\')))
    
    if os.path.exists(old_path):
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.rename(old_path, new_path)
        return jsonify({'success': True, 'message': 'Renamed successfully'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/extract/<server_id>', methods=['POST'])
def api_extract(server_id):
    data = request.get_json() or {}
    file_rel = data.get('file_path', '')
    target_rel = data.get('target_path', '')
    server_dir = get_server_dir(server_id)
    
    zip_path = os.path.normpath(os.path.join(server_dir, file_rel.lstrip('/\\')))
    dest_path = os.path.normpath(os.path.join(server_dir, target_rel.lstrip('/\\'))) if target_rel else os.path.dirname(zip_path)
    
    if os.path.exists(zip_path) and zip_path.endswith('.zip'):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(dest_path)
            return jsonify({'status': 'success', 'message': 'Extracted successfully'})
        except Exception as e: 
            return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'error', 'message': 'Invalid ZIP file'}), 400

@app.route('/api/get_startup/<server_id>')
def api_get_startup(server_id):
    servers = load_servers()
    server = servers.get(server_id, {})
    return jsonify({
        'main_file': server.get('main_file', 'main.py'), 
        'req_file': server.get('requirements_file', 'requirements.txt')
    })

@app.route('/api/set_startup/<server_id>', methods=['POST'])
def api_set_startup(server_id):
    d = request.get_json() or {}
    servers = load_servers()
    if server_id in servers:
        servers[server_id]['main_file'] = d.get('main_file', 'main.py')
        servers[server_id]['requirements_file'] = d.get('req_file') or d.get('requirements_file', 'requirements.txt')
        save_servers(servers)
        return jsonify({'success': True, 'message': 'Startup config saved'})
    return jsonify({'error': 'Not found'}), 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
