from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import requests
import random
from datetime import datetime, date
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24)
DB_PATH = 'alphalearn.db'

# -------------------- Database initialization --------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Daily sets table - one row per date for 26 unique words
    c.execute('''CREATE TABLE IF NOT EXISTS daily_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date_assigned DATE UNIQUE NOT NULL
    )''')
    # Words table - stores all words; a word can appear in many days but each day has 26 unique rows
    c.execute('''CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT NOT NULL,
        definition TEXT,
        example TEXT
    )''')
    # Daily words mapping (26 per day, letters A-Z)
    c.execute('''CREATE TABLE IF NOT EXISTS daily_words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        daily_set_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        letter CHAR(1) NOT NULL,
        UNIQUE(daily_set_id, letter),
        FOREIGN KEY(daily_set_id) REFERENCES daily_sets(id),
        FOREIGN KEY(word_id) REFERENCES words(id)
    )''')
    # User progress for testing/learning
    c.execute('''CREATE TABLE IF NOT EXISTS user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        first_tested_at TIMESTAMP,
        last_tested_at TIMESTAMP,
        correct_count INTEGER DEFAULT 0,
        incorrect_count INTEGER DEFAULT 0,
        learned BOOLEAN DEFAULT 0,
        UNIQUE(user_id, word_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(word_id) REFERENCES words(id)
    )''')
    # Error list for wrong answers history
    c.execute('''CREATE TABLE IF NOT EXISTS user_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        last_wrong_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, word_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(word_id) REFERENCES words(id)
    )''')
    conn.commit()
    conn.close()

# -------------------- Auth helpers --------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# -------------------- Dictionary API --------------------
def fetch_word_definition(word):
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        response = requests.get(url, timeout=6)
        if response.status_code == 200:
            data = response.json()[0]
            meanings = data.get('meanings', [])
            if meanings:
                definition = meanings[0]['definitions'][0]['definition']
                example = meanings[0]['definitions'][0].get('example', 'No example available')
                return definition, example
    except Exception:
        pass
    return "Definition not available", "No example available"

# -------------------- Daily 26 words generator --------------------
SEED_WORDS = [
    # At least a couple per letter to allow uniqueness
    'abate','abjure','abyss','banal','bastion','befriend','cadence','cajole','candid','daunt','debunk','decipher',
    'eager','ebullient','echelon','facet','fallacy','fastidious','gaiety','gargantuan','genial','habitat','hackneyed','halcyon',
    'iconic','ideal','idle','jaunt','jeer','jocular','keen','kinetic','knack','labile','laconic','lambent',
    'malleable','manifest','maverick','naive','nascent','nebulous','oasis','oblique','odious','palatable','palpable','paragon',
    'quaint','quell','querulous','rabid','rancor','rapt','sagacious','salient','sanguine','tacit','tangible','tenable',
    'ubiquitous','ulterior','umbrage','vacuous','valiant','venerable','wane','wary','witty','xenial','xeric','xiphoid',
    'yare','yeoman','yield','zeal','zenith','zephyr'
]

LETTERS = [chr(c) for c in range(ord('A'), ord('Z')+1)]

def ensure_daily_set(date_str: str):
    """Ensure a daily_set with 26 unique words (A-Z) exists for date_str."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # If set exists, just return mapping
    c.execute('SELECT id FROM daily_sets WHERE date_assigned = ?', (date_str,))
    row = c.fetchone()
    if row:
        daily_set_id = row[0]
        conn.close()
        return daily_set_id
    # Create set
    c.execute('INSERT INTO daily_sets(date_assigned) VALUES (?)', (date_str,))
    daily_set_id = c.lastrowid
    # To maintain uniqueness for the day, pick one word per letter A-Z.
    # Try to find a word from SEED_WORDS starting with each letter; if none, generate a fallback pseudo-word.
    for letter in LETTERS:
        candidates = [w for w in SEED_WORDS if w.lower().startswith(letter.lower())]
        if not candidates:
            # Fallback deterministic pseudo word
            candidates = [f"{letter.lower()}-word-{random.randint(100,999)}"]
        chosen = random.choice(candidates)
        # Insert or reuse in words table
        c.execute('SELECT id FROM words WHERE word = ?', (chosen,))
        wrow = c.fetchone()
        if wrow:
            word_id = wrow[0]
        else:
            definition, example = fetch_word_definition(chosen)
            c.execute('INSERT INTO words(word, definition, example) VALUES(?,?,?)', (chosen, definition, example))
            word_id = c.lastrowid
        # Map to daily_words
        c.execute('INSERT OR IGNORE INTO daily_words(daily_set_id, word_id, letter) VALUES(?,?,?)', (daily_set_id, word_id, letter))
    conn.commit()
    conn.close()
    return daily_set_id


def get_today_words():
    today = date.today().isoformat()
    daily_set_id = ensure_daily_set(today)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition, w.example, dw.letter
                 FROM daily_words dw JOIN words w ON w.id = dw.word_id
                 WHERE dw.daily_set_id = ? ORDER BY dw.letter ASC''', (daily_set_id,))
    rows = c.fetchall()
    conn.close()
    return [
        { 'id': r[0], 'word': r[1], 'definition': r[2], 'example': r[3], 'letter': r[4] }
        for r in rows
    ]

# -------------------- App routes --------------------
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('register.html', error='All fields required')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error='Username already exists')
        finally:
            conn.close()
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, username FROM users WHERE username = ? AND password = ?', (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['username'] = user[1]
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    today_words = get_today_words()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT COUNT(*) FROM user_progress WHERE user_id = ? AND learned = 1''', (session['user_id'],))
    words_learned = c.fetchone()[0]
    c.execute('''SELECT w.word, w.definition, up.correct_count, up.incorrect_count
                 FROM user_progress up JOIN words w ON up.word_id = w.id
                 WHERE up.user_id = ? AND up.learned = 1
                 ORDER BY up.last_tested_at DESC LIMIT 5''', (session['user_id'],))
    recent_words = c.fetchall()
    conn.close()
    return render_template('dashboard.html', today_words=today_words, words_learned=words_learned, recent_words=recent_words)


# -------------------- Take Test (5 untested words) --------------------
@app.route('/take_test')
@login_required
def take_test():
    # Pick 5 words the user hasn't been tested on before (first_tested_at is NULL)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition
                 FROM words w
                 LEFT JOIN user_progress up ON up.word_id = w.id AND up.user_id = ?
                 WHERE up.word_id IS NULL
                 ORDER BY RANDOM() LIMIT 5''', (session['user_id'],))
    q = c.fetchall()
    conn.close()
    return render_template('take_test.html', questions=q)


@app.route('/submit_test', methods=['POST'])
@login_required
def submit_test():
    # Request contains answers json-like: ids[], correct(bool) per question
    # For simplicity, expect fields: word_id[], is_correct[]
    ids = request.form.getlist('word_id')
    correctness = request.form.getlist('is_correct')
    now = datetime.utcnow().isoformat(' ')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for i, wid in enumerate(ids):
        correct = correctness[i] == '1'
        # Upsert progress
        c.execute('''INSERT INTO user_progress(user_id, word_id, first_tested_at, last_tested_at, correct_count, incorrect_count, learned)
                     VALUES(?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(user_id, word_id) DO UPDATE SET
                         last_tested_at=excluded.last_tested_at,
                         correct_count = user_progress.correct_count + excluded.correct_count,
                         incorrect_count = user_progress.incorrect_count + excluded.incorrect_count,
                         learned = CASE WHEN user_progress.correct_count + excluded.correct_count >= 3 AND user_progress.incorrect_count + excluded.incorrect_count = 0 THEN 1 ELSE user_progress.learned END''',
                 (session['user_id'], wid, now, now, 1 if correct else 0, 0 if correct else 1, 1 if correct else 0))
        if correct:
            # remove from errors if present
            c.execute('DELETE FROM user_errors WHERE user_id = ? AND word_id = ?', (session['user_id'], wid))
        else:
            # add/update error list
            c.execute('''INSERT INTO user_errors(user_id, word_id, last_wrong_at) VALUES(?,?,?)
                       ON CONFLICT(user_id, word_id) DO UPDATE SET last_wrong_at = excluded.last_wrong_at''',
                      (session['user_id'], wid, now))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


# -------------------- Error-only Test --------------------
@app.route('/error_test')
@login_required
def error_test():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition
                 FROM user_errors ue JOIN words w ON w.id = ue.word_id
                 WHERE ue.user_id = ? ORDER BY ue.last_wrong_at DESC LIMIT 10''', (session['user_id'],))
    q = c.fetchall()
    conn.close()
    return render_template('error_test.html', questions=q)


@app.route('/submit_error_test', methods=['POST'])
@login_required
def submit_error_test():
    ids = request.form.getlist('word_id')
    correctness = request.form.getlist('is_correct')
    now = datetime.utcnow().isoformat(' ')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for i, wid in enumerate(ids):
        correct = correctness[i] == '1'
        # Update progress counters
        c.execute('''INSERT INTO user_progress(user_id, word_id, first_tested_at, last_tested_at, correct_count, incorrect_count, learned)
                     VALUES(?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(user_id, word_id) DO UPDATE SET
                         last_tested_at=excluded.last_tested_at,
                         correct_count = user_progress.correct_count + excluded.correct_count,
                         incorrect_count = user_progress.incorrect_count + excluded.incorrect_count''',
                  (session['user_id'], wid, now, now, 1 if correct else 0, 0 if correct else 1, 1 if correct else 0))
        if correct:
            # Allow clearing the error on pass
            c.execute('DELETE FROM user_errors WHERE user_id = ? AND word_id = ?', (session['user_id'], wid))
        else:
            c.execute('''INSERT INTO user_errors(user_id, word_id, last_wrong_at) VALUES(?,?,?)
                       ON CONFLICT(user_id, word_id) DO UPDATE SET last_wrong_at = excluded.last_wrong_at''',
                      (session['user_id'], wid, now))
    conn.commit()
    conn.close()
    return redirect(url_for('error_test'))


# Endpoint to clear a single error manually after passing
@app.route('/clear_error', methods=['POST'])
@login_required
def clear_error():
    wid = request.form.get('word_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM user_errors WHERE user_id = ? AND word_id = ?', (session['user_id'], wid))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# -------------------- Review (existing) --------------------
@app.route('/review')
@login_required
def review():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition, w.example,
                        up.correct_count, up.incorrect_count, up.learned
                 FROM user_progress up JOIN words w ON up.word_id = w.id
                 WHERE up.user_id = ? ORDER BY up.last_tested_at DESC''', (session['user_id'],))
    learned_words = c.fetchall()
    conn.close()
    return render_template('review.html', learned_words=learned_words)


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=8080)
