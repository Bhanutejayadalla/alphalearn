import os
import sqlite3
import random
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

# -------------------- App & Config --------------------
app = Flask(__name__)
app.secret_key = os.urandom(24)
DB_PATH = 'alphalearn.db'

# -------------------- Database initialization --------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Daily sets (one per date)
    c.execute('''CREATE TABLE IF NOT EXISTS daily_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date_assigned DATE UNIQUE NOT NULL
    )''')

    # Words
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
        letter TEXT NOT NULL,
        word_id INTEGER NOT NULL,
        UNIQUE(daily_set_id, letter),
        FOREIGN KEY(daily_set_id) REFERENCES daily_sets(id),
        FOREIGN KEY(word_id) REFERENCES words(id)
    )''')

    # User progress per word (for tests)
    c.execute('''CREATE TABLE IF NOT EXISTS user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        first_tested_at TIMESTAMP,
        last_tested_at TIMESTAMP,
        correct_count INTEGER DEFAULT 0,
        incorrect_count INTEGER DEFAULT 0,
        learned INTEGER DEFAULT 0,
        UNIQUE(user_id, word_id)
    )''')

    # User errors (only wrong answers to form the Error-Only Test pool)
    c.execute('''CREATE TABLE IF NOT EXISTS user_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        last_wrong_at TIMESTAMP,
        UNIQUE(user_id, word_id)
    )''')

    # A simple sessions table for daily set sticky assignment per user/day
    c.execute('''CREATE TABLE IF NOT EXISTS user_daily_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date_assigned DATE NOT NULL,
        daily_set_id INTEGER NOT NULL,
        UNIQUE(user_id, date_assigned)
    )''')

    conn.commit()
    conn.close()


# -------------------- Helpers --------------------

def get_db():
    return sqlite3.connect(DB_PATH)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def get_or_create_today_set(c):
    today = date.today().isoformat()
    c.execute('SELECT id FROM daily_sets WHERE date_assigned = ?', (today,))
    row = c.fetchone()
    if row:
        return row[0]

    # Create new set for today (choose 26 random words if words table populated)
    c.execute('INSERT OR IGNORE INTO daily_sets(date_assigned) VALUES(?)', (today,))
    c.execute('SELECT id FROM daily_sets WHERE date_assigned = ?', (today,))
    daily_set_id = c.fetchone()[0]

    # Ensure there are words to choose from; if not, seed a minimal list
    c.execute('SELECT id, word FROM words')
    all_words = c.fetchall()
    if len(all_words) < 26:
        seed = [
            ('Apple','A fruit','I ate an apple'), ('Ball','A toy','The ball bounced'),
            ('Cat','An animal','The cat meowed'), ('Dog','An animal','The dog barked'),
            ('Egg','Food','Eggs are nutritious'), ('Fan','Device','The fan spins'),
            ('Goat','Animal','A goat grazes'), ('Hat','Clothing','He wore a hat'),
            ('Ice','Frozen water','Ice melts'), ('Jug','Container','Jug holds water'),
            ('Kite','Toy','Kite flies high'), ('Lamp','Light','Lamp glows'),
            ('Mug','Cup','Mug for coffee'), ('Nest','Home','Birds build nests'),
            ('Owl','Bird','Owl hoots'), ('Pen','Tool','Pen writes'),
            ('Queen','Royalty','The queen waved'), ('Rose','Flower','Rose smells nice'),
            ('Sun','Star','Sun shines'), ('Tree','Plant','Tree grows tall'),
            ('Umbrella','Tool','Umbrella in rain'), ('Van','Vehicle','Van drives'),
            ('Watch','Time','Watch ticks'), ('Xylophone','Instrument','It plays notes'),
            ('Yarn','Fiber','Yarn knits'), ('Zebra','Animal','Zebra has stripes')
        ]
        c.executemany('INSERT INTO words(word, definition, example) VALUES(?,?,?)', seed)
        c.execute('SELECT id, word FROM words')
        all_words = c.fetchall()

    # Map letters A-Z to random distinct word ids
    letters = [chr(ord('A')+i) for i in range(26)]
    picked = random.sample(all_words, 26)
    for letter, (wid, _word) in zip(letters, picked):
        c.execute('''INSERT OR IGNORE INTO daily_words(daily_set_id, letter, word_id)
                     VALUES(?,?,?)''', (daily_set_id, letter, wid))
    return daily_set_id


def get_today_set_for_user(user_id):
    conn = get_db()
    c = conn.cursor()
    today = date.today().isoformat()

    # If user already has a set for today, reuse it
    c.execute('SELECT daily_set_id FROM user_daily_state WHERE user_id=? AND date_assigned=?', (user_id, today))
    row = c.fetchone()
    if row:
        dsid = row[0]
    else:
        # Ensure a daily set exists for today and assign to user
        dsid = get_or_create_today_set(c)
        c.execute('INSERT OR REPLACE INTO user_daily_state(user_id, date_assigned, daily_set_id) VALUES(?,?,?)', (user_id, today, dsid))
        conn.commit()

    # Load words for the set
    c.execute('''SELECT dw.letter, w.id, w.word, w.definition, w.example
                 FROM daily_words dw JOIN words w ON dw.word_id = w.id
                 WHERE dw.daily_set_id = ? ORDER BY dw.letter''', (dsid,))
    rows = c.fetchall()
    conn.close()
    # Return list of dicts
    return [
        {
            'letter': r[0], 'word_id': r[1], 'word': r[2], 'definition': r[3], 'example': r[4]
        } for r in rows
    ]


# -------------------- Auth (placeholder/simple) --------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id FROM users WHERE username=? AND password=?', (username, password))
        row = c.fetchone()
        if not row:
            # auto-create for simplicity
            c.execute('INSERT OR IGNORE INTO users(username, password) VALUES(?,?)', (username, password))
            conn.commit()
            c.execute('SELECT id FROM users WHERE username=?', (username,))
            row = c.fetchone()
        conn.close()
        session['user_id'] = row[0]
        session['username'] = username
        return redirect(url_for('home'))
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))


# -------------------- Home & Daily Words --------------------
@app.route('/')
@login_required
def home():
    words = get_today_set_for_user(session['user_id'])
    return render_template('index.html', words=words, username=session.get('username'))


# -------------------- Mark Learned (AJAX or form) --------------------
@app.route('/mark_learned', methods=['POST'])
@login_required
def mark_learned():
    # Accept JSON or form-encoded
    payload = request.get_json(silent=True) or request.form
    word_id = payload.get('word_id')
    learned = payload.get('learned')
    # Normalize types
    try:
        word_id = int(word_id)
    except Exception:
        return jsonify({'success': False, 'error': 'invalid word_id'}), 400
    learned_flag = 1 if str(learned).lower() in ('1', 'true', 'yes', 'on') else 0

    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()

    # Upsert progress; ensure first_tested_at is preserved
    c.execute('''INSERT INTO user_progress(user_id, word_id, first_tested_at, last_tested_at, correct_count, incorrect_count, learned)
                 VALUES(?, ?, ?, ?, 0, 0, ?)
                 ON CONFLICT(user_id, word_id) DO UPDATE SET
                     last_tested_at=excluded.last_tested_at,
                     learned=excluded.learned''',
              (session['user_id'], word_id, now, now, learned_flag))

    # If marking learned, clear any error entries for that word
    if learned_flag:
        c.execute('DELETE FROM user_errors WHERE user_id=? AND word_id=?', (session['user_id'], word_id))

    conn.commit()
    conn.close()

    # Respond based on request type
    if request.is_json:
        return jsonify({'success': True, 'word_id': word_id, 'learned': bool(learned_flag)})
    # Fallback to redirect for form posts
    return redirect(url_for('home'))


# -------------------- Testing --------------------

def _update_test_result(word_id: int, correct: bool):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    # Upsert progress counters
    c.execute('''INSERT INTO user_progress(user_id, word_id, first_tested_at, last_tested_at, correct_count, incorrect_count, learned)
                 VALUES(?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(user_id, word_id) DO UPDATE SET
                     last_tested_at=excluded.last_tested_at,
                     correct_count = user_progress.correct_count + excluded.correct_count,
                     incorrect_count = user_progress.incorrect_count + excluded.incorrect_count''',
              (session['user_id'], word_id, now, now, 1 if correct else 0, 0 if correct else 1, 1 if correct else 0))

    if correct:
        # Clear error on pass
        c.execute('DELETE FROM user_errors WHERE user_id=? AND word_id=?', (session['user_id'], word_id))
    else:
        # Record/refresh error on fail
        c.execute('''INSERT INTO user_errors(user_id, word_id, last_wrong_at) VALUES(?,?,?)
                     ON CONFLICT(user_id, word_id) DO UPDATE SET last_wrong_at=excluded.last_wrong_at''',
                  (session['user_id'], word_id, now))
    conn.commit()
    conn.close()


@app.route('/take_test')
@login_required
def take_test():
    # Pool is today's words, excluding already learned if needed
    words = get_today_set_for_user(session['user_id'])
    # Optionally allow query param to include learned
    include_learned = request.args.get('include_learned', '0') in ('1', 'true', 'yes')

    # Get learned map
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT word_id, learned FROM user_progress WHERE user_id=?', (session['user_id'],))
    prog = {wid: learned for (wid, learned) in c.fetchall()}
    conn.close()

    test_pool = []
    for item in words:
        wid = item['word_id']
        if include_learned or not prog.get(wid, 0):
            test_pool.append(item)

    random.shuffle(test_pool)
    return render_template('take_test.html', words=test_pool, mode='daily')


@app.route('/error_test')
@login_required
def error_test():
    # Pool from user_errors
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition, w.example
                 FROM user_errors ue JOIN words w ON ue.word_id=w.id
                 WHERE ue.user_id=? ORDER BY ue.last_wrong_at DESC''', (session['user_id'],))
    rows = c.fetchall()
    conn.close()

    words = [{'word_id': r[0], 'word': r[1], 'definition': r[2], 'example': r[3]} for r in rows]
    random.shuffle(words)
    return render_template('take_test.html', words=words, mode='errors')


@app.route('/submit_answer', methods=['POST'])
@login_required
def submit_answer():
    word_id = int(request.form.get('word_id'))
    user_answer = (request.form.get('answer') or '').strip().lower()

    # Retrieve canonical answer (word itself for spelling test; adjust as needed)
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT word FROM words WHERE id=?', (word_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return redirect(url_for('take_test'))

    correct = (user_answer == (row[0] or '').strip().lower())
    _update_test_result(word_id, correct)

    # Optionally pass feedback via query string
    return redirect(url_for('take_test', feedback='1' if correct else '0'))


# Endpoint to clear a single error manually after passing (AJAX)
@app.route('/clear_error', methods=['POST'])
@login_required
def clear_error():
    wid = (request.get_json(silent=True) or request.form).get('word_id')
    try:
        wid = int(wid)
    except Exception:
        return jsonify({'success': False, 'error': 'invalid word_id'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM user_errors WHERE user_id=? AND word_id=?', (session['user_id'], wid))
    conn.commit()
    conn.close()

    return jsonify({'success': True})


# -------------------- Review --------------------
@app.route('/review')
@login_required
def review():
    conn = get_db()
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
