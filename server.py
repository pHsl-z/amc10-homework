#!/usr/bin/env python3
"""
AMC10 作业管理系统 - 服务端
以 AMC 10 试题汇编 2015-2025 为主题库
"""
import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# ---------- Config ----------
# Priority: env PORT > command line arg > default 9393
PORT = int(os.environ.get('PORT', sys.argv[1] if len(sys.argv) > 1 else '9393'))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROBLEMS_FILE = os.path.join(DATA_DIR, "problems.json")
ASSIGNMENTS_FILE = os.path.join(DATA_DIR, "assignments.json")
RECORDS_FILE = os.path.join(DATA_DIR, "records.json")
STUDENTS_FILE = os.path.join(DATA_DIR, "students.json")

PASSWORD = "3393"

# Password-protected pages - simple HTML form
PASSWORD_FORM = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>请输入密码</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{
    font-family:"Microsoft Yahei","PingFang SC",sans-serif;
    background:linear-gradient(135deg,#1e3a5f,#3182ce);
    min-height:100vh;
    display:flex;align-items:center;justify-content:center;
}
.card{
    background:#fff;
    border-radius:16px;
    padding:40px;
    width:380px;
    box-shadow:0 20px 60px rgba(0,0,0,0.2);
    text-align:center;
}
.card h2{color:#1a365d;font-size:20px;margin-bottom:8px;}
.card p{color:#718096;font-size:14px;margin-bottom:24px;}
.card input{
    width:100%;padding:12px 16px;
    border:1.5px solid #e2e8f0;border-radius:8px;
    font-size:18px;text-align:center;letter-spacing:8px;
    margin-bottom:16px;transition:border-color 0.2s;
}
.card input:focus{outline:none;border-color:#3182ce;}
.card button{
    width:100%;padding:12px;border:none;border-radius:8px;
    background:#3182ce;color:#fff;font-size:16px;font-weight:600;
    cursor:pointer;transition:background 0.2s;
}
.card button:hover{background:#2c5aa0;}
.error{color:#e53e3e;font-size:13px;margin-top:8px;display:none;}
</style>
</head>
<body>
<div class="card">
    <h2>教师管理</h2>
    <p>请输入密码以进入管理页面</p>
    <form id="pwForm" onsubmit="return checkPw()">
        <input type="password" id="pwInput" placeholder="密码" autofocus>
        <button type="submit">进入</button>
        <div class="error" id="pwError">密码错误，请重试</div>
    </form>
</div>
<script>
function checkPw(){
    var v = document.getElementById('pwInput').value;
    if(v === '3393'){
        document.cookie = 'teacher_key=3393; path=/; max-age=86400';
        window.location.href = window.location.pathname + window.location.search.replace(/[?&]key=[^&]*/,'').replace(/^&/,'?');
        return false;
    }
    document.getElementById('pwError').style.display = 'block';
    return false;
}
// Also check URL param
var params = new URLSearchParams(location.search);
if(params.get('key') === '3393'){
    document.cookie = 'teacher_key=3393; path=/; max-age=86400';
}
</script>
</body>
</html>"""

# ---------- Load data ----------
print("Loading problem bank...")
with open(PROBLEMS_FILE, 'r', encoding='utf-8') as f:
    all_problems = json.load(f)
print(f"  Loaded {len(all_problems)} problems")

# Build lookup by seq
problems_by_seq = {p['seq']: p for p in all_problems}

# Ensure data files exist
for fpath in [ASSIGNMENTS_FILE, RECORDS_FILE, STUDENTS_FILE]:
    if not os.path.exists(fpath):
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump([], f)

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _migrate_students(students):
    """Migrate old flat student list to new object format with categories."""
    migrated = []
    for s in students:
        if isinstance(s, str):
            migrated.append({'name': s, 'grade': '', 'class': '', 'region': ''})
        elif isinstance(s, dict) and 'name' in s:
            # Ensure all category fields exist
            s.setdefault('grade', '')
            s.setdefault('class', '')
            s.setdefault('region', '')
            migrated.append(s)
        else:
            migrated.append(s)
    return migrated

# ---------- HTTP Handler ----------
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # Suppress logs

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n).decode('utf-8') if n > 0 else ''

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        qs = parse_qs(parsed.query)

        # --- Static files ---
        if path == '' or path == '/index.html':
            self._serve_static('index.html')
        elif path == '/student.html':
            self._serve_static('student.html')
        elif path == '/records.html':
            self._serve_static('records.html')
        elif path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()

        # --- API: problems ---
        elif path == '/api/problems':
            seqs = qs.get('seqs')
            if seqs:
                ids = [int(x) for x in seqs[0].split(',')]
                result = [problems_by_seq.get(i) for i in ids if i in problems_by_seq]
            else:
                result = all_problems
            self._send_json(result)

        elif path == '/api/problem':
            seq = int(qs.get('seq', [0])[0])
            p = problems_by_seq.get(seq)
            self._send_json(p if p else {'error': 'not found'}, 404 if not p else 200)

        # --- API: assignments ---
        elif path == '/api/assignments':
            data = load_json(ASSIGNMENTS_FILE)
            if qs.get('id'):
                aid = int(qs['id'][0])
                data = [a for a in data if a.get('id') == aid]
            self._send_json(data)

        # --- API: records ---
        elif path == '/api/records':
            records = load_json(RECORDS_FILE)
            assign_id = qs.get('assignment_id')
            student = qs.get('student_name')
            if assign_id:
                aid = int(assign_id[0])
                records = [r for r in records if r.get('assignment_id') == aid]
            if student:
                records = [r for r in records if r.get('student_name', '').lower() == student[0].lower()]
            self._send_json(records)

        # --- API: students ---
        elif path == '/api/students':
            students = load_json(STUDENTS_FILE)
            # Migrate old flat format to new object format
            students = _migrate_students(students)
            self._send_json(students)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        body = json.loads(self._read_body()) if self.headers.get('Content-Length', '0') != '0' else {}

        # --- API: save assignment ---
        if path == '/api/assignments':
            assignments = load_json(ASSIGNMENTS_FILE)
            a = body
            a['id'] = a.get('id', max([x.get('id', 0) for x in assignments] + [0]) + 1)
            # Update or create
            existing = [x for x in assignments if x.get('id') == a['id']]
            if existing:
                for i, x in enumerate(assignments):
                    if x.get('id') == a['id']:
                        assignments[i] = a
                        break
            else:
                assignments.append(a)
            save_json(ASSIGNMENTS_FILE, assignments)
            self._send_json({'status': 'ok', 'id': a['id']})

        # --- API: submit answers (auto-grade) ---
        elif path == '/api/records':
            assignment_id = int(body.get('assignment_id', 0))
            student_name = body.get('student_name', '').strip()
            answers = body.get('answers', {})

            if not student_name:
                self._send_json({'error': 'missing student name'}, 400)
                return

            # Load assignment
            assignments = load_json(ASSIGNMENTS_FILE)
            assignment = next((a for a in assignments if a.get('id') == assignment_id), None)
            if not assignment:
                self._send_json({'error': 'assignment not found'}, 404)
                return

            # Grade
            prob_seqs = assignment.get('problems', [])
            results = []
            correct_count = 0
            for seq in prob_seqs:
                p = problems_by_seq.get(seq)
                if not p:
                    continue
                student_answer = answers.get(str(seq), '').upper().strip()
                correct_answer = p.get('answer', '').upper().strip()
                is_correct = student_answer == correct_answer
                if is_correct:
                    correct_count += 1
                results.append({
                    'seq': seq,
                    'student_answer': student_answer or '(未答)',
                    'correct_answer': correct_answer,
                    'is_correct': is_correct
                })

            # Save record
            record = {
                'assignment_id': assignment_id,
                'assignment_title': assignment.get('title', ''),
                'student_name': student_name,
                'answers': answers,
                'score': correct_count,
                'total': len(prob_seqs),
                'results': results,
                'timestamp': datetime.now().isoformat()
            }

            records = load_json(RECORDS_FILE)
            records.append(record)
            save_json(RECORDS_FILE, records)

            # Update student list
            students = load_json(STUDENTS_FILE)
            if student_name not in students:
                students.append(student_name)
                save_json(STUDENTS_FILE, students)

            self._send_json({
                'status': 'submitted',
                'score': correct_count,
                'total': len(prob_seqs),
                'results': results
            })

        # --- API: delete assignment ---
        elif path == '/api/assignments/delete':
            aid = body.get('id')
            if aid:
                assignments = load_json(ASSIGNMENTS_FILE)
                assignments = [a for a in assignments if a.get('id') != aid]
                save_json(ASSIGNMENTS_FILE, assignments)
            self._send_json({'status': 'ok'})

        # --- API: save student (create/update) ---
        elif path == '/api/students':
            students = load_json(STUDENTS_FILE)
            students = _migrate_students(students)
            new_student = {
                'name': body.get('name', '').strip(),
                'grade': body.get('grade', '').strip(),
                'class': body.get('class', '').strip(),
                'region': body.get('region', '').strip()
            }
            if not new_student['name']:
                self._send_json({'error': 'missing student name'}, 400)
                return
            # Update existing or add new
            existing = [s for s in students if s.get('name') == new_student['name']]
            if existing:
                for i, s in enumerate(students):
                    if s.get('name') == new_student['name']:
                        students[i] = new_student
                        break
            else:
                students.append(new_student)
            save_json(STUDENTS_FILE, students)
            self._send_json({'status': 'ok', 'student': new_student})

        # --- API: delete student ---
        elif path == '/api/students/delete':
            name = body.get('name', '').strip()
            if name:
                students = load_json(STUDENTS_FILE)
                students = _migrate_students(students)
                students = [s for s in students if s.get('name') != name]
                save_json(STUDENTS_FILE, students)
            self._send_json({'status': 'ok'})

        else:
            self.send_response(404)
            self.end_headers()

    def _check_password(self):
        """Check if teacher password (3393) is provided via cookie or query param."""
        qs = parse_qs(urlparse(self.path).query)
        # Check query param
        if qs.get('key') and qs['key'][0] == PASSWORD:
            return True
        # Check cookie
        cookie = self.headers.get('Cookie', '')
        if f'teacher_key={PASSWORD}' in cookie:
            return True
        return False

    def _serve_static(self, filename):
        # Password-protect teacher pages
        if filename in ('index.html', 'records.html'):
            if not self._check_password():
                self.send_response(200)
                body = PASSWORD_FORM.encode('utf-8')
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
                return
        fpath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(fpath):
            self.send_response(404)
            self.end_headers()
            return
        with open(fpath, 'rb') as f:
            data = f.read()
        ext = os.path.splitext(filename)[1]
        ct = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
        }.get(ext, 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

# ---------- Start server ----------
if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"AMC10 作业管理系统 (题库: {len(all_problems)}题)")
    print(f"启动地址: http://localhost:{PORT}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()