from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import requests
import random
from datetime import datetime, date
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Database initialization
def init_db():
    conn = sqlite3.connect('alphalearn.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Words table - stores daily word history
    c.execute('''CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT NOT NULL,
        definition TEXT,
        example TEXT,
        date_assigned DATE NOT NULL,
        UNIQUE(date_assigned)
    )''')
    
    # User progress table
    c.execute('''CREATE TABLE IF NOT EXISTS user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        learned BOOLEAN DEFAULT 0,
        review_count INTEGER DEFAULT 0,
        last_reviewed TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (word_id) REFERENCES words(id),
        UNIQUE(user_id, word_id)
    )''')
    
    conn.commit()
    conn.close()

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Free Dictionary API integration
def fetch_word_definition(word):
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()[0]
            meanings = data.get('meanings', [])
            if meanings:
                definition = meanings[0]['definitions'][0]['definition']
                example = meanings[0]['definitions'][0].get('example', 'No example available')
                return definition, example
    except:
        pass
    return "Definition not available", "No example available"

# Get or create daily word
def get_daily_word():
    conn = sqlite3.connect('alphalearn.db')
    c = conn.cursor()
    today = date.today().isoformat()
    
    # Check if word exists for today
    c.execute('SELECT id, word, definition, example FROM words WHERE date_assigned = ?', (today,))
    result = c.fetchone()
    
    if result:
        conn.close()
        return {'id': result[0], 'word': result[1], 'definition': result[2], 'example': result[3]}
    
    # Generate new word for today
    word_list = ['eloquent', 'benevolent', 'diligent', 'resilient', 'profound', 
                 'meticulous', 'pragmatic', 'ephemeral', 'jubilant', 'serendipity',
                 'ameliorate', 'candor', 'dichotomy', 'efficacious', 'fastidious',
                 'gregarious', 'hapless', 'iconoclast', 'juxtapose', 'loquacious']
    
    # Get words already used
    c.execute('SELECT word FROM words')
    used_words = [row[0] for row in c.fetchall()]
    available_words = [w for w in word_list if w not in used_words]
    
    if not available_words:
        available_words = word_list  # Reset if all used
    
    new_word = random.choice(available_words)
    definition, example = fetch_word_definition(new_word)
    
    c.execute('INSERT INTO words (word, definition, example, date_assigned) VALUES (?, ?, ?, ?)',
              (new_word, definition, example, today))
    conn.commit()
    word_id = c.lastrowid
    conn.close()
    
    return {'id': word_id, 'word': new_word, 'definition': definition, 'example': example}

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
        
        conn = sqlite3.connect('alphalearn.db')
        c = conn.cursor()
        
        try:
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('register.html', error='Username already exists')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect('alphalearn.db')
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
    daily_word = get_daily_word()
    
    # Get user progress
    conn = sqlite3.connect('alphalearn.db')
    c = conn.cursor()
    c.execute('''SELECT COUNT(*) FROM user_progress 
                 WHERE user_id = ? AND learned = 1''', (session['user_id'],))
    words_learned = c.fetchone()[0]
    
    c.execute('''SELECT w.word, w.definition, up.review_count 
                 FROM user_progress up 
                 JOIN words w ON up.word_id = w.id 
                 WHERE up.user_id = ? AND up.learned = 1 
                 ORDER BY up.last_reviewed DESC LIMIT 5''', (session['user_id'],))
    recent_words = c.fetchall()
    conn.close()
    
    return render_template('dashboard.html', 
                          daily_word=daily_word,
                          words_learned=words_learned,
                          recent_words=recent_words)

@app.route('/mark_learned', methods=['POST'])
@login_required
def mark_learned():
    word_id = request.form.get('word_id')
    
    conn = sqlite3.connect('alphalearn.db')
    c = conn.cursor()
    
    c.execute('''INSERT OR REPLACE INTO user_progress 
                 (user_id, word_id, learned, review_count, last_reviewed) 
                 VALUES (?, ?, 1, COALESCE((SELECT review_count FROM user_progress WHERE user_id = ? AND word_id = ?), 0) + 1, CURRENT_TIMESTAMP)''',
              (session['user_id'], word_id, session['user_id'], word_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/review')
@login_required
def review():
    conn = sqlite3.connect('alphalearn.db')
    c = conn.cursor()
    c.execute('''SELECT w.id, w.word, w.definition, w.example, up.review_count 
                 FROM user_progress up 
                 JOIN words w ON up.word_id = w.id 
                 WHERE up.user_id = ? AND up.learned = 1 
                 ORDER BY up.last_reviewed ASC''', (session['user_id'],))
    learned_words = c.fetchall()
    conn.close()
    
    return render_template('review.html', learned_words=learned_words)

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=8080)
