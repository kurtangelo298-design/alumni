from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import base64
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'slsu-alumni-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///alumni.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
db = SQLAlchemy(app)

# ============== DATABASE MODELS ==============
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_number = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    maiden_name = db.Column(db.String(100), default='')
    year_graduated = db.Column(db.Integer, nullable=False)
    course = db.Column(db.String(200), nullable=False)
    current_job = db.Column(db.String(200), default='')
    location = db.Column(db.String(200), default='')
    profile_pic = db.Column(db.Text, default='')  # base64
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.Text, default='')  # base64
    timestamp = db.Column(db.DateTime, default=datetime.now)
    user = db.relationship('User', backref=db.backref('posts', lazy=True))

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)  # for replies
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    user = db.relationship('User', backref=db.backref('comments', lazy=True))
    post = db.relationship('Post', backref=db.backref('comments', lazy=True, cascade='all, delete-orphan'))
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy=True, cascade='all, delete-orphan')

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post = db.relationship('Post', backref=db.backref('likes', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('likes', lazy=True))

class CommentLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    comment = db.relationship('Comment', backref=db.backref('likes', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('comment_likes', lazy=True))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, default=False)
    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])

class HomecomingSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    years_before = db.Column(db.Integer, default=10)
    event_date = db.Column(db.String(20), default='')
    venue = db.Column(db.String(300), default='SLSU Main Campus')
    deadline = db.Column(db.String(20), default='')
    details = db.Column(db.Text, default='')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(200), default='#')
    timestamp = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, default=False)

# ============== CREATE TABLES & INIT ADMIN + MIGRATION ==============
with app.app_context():
    db.create_all()
    # Auto-migrate existing DB: add parent_id column to comment table if missing
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if 'comment' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('comment')]
        if 'parent_id' not in cols:
            try:
                db.session.execute(text('ALTER TABLE comment ADD COLUMN parent_id INTEGER'))
                db.session.commit()
            except Exception:
                db.session.rollback()
    # Create default admin if not exists
    admin = User.query.filter_by(student_number='ADMIN001').first()
    if not admin:
        admin = User(
            student_number='ADMIN001',
            full_name='System Administrator',
            year_graduated=2000,
            course='Administration',
            password=generate_password_hash('admin123'),
            is_admin=True,
            is_approved=True
        )
        db.session.add(admin)
    # Create default homecoming settings
    settings = HomecomingSettings.query.first()
    if not settings:
        settings = HomecomingSettings(
            years_before=10,
            event_date='2026-12-15',
            venue='SLSU Main Campus, Sariaya, Quezon',
            deadline='2026-12-01',
            details='Join us for the Grand Alumni Homecoming!'
        )
        db.session.add(settings)
    db.session.commit()

# ============== HELPER FUNCTIONS ==============
def get_current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def get_homecoming_info(user):
    settings = HomecomingSettings.query.first()
    if not user or not settings:
        return None
    # Admin accounts do NOT get a homecoming schedule - only real alumni do
    if user.is_admin:
        return None
    homecoming_year = user.year_graduated + settings.years_before
    current_year = datetime.now().year
    years_away = homecoming_year - current_year
    status = 'upcoming'
    if years_away < 0:
        status = 'completed'
    elif years_away <= 1:
        status = 'near'
    return {
        'batch': user.year_graduated,
        'homecoming_year': homecoming_year,
        'years_away': years_away,
        'event_date': settings.event_date,
        'venue': settings.venue,
        'deadline': settings.deadline,
        'details': settings.details,
        'years_before': settings.years_before,
        'status': status
    }

def time_ago(dt):
    now = datetime.now()
    diff = now - dt
    if diff.days > 365:
        return f"{diff.days // 365}y ago"
    if diff.days > 30:
        return f"{diff.days // 30}mo ago"
    if diff.days > 0:
        return f"{diff.days}d ago"
    if diff.seconds > 3600:
        return f"{diff.seconds // 3600}h ago"
    if diff.seconds > 60:
        return f"{diff.seconds // 60}m ago"
    return "just now"

app.jinja_env.globals['time_ago'] = time_ago

# ============== CUSTOM RENDER FUNCTION (fixes template inheritance) ==============
def render_tpl(template_str, **context):
    if '{% extends "base" %}' in template_str:
        tpl = app.jinja_env.from_string(template_str)
        return tpl.render(**context)
    return render_template_string(template_str, **context)

# ============== HTML TEMPLATE ==============
BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLSU Alumni System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        * { scrollbar-width: thin; scrollbar-color: #4b5563 #1f2937; }
        *::-webkit-scrollbar { width: 6px; }
        *::-webkit-scrollbar-track { background: #1f2937; }
        *::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 3px; }
        .sidebar { transition: transform 0.3s ease; }
        .sidebar.hidden-mobile { transform: translateX(-100%); }
        @media (min-width: 1024px) { .sidebar.hidden-mobile { transform: translateX(0); } }
        .fade-in { animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .btn-primary { background: linear-gradient(135deg, #2563eb, #1d4ed8); }
        .btn-primary:hover { background: linear-gradient(135deg, #1d4ed8, #1e40af); }
        .card-hover { transition: all 0.2s ease; }
        .card-hover:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
        .modal { display: none; position: fixed; z-index: 100; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        .chat-container { height: 400px; overflow-y: auto; }
        .notification-dot { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">
    <!-- TOP BAR -->
    <nav class="bg-gray-800 border-b border-gray-700 fixed top-0 left-0 right-0 z-50">
        <div class="flex items-center justify-between px-4 py-3">
            <div class="flex items-center gap-3">
                <button onclick="toggleSidebar()" class="lg:hidden p-2 hover:bg-gray-700 rounded-lg">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
                    </svg>
                </button>
                <a href="{{ url_for('feed') }}" class="flex items-center gap-2">
                    <div class="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center font-bold">S</div>
                    <span class="font-bold text-lg hidden sm:block">SLSU Alumni</span>
                </a>
            </div>
            <form action="{{ url_for('directory') }}" method="GET" class="flex-1 max-w-md mx-4 hidden md:block">
                <input type="text" name="search" placeholder="Search alumni..." class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-blue-500">
            </form>
            <div class="flex items-center gap-2">
                {% if current_user %}
                <button onclick="openNotifications()" class="relative p-2 hover:bg-gray-700 rounded-lg">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
                    </svg>
                    {% set unread = current_user and current_user.id and Notification.query.filter_by(user_id=current_user.id, is_read=False).count() %}
                    {% if unread and unread > 0 %}
                    <span class="notification-dot absolute top-1 right-1 w-3 h-3 bg-red-500 rounded-full"></span>
                    {% endif %}
                </button>
                <button onclick="openMessages()" class="p-2 hover:bg-gray-700 rounded-lg relative">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
                    </svg>
                    {% set unread_msgs = current_user and current_user.id and Message.query.filter_by(receiver_id=current_user.id, is_read=False).count() %}
                    {% if unread_msgs and unread_msgs > 0 %}
                    <span class="notification-dot absolute top-1 right-1 w-3 h-3 bg-red-500 rounded-full"></span>
                    {% endif %}
                </button>
                <div class="flex items-center gap-2 ml-2 cursor-pointer" onclick="openProfile({{ current_user.id }})">
                    {% if current_user.profile_pic %}
                    <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-8 h-8 rounded-full object-cover">
                    {% else %}
                    <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-sm font-bold">{{ current_user.full_name[0] }}</div>
                    {% endif %}
                    <span class="hidden sm:block text-sm">{{ current_user.full_name.split()[0] }}</span>
                </div>
                {% endif %}
            </div>
        </div>
    </nav>
    <div class="flex pt-16">
        <!-- SIDEBAR -->
        <aside id="sidebar" class="sidebar hidden-mobile fixed lg:sticky top-16 left-0 h-[calc(100vh-4rem)] w-64 bg-gray-800 border-r border-gray-700 z-40 overflow-y-auto">
            <nav class="p-4 space-y-1">
                {% if current_user %}
                <a href="{{ url_for('feed') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'feed' %}bg-gray-700 text-blue-400{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/></svg>
                    News Feed
                </a>
                {% if not current_user.is_admin %}
                <a href="{{ url_for('my_homecoming') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'my_homecoming' %}bg-gray-700 text-blue-400{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                    My Homecoming
                </a>
                {% endif %}
                <a href="{{ url_for('profile', user_id=current_user.id) }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'profile' %}bg-gray-700 text-blue-400{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
                    My Profile
                </a>
                <a href="{{ url_for('directory') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'directory' %}bg-gray-700 text-blue-400{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/></svg>
                    Alumni Directory
                </a>
                <a href="{{ url_for('messages_page') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'messages_page' %}bg-gray-700 text-blue-400{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                    Messages
                </a>
                {% if current_user.is_admin %}
                <a href="{{ url_for('admin_panel') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-gray-700 {% if request.endpoint == 'admin_panel' %}bg-gray-700 text-yellow-400{% endif %} text-yellow-400">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                    Admin Panel
                </a>
                {% endif %}
                <div class="pt-4 mt-4 border-t border-gray-700">
                    <a href="{{ url_for('logout') }}" class="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-red-900/30 text-red-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
                        Logout
                    </a>
                </div>
                {% endif %}
            </nav>
        </aside>
        <!-- MAIN CONTENT -->
        <main class="flex-1 lg:ml-0 min-h-screen">
            {% block content %}{% endblock %}
        </main>
    </div>
    <!-- NOTIFICATIONS MODAL -->
    <div id="notifModal" class="modal">
        <div class="bg-gray-800 rounded-xl w-full max-w-md mx-4 max-h-[80vh] overflow-hidden">
            <div class="p-4 border-b border-gray-700 flex justify-between items-center">
                <h3 class="font-bold text-lg">Notifications</h3>
                <button onclick="closeModal('notifModal')" class="p-1 hover:bg-gray-700 rounded">&times;</button>
            </div>
            <div id="notifList" class="p-4 space-y-2 max-h-96 overflow-y-auto"></div>
        </div>
    </div>
    <!-- MESSAGES MODAL -->
    <div id="msgModal" class="modal">
        <div class="bg-gray-800 rounded-xl w-full max-w-lg mx-4 max-h-[85vh] overflow-hidden flex flex-col">
            <div class="p-4 border-b border-gray-700 flex justify-between items-center">
                <h3 class="font-bold text-lg">Messages</h3>
                <button onclick="closeModal('msgModal')" class="p-1 hover:bg-gray-700 rounded">&times;</button>
            </div>
            <div id="msgList" class="flex-1 overflow-y-auto p-4 space-y-2"></div>
        </div>
    </div>
    <!-- CHAT MODAL -->
    <div id="chatModal" class="modal">
        <div class="bg-gray-800 rounded-xl w-full max-w-lg mx-4 max-h-[85vh] overflow-hidden flex flex-col">
            <div class="p-4 border-b border-gray-700 flex justify-between items-center">
                <h3 id="chatTitle" class="font-bold text-lg">Chat</h3>
                <button onclick="closeModal('chatModal')" class="p-1 hover:bg-gray-700 rounded">&times;</button>
            </div>
            <div id="chatMessages" class="chat-container flex-1 overflow-y-auto p-4 space-y-3"></div>
            <div class="p-4 border-t border-gray-700 flex gap-2">
                <input type="text" id="chatInput" placeholder="Type a message..." class="flex-1 bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500" onkeypress="if(event.key==='Enter')sendChat()">
                <button onclick="sendChat()" class="btn-primary px-4 py-2 rounded-lg font-medium">Send</button>
            </div>
        </div>
    </div>
    <script>
        let currentChatUser = null;
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('hidden-mobile');
        }
        function openModal(id) {
            document.getElementById(id).classList.add('active');
        }
        function closeModal(id) {
            document.getElementById(id).classList.remove('active');
        }
        function openNotifications() {
            fetch('/api/notifications')
                .then(r => r.json())
                .then(data => {
                    const list = document.getElementById('notifList');
                    if (data.notifications.length === 0) {
                        list.innerHTML = '<p class="text-gray-400 text-center py-8">No notifications</p>';
                    } else {
                        list.innerHTML = data.notifications.map(n => `
                            <a href="${n.link}" class="block p-3 rounded-lg hover:bg-gray-700 ${n.is_read ? 'opacity-60' : 'bg-blue-900/20'}">
                                <p class="text-sm">${n.content}</p>
                                <p class="text-xs text-gray-400 mt-1">${n.time_ago}</p>
                            </a>
                        `).join('');
                    }
                    openModal('notifModal');
                    fetch('/api/notifications/read', { method: 'POST' });
                });
        }
        function openMessages() {
            fetch('/api/conversations')
                .then(r => r.json())
                .then(data => {
                    const list = document.getElementById('msgList');
                    if (data.conversations.length === 0) {
                        list.innerHTML = '<p class="text-gray-400 text-center py-8">No conversations yet</p>';
                    } else {
                        list.innerHTML = data.conversations.map(c => `
                            <div onclick="openChat(${c.user_id})" class="flex items-center gap-3 p-3 rounded-lg hover:bg-gray-700 cursor-pointer">
                                <div class="w-10 h-10 bg-blue-600 rounded-full flex items-center justify-center font-bold">${c.name[0]}</div>
                                <div class="flex-1 min-w-0">
                                    <p class="font-medium truncate">${c.name}</p>
                                    <p class="text-sm text-gray-400 truncate">${c.last_message}</p>
                                </div>
                                ${c.unread > 0 ? '<span class="bg-blue-600 text-xs px-2 py-1 rounded-full">' + c.unread + '</span>' : ''}
                            </div>
                        `).join('');
                    }
                    openModal('msgModal');
                });
        }
        function openChat(userId) {
            currentChatUser = userId;
            fetch(`/api/user/${userId}`)
                .then(r => r.json())
                .then(data => {
                    document.getElementById('chatTitle').textContent = data.name;
                    loadChatMessages();
                    closeModal('msgModal');
                    openModal('chatModal');
                });
        }
        function loadChatMessages() {
            if (!currentChatUser) return;
            fetch(`/api/messages/${currentChatUser}`)
                .then(r => r.json())
                .then(data => {
                    const container = document.getElementById('chatMessages');
                    if (data.messages.length === 0) {
                        container.innerHTML = '<p class="text-gray-400 text-center py-8">Start a conversation!</p>';
                    } else {
                        container.innerHTML = data.messages.map(m => `
                            <div class="flex ${m.is_me ? 'justify-end' : 'justify-start'}">
                                <div class="${m.is_me ? 'bg-blue-600' : 'bg-gray-700'} rounded-2xl px-4 py-2 max-w-xs">
                                    <p class="text-sm">${m.content}</p>
                                    <p class="text-xs ${m.is_me ? 'text-blue-200' : 'text-gray-400'} mt-1">${m.time_ago}</p>
                                </div>
                            </div>
                        `).join('');
                    }
                    container.scrollTop = container.scrollHeight;
                });
        }
        function sendChat() {
            const input = document.getElementById('chatInput');
            if (!input.value.trim() || !currentChatUser) return;
            fetch(`/api/messages/${currentChatUser}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: input.value })
            }).then(() => {
                input.value = '';
                loadChatMessages();
            });
        }
        function openProfile(userId) {
            window.location.href = `/profile/${userId}`;
        }
        // Close modals on outside click
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) modal.classList.remove('active');
            });
        });
    </script>
</body>
</html>
'''

# ============== ROUTES ==============
@app.route('/')
def index():
    user = get_current_user()
    if user:
        return redirect(url_for('feed'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        student_number = request.form.get('student_number', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(student_number=student_number).first()
        if user and check_password_hash(user.password, password):
            if not user.is_approved:
                return render_tpl(LOGIN_TEMPLATE, error='Your account is pending approval by admin.')
            session['user_id'] = user.id
            return redirect(url_for('feed'))
        return render_tpl(LOGIN_TEMPLATE, error='Invalid student number or password.')
    return render_tpl(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        student_number = request.form.get('student_number', '').strip()
        if User.query.filter_by(student_number=student_number).first():
            return render_tpl(REGISTER_TEMPLATE, error='Student number already registered.')

        profile_pic = ''
        if 'profile_pic' in request.files and request.files['profile_pic'].filename:
            file = request.files['profile_pic']
            profile_pic = base64.b64encode(file.read()).decode('utf-8')

        user = User(
            student_number=student_number,
            full_name=request.form.get('full_name', '').strip(),
            maiden_name=request.form.get('maiden_name', '').strip(),
            year_graduated=int(request.form.get('year_graduated', 2020)),
            course=request.form.get('course', '').strip(),
            current_job=request.form.get('current_job', '').strip(),
            location=request.form.get('location', '').strip(),
            profile_pic=profile_pic,
            password=generate_password_hash(request.form.get('password', ''))
        )
        db.session.add(user)
        db.session.commit()
        return render_tpl(LOGIN_TEMPLATE, success='Registration successful! Please wait for admin approval.')
    return render_tpl(REGISTER_TEMPLATE)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/feed')
def feed():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    posts = Post.query.order_by(Post.timestamp.desc()).all()
    homecoming = get_homecoming_info(user)
    return render_tpl(FEED_TEMPLATE, current_user=user, posts=posts, homecoming=homecoming, Notification=Notification, Message=Message, Like=Like, CommentLike=CommentLike)

@app.route('/my-homecoming')
def my_homecoming():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    # Admin has no homecoming page - redirect to feed
    if user.is_admin:
        return redirect(url_for('feed'))
    homecoming = get_homecoming_info(user)
    return render_tpl(HOMECOMING_TEMPLATE, current_user=user, homecoming=homecoming, Notification=Notification, Message=Message)

@app.route('/profile/<int:user_id>')
def profile(user_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('login'))
    profile_user = User.query.get_or_404(user_id)
    posts = Post.query.filter_by(user_id=user_id).order_by(Post.timestamp.desc()).all()
    homecoming = get_homecoming_info(profile_user)
    return render_tpl(PROFILE_TEMPLATE, current_user=current_user, profile_user=profile_user, posts=posts, homecoming=homecoming, Notification=Notification, Message=Message, Like=Like, CommentLike=CommentLike)

@app.route('/directory')
def directory():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    search = request.args.get('search', '')
    batch = request.args.get('batch', '')
    course = request.args.get('course', '')

    query = User.query.filter_by(is_approved=True).filter(User.is_admin == False)
    if search:
        query = query.filter(User.full_name.contains(search))
    if batch:
        query = query.filter_by(year_graduated=int(batch))
    if course:
        query = query.filter(User.course.contains(course))

    users = query.order_by(User.year_graduated.desc(), User.full_name).all()

    batches = db.session.query(User.year_graduated).filter_by(is_approved=True).distinct().order_by(User.year_graduated.desc()).all()
    courses = db.session.query(User.course).filter_by(is_approved=True).distinct().order_by(User.course).all()

    return render_tpl(DIRECTORY_TEMPLATE, current_user=user, users=users, batches=[b[0] for b in batches], courses=[c[0] for c in courses], Notification=Notification, Message=Message)

@app.route('/messages')
def messages_page():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_tpl(MESSAGES_TEMPLATE, current_user=user, Notification=Notification, Message=Message)

@app.route('/admin')
def admin_panel():
    user = get_current_user()
    if not user or not user.is_admin:
        return redirect(url_for('feed'))
    pending = User.query.filter_by(is_approved=False).filter(User.is_admin == False).all()
    all_users = User.query.filter(User.is_admin == False).order_by(User.created_at.desc()).all()
    settings = HomecomingSettings.query.first()
    posts = Post.query.order_by(Post.timestamp.desc()).limit(20).all()
    return render_tpl(ADMIN_TEMPLATE, current_user=user, pending=pending, all_users=all_users, settings=settings, posts=posts, Notification=Notification, Message=Message)

# ============== API ROUTES ==============
@app.route('/api/posts', methods=['POST'])
def create_post():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    content = request.form.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Content required'}), 400

    image = ''
    if 'image' in request.files and request.files['image'].filename:
        file = request.files['image']
        image = base64.b64encode(file.read()).decode('utf-8')

    post = Post(user_id=user.id, content=content, image=image)
    db.session.add(post)
    db.session.commit()
    return redirect(url_for('feed'))

@app.route('/api/posts/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    user = get_current_user()
    post = Post.query.get_or_404(post_id)
    if not user or (post.user_id != user.id and not user.is_admin):
        return jsonify({'error': 'Unauthorized'}), 401
    db.session.delete(post)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
def toggle_like(post_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    like = Like.query.filter_by(post_id=post_id, user_id=user.id).first()
    if like:
        db.session.delete(like)
        liked = False
    else:
        like = Like(post_id=post_id, user_id=user.id)
        db.session.add(like)
        liked = True
    db.session.commit()
    count = Like.query.filter_by(post_id=post_id).count()
    return jsonify({'liked': liked, 'count': count})

@app.route('/api/posts/<int:post_id>/comment', methods=['POST'])
def add_comment(post_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    content = request.json.get('content', '').strip()
    if not content:
        return jsonify({'error': 'Content required'}), 400
    parent_id = request.json.get('parent_id')
    if parent_id:
        parent_id = int(parent_id)
    comment = Comment(post_id=post_id, user_id=user.id, content=content, parent_id=parent_id)
    db.session.add(comment)
    db.session.flush()  # get comment.id
    # Notify post owner
    post = Post.query.get(post_id)
    if post and post.user_id != user.id:
        notif = Notification(user_id=post.user_id, content=f'{user.full_name} commented on your post', link=f'/feed#post-{post_id}')
        db.session.add(notif)
    # Notify parent comment owner if this is a reply
    if parent_id:
        parent = Comment.query.get(parent_id)
        if parent and parent.user_id != user.id and (not post or parent.user_id != post.user_id):
            notif2 = Notification(user_id=parent.user_id, content=f'{user.full_name} replied to your comment', link=f'/feed#post-{post_id}')
            db.session.add(notif2)
    db.session.commit()
    return jsonify({'success': True, 'comment': {
        'id': comment.id,
        'user': user.full_name,
        'content': content,
        'time_ago': 'just now',
        'pic': user.profile_pic
    }})

@app.route('/api/comments/<int:comment_id>/like', methods=['POST'])
def toggle_comment_like(comment_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    comment = Comment.query.get_or_404(comment_id)
    like = CommentLike.query.filter_by(comment_id=comment_id, user_id=user.id).first()
    if like:
        db.session.delete(like)
        liked = False
    else:
        like = CommentLike(comment_id=comment_id, user_id=user.id)
        db.session.add(like)
        liked = True
        # Notify comment owner
        if comment.user_id != user.id:
            notif = Notification(user_id=comment.user_id, content=f'{user.full_name} reacted to your comment', link=f'/feed#post-{comment.post_id}')
            db.session.add(notif)
    db.session.commit()
    count = CommentLike.query.filter_by(comment_id=comment_id).count()
    return jsonify({'liked': liked, 'count': count})

@app.route('/api/comments/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    user = get_current_user()
    comment = Comment.query.get_or_404(comment_id)
    if not user or (comment.user_id != user.id and not user.is_admin):
        return jsonify({'error': 'Unauthorized'}), 401
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/notifications')
def get_notifications():
    user = get_current_user()
    if not user:
        return jsonify({'notifications': []})
    notifs = Notification.query.filter_by(user_id=user.id).order_by(Notification.timestamp.desc()).limit(20).all()
    return jsonify({'notifications': [{
        'content': n.content, 'link': n.link, 'is_read': n.is_read,
        'time_ago': time_ago(n.timestamp)
    } for n in notifs]})

@app.route('/api/notifications/read', methods=['POST'])
def read_notifications():
    user = get_current_user()
    if user:
        Notification.query.filter_by(user_id=user.id, is_read=False).update({'is_read': True})
        db.session.commit()
    return jsonify({'success': True})

@app.route('/api/conversations')
def get_conversations():
    user = get_current_user()
    if not user:
        return jsonify({'conversations': []})
    sent = db.session.query(Message.receiver_id).filter_by(sender_id=user.id).distinct()
    received = db.session.query(Message.sender_id).filter_by(receiver_id=user.id).distinct()
    user_ids = set([u[0] for u in sent] + [u[0] for u in received])

    conversations = []
    for uid in user_ids:
        other = User.query.get(uid)
        if not other:
            continue
        last_msg = Message.query.filter(
            ((Message.sender_id == user.id) & (Message.receiver_id == uid)) |
            ((Message.sender_id == uid) & (Message.receiver_id == user.id))
        ).order_by(Message.timestamp.desc()).first()
        unread = Message.query.filter_by(sender_id=uid, receiver_id=user.id, is_read=False).count()
        conversations.append({
            'user_id': uid, 'name': other.full_name,
            'last_message': last_msg.content if last_msg else '',
            'unread': unread
        })
    return jsonify({'conversations': conversations})

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    u = User.query.get_or_404(user_id)
    return jsonify({'id': u.id, 'name': u.full_name})

@app.route('/api/messages/<int:other_id>', methods=['GET', 'POST'])
def api_messages(other_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    if request.method == 'POST':
        content = request.json.get('content', '').strip()
        if not content:
            return jsonify({'error': 'Content required'}), 400
        msg = Message(sender_id=user.id, receiver_id=other_id, content=content)
        db.session.add(msg)
        notif = Notification(user_id=other_id, content=f'{user.full_name} sent you a message', link='/messages')
        db.session.add(notif)
        db.session.commit()
        return jsonify({'success': True})

    Message.query.filter_by(sender_id=other_id, receiver_id=user.id, is_read=False).update({'is_read': True})
    db.session.commit()

    messages = Message.query.filter(
        ((Message.sender_id == user.id) & (Message.receiver_id == other_id)) |
        ((Message.sender_id == other_id) & (Message.receiver_id == user.id))
    ).order_by(Message.timestamp).all()

    return jsonify({'messages': [{
        'content': m.content, 'is_me': m.sender_id == user.id,
        'time_ago': time_ago(m.timestamp)
    } for m in messages]})

@app.route('/api/admin/approve/<int:user_id>', methods=['POST'])
def admin_approve(user_id):
    admin = get_current_user()
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    notif = Notification(user_id=user.id, content='Your account has been approved! Welcome to SLSU Alumni.', link='/feed')
    db.session.add(notif)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/reject/<int:user_id>', methods=['POST'])
def admin_reject(user_id):
    admin = get_current_user()
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/settings', methods=['POST'])
def admin_settings():
    admin = get_current_user()
    if not admin or not admin.is_admin:
        return jsonify({'error': 'Unauthorized'}), 401
    settings = HomecomingSettings.query.first()
    old_years = settings.years_before
    settings.years_before = int(request.form.get('years_before', 10))
    settings.event_date = request.form.get('event_date', '')
    settings.venue = request.form.get('venue', '')
    settings.deadline = request.form.get('deadline', '')
    settings.details = request.form.get('details', '')
    db.session.commit()

    if old_years != settings.years_before:
        alumni = User.query.filter_by(is_approved=True).filter(User.is_admin == False).all()
        for a in alumni:
            notif = Notification(user_id=a.id, content=f'Homecoming settings updated! Now {settings.years_before} years after graduation.', link='/my-homecoming')
            db.session.add(notif)
        db.session.commit()

    return redirect(url_for('admin_panel'))

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401
    user.current_job = request.form.get('current_job', user.current_job)
    user.location = request.form.get('location', user.location)
    user.course = request.form.get('course', user.course)
    if 'profile_pic' in request.files and request.files['profile_pic'].filename:
        file = request.files['profile_pic']
        user.profile_pic = base64.b64encode(file.read()).decode('utf-8')
    db.session.commit()
    return redirect(url_for('profile', user_id=user.id))

# ============== PAGE TEMPLATES ==============
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLSU Alumni - Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-md">
        <div class="text-center mb-8">
            <div class="w-20 h-20 bg-blue-600 rounded-2xl flex items-center justify-center text-4xl font-bold mx-auto mb-4">S</div>
            <h1 class="text-3xl font-bold text-white">SLSU Alumni System</h1>
            <p class="text-gray-400 mt-2">Connect with your fellow graduates</p>
        </div>
        <div class="bg-gray-800 rounded-2xl p-8 shadow-xl">
            {% if error %}
            <div class="bg-red-900/30 border border-red-700 text-red-300 px-4 py-3 rounded-lg mb-4">{{ error }}</div>
            {% endif %}
            {% if success %}
            <div class="bg-green-900/30 border border-green-700 text-green-300 px-4 py-3 rounded-lg mb-4">{{ success }}</div>
            {% endif %}
            <form method="POST">
                <div class="mb-4">
                    <label class="block text-gray-300 text-sm mb-2">Student Number</label>
                    <input type="text" name="student_number" required class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500" placeholder="e.g. 2020-12345">
                </div>
                <div class="mb-6">
                    <label class="block text-gray-300 text-sm mb-2">Password</label>
                    <input type="password" name="password" required class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500" placeholder="********">
                </div>
                <button type="submit" class="w-full btn-primary text-white py-3 rounded-lg font-medium hover:opacity-90 transition">Login</button>
            </form>
            <p class="text-center text-gray-400 mt-6">
                Don't have an account?
                <a href="{{ url_for('register') }}" class="text-blue-400 hover:underline">Register here</a>
            </p>
            <div class="mt-4 pt-4 border-t border-gray-700 text-xs text-gray-500 text-center">
                <p>Admin: ADMIN001 / admin123</p>
            </div>
        </div>
    </div>
    <style>.btn-primary{background:linear-gradient(135deg,#2563eb,#1d4ed8)}</style>
</body>
</html>
'''

REGISTER_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLSU Alumni - Register</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 min-h-screen py-8 px-4">
    <div class="w-full max-w-lg mx-auto">
        <div class="text-center mb-8">
            <div class="w-16 h-16 bg-blue-600 rounded-2xl flex items-center justify-center text-3xl font-bold mx-auto mb-4">S</div>
            <h1 class="text-2xl font-bold text-white">Register - SLSU Alumni</h1>
        </div>
        <div class="bg-gray-800 rounded-2xl p-8 shadow-xl">
            {% if error %}
            <div class="bg-red-900/30 border border-red-700 text-red-300 px-4 py-3 rounded-lg mb-4">{{ error }}</div>
            {% endif %}
            <form method="POST" enctype="multipart/form-data">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="md:col-span-2">
                        <label class="block text-gray-300 text-sm mb-2">Full Name *</label>
                        <input type="text" name="full_name" required class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Maiden Name (optional)</label>
                        <input type="text" name="maiden_name" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Student Number *</label>
                        <input type="text" name="student_number" required class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Year Graduated *</label>
                        <input type="number" name="year_graduated" required min="1950" max="2099" value="2020" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Course / Degree *</label>
                        <input type="text" name="course" required class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Current Job</label>
                        <input type="text" name="current_job" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-gray-300 text-sm mb-2">Location</label>
                        <input type="text" name="location" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-gray-300 text-sm mb-2">Profile Picture</label>
                        <input type="file" name="profile_pic" accept="image/*" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-gray-300 text-sm mb-2">Password *</label>
                        <input type="password" name="password" required minlength="6" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                    </div>
                </div>
                <button type="submit" class="w-full btn-primary text-white py-3 rounded-lg font-medium mt-6 hover:opacity-90 transition">Register</button>
            </form>
            <p class="text-center text-gray-400 mt-6">
                Already have an account?
                <a href="{{ url_for('login') }}" class="text-blue-400 hover:underline">Login here</a>
            </p>
        </div>
    </div>
    <style>.btn-primary{background:linear-gradient(135deg,#2563eb,#1d4ed8)}</style>
</body>
</html>
'''

# Shared post-card JS (used by both feed and profile)
POST_CARD_JS = '''
<script>
function toggleLike(postId, btn) {
    fetch(`/api/posts/${postId}/like`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            btn.classList.toggle('text-red-400', data.liked);
            btn.classList.toggle('text-gray-400', !data.liked);
            btn.querySelector('.like-count').textContent = data.count;
            btn.querySelector('svg').setAttribute('fill', data.liked ? 'currentColor' : 'none');
        });
}
function toggleComments(postId) {
    document.getElementById(`comments-${postId}`).classList.toggle('hidden');
}
function addComment(postId) {
    const input = document.getElementById(`comment-input-${postId}`);
    if (!input.value.trim()) return;
    fetch(`/api/posts/${postId}/comment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: input.value })
    }).then(() => location.reload());
}
function toggleReplyBox(commentId) {
    const box = document.getElementById('reply-box-' + commentId);
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) {
        document.getElementById('reply-input-' + commentId).focus();
    }
}
function addReply(postId, parentId) {
    const input = document.getElementById('reply-input-' + parentId);
    if (!input || !input.value.trim()) return;
    fetch(`/api/posts/${postId}/comment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: input.value, parent_id: parentId })
    }).then(() => location.reload());
}
function toggleCommentLike(commentId, btn) {
    fetch(`/api/comments/${commentId}/like`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            btn.classList.toggle('text-blue-400', data.liked);
            btn.classList.toggle('text-gray-400', !data.liked);
            const span = btn.querySelector('.clike-count');
            if (span) span.textContent = '(' + data.count + ')';
        });
}
function deletePost(postId) {
    if (confirm('Delete this post?')) {
        fetch(`/api/posts/${postId}/delete`, { method: 'POST' })
            .then(() => location.reload());
    }
}
function deleteComment(commentId) {
    if (confirm('Delete this comment?')) {
        fetch(`/api/comments/${commentId}/delete`, { method: 'POST' })
            .then(() => location.reload());
    }
}
</script>
'''

FEED_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-2xl mx-auto p-4 lg:p-6">
    <!-- HOMECOMING BANNER -->
    {% if homecoming %}
    <div class="mb-6 fade-in">
        {% if homecoming.status == 'near' %}
        <div class="bg-gradient-to-r from-yellow-600 to-orange-600 rounded-2xl p-6 shadow-xl">
        {% elif homecoming.status == 'completed' %}
        <div class="bg-gradient-to-r from-gray-600 to-gray-700 rounded-2xl p-6 shadow-xl">
        {% else %}
        <div class="bg-gradient-to-r from-blue-600 to-purple-600 rounded-2xl p-6 shadow-xl">
        {% endif %}
            <div class="flex items-start justify-between">
                <div class="w-full">
                    <div class="flex items-center gap-2 mb-2">
                        <h2 class="text-xl font-bold">HOMECOMING: Batch {{ homecoming.batch }}</h2>
                    </div>
                    <p class="text-blue-100 mb-3">
                        {% if homecoming.status == 'near' %}
                        <span class="font-bold text-yellow-200">Coming soon!</span> Only {{ homecoming.years_away }} year(s) to go!
                        {% elif homecoming.status == 'completed' %}
                        <span class="font-bold">Completed!</span> Your homecoming year was {{ homecoming.homecoming_year }}
                        {% else %}
                        <span class="font-bold">Mark your calendar!</span> {{ homecoming.years_away }} years to go
                        {% endif %}
                    </p>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        <div class="bg-white/10 rounded-lg p-3">
                            <p class="text-blue-100 text-xs">Homecoming Year</p>
                            <p class="font-bold text-lg">{{ homecoming.homecoming_year }}</p>
                        </div>
                        <div class="bg-white/10 rounded-lg p-3">
                            <p class="text-blue-100 text-xs">Years After Grad</p>
                            <p class="font-bold text-lg">{{ homecoming.years_before }} yrs</p>
                        </div>
                        {% if homecoming.event_date %}
                        <div class="bg-white/10 rounded-lg p-3">
                            <p class="text-blue-100 text-xs">Event Date</p>
                            <p class="font-bold">{{ homecoming.event_date }}</p>
                        </div>
                        {% endif %}
                        {% if homecoming.venue %}
                        <div class="bg-white/10 rounded-lg p-3">
                            <p class="text-blue-100 text-xs">Venue</p>
                            <p class="font-bold text-sm truncate">{{ homecoming.venue }}</p>
                        </div>
                        {% endif %}
                    </div>
                    {% if homecoming.details %}
                    <p class="mt-3 text-sm text-blue-100">{{ homecoming.details }}</p>
                    {% endif %}
                    {% if homecoming.deadline %}
                    <p class="mt-2 text-xs text-blue-200">Registration deadline: {{ homecoming.deadline }}</p>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
    {% endif %}
    <!-- CREATE POST -->
    <div class="bg-gray-800 rounded-2xl p-4 mb-6 shadow-lg">
        <form method="POST" action="/api/posts" enctype="multipart/form-data" class="space-y-3">
            <div class="flex gap-3">
                {% if current_user.profile_pic %}
                <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-10 h-10 rounded-full object-cover flex-shrink-0">
                {% else %}
                <div class="w-10 h-10 bg-blue-600 rounded-full flex items-center justify-center font-bold flex-shrink-0">{{ current_user.full_name[0] }}</div>
                {% endif %}
                <textarea name="content" required rows="2" placeholder="What's on your mind, {{ current_user.full_name.split()[0] }}?" class="flex-1 bg-gray-700 border border-gray-600 rounded-xl px-4 py-3 resize-none focus:outline-none focus:border-blue-500"></textarea>
            </div>
            <div class="flex items-center justify-between pt-2 border-t border-gray-700">
                <label class="flex items-center gap-2 text-gray-400 hover:text-blue-400 cursor-pointer">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                    <span class="text-sm">Photo</span>
                    <input type="file" name="image" accept="image/*" class="hidden">
                </label>
                <button type="submit" class="btn-primary px-6 py-2 rounded-lg font-medium text-sm">Post</button>
            </div>
        </form>
    </div>
    <!-- POSTS -->
    <div class="space-y-6">
        {% for post in posts %}
        <div id="post-{{ post.id }}" class="bg-gray-800 rounded-2xl shadow-lg card-hover fade-in">
            <div class="p-4">
                <div class="flex items-start justify-between mb-3">
                    <div class="flex items-center gap-3 cursor-pointer" onclick="openProfile({{ post.user.id }})">
                        {% if post.user.profile_pic %}
                        <img src="data:image/png;base64,{{ post.user.profile_pic }}" class="w-10 h-10 rounded-full object-cover">
                        {% else %}
                        <div class="w-10 h-10 bg-blue-600 rounded-full flex items-center justify-center font-bold">{{ post.user.full_name[0] }}</div>
                        {% endif %}
                        <div>
                            <p class="font-medium hover:text-blue-400">{{ post.user.full_name }}</p>
                            <p class="text-xs text-gray-400">Batch {{ post.user.year_graduated }} - {{ time_ago(post.timestamp) }}</p>
                        </div>
                    </div>
                    {% if post.user_id == current_user.id or current_user.is_admin %}
                    <button onclick="deletePost({{ post.id }})" class="text-gray-400 hover:text-red-400 p-1">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                    {% endif %}
                </div>
                <p class="text-gray-100 mb-3 whitespace-pre-wrap">{{ post.content }}</p>
                {% if post.image %}
                <img src="data:image/png;base64,{{ post.image }}" class="rounded-xl max-h-96 w-full object-cover mb-3">
                {% endif %}
                <div class="flex items-center gap-4 pt-3 border-t border-gray-700 text-sm">
                    {% set liked = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first() %}
                    {% set like_count = Like.query.filter_by(post_id=post.id).count() %}
                    <button onclick="toggleLike({{ post.id }}, this)" class="flex items-center gap-2 {{ 'text-red-400' if liked else 'text-gray-400' }} hover:text-red-400">
                        <svg class="w-5 h-5" fill="{{ 'currentColor' if liked else 'none' }}" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"/></svg>
                        <span class="like-count">{{ like_count }}</span>
                    </button>
                    {% set top_comment_count = post.comments|selectattr('parent_id', 'none')|list|length %}
                    <button onclick="toggleComments({{ post.id }})" class="flex items-center gap-2 text-gray-400 hover:text-blue-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                        <span>{{ top_comment_count }} Comments</span>
                    </button>
                    <button onclick="openChat({{ post.user.id }})" class="flex items-center gap-2 text-gray-400 hover:text-green-400 ml-auto">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                        <span class="hidden sm:inline">Message</span>
                    </button>
                </div>
                <!-- COMMENTS SECTION (with replies + reactions) -->
                <div id="comments-{{ post.id }}" class="hidden mt-4 pt-4 border-t border-gray-700 space-y-3">
                    {% for comment in post.comments if comment.parent_id is none %}
                    <div class="flex gap-3">
                        {% if comment.user.profile_pic %}
                        <img src="data:image/png;base64,{{ comment.user.profile_pic }}" class="w-8 h-8 rounded-full object-cover flex-shrink-0 cursor-pointer" onclick="openProfile({{ comment.user.id }})">
                        {% else %}
                        <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 cursor-pointer" onclick="openProfile({{ comment.user.id }})">{{ comment.user.full_name[0] }}</div>
                        {% endif %}
                        <div class="flex-1 min-w-0">
                            <div class="bg-gray-700 rounded-2xl rounded-tl-sm px-3 py-2 inline-block max-w-full">
                                <div class="flex items-center justify-between gap-3">
                                    <p class="text-sm font-semibold hover:text-blue-400 cursor-pointer" onclick="openProfile({{ comment.user.id }})">{{ comment.user.full_name }}</p>
                                    {% if comment.user_id == current_user.id or current_user.is_admin %}
                                    <button onclick="deleteComment({{ comment.id }})" class="text-gray-400 hover:text-red-400 text-xs flex-shrink-0">&times;</button>
                                    {% endif %}
                                </div>
                                <p class="text-sm text-gray-100 break-words">{{ comment.content }}</p>
                            </div>
                            <div class="flex items-center gap-4 mt-1 px-1 text-xs">
                                <span class="text-gray-500">{{ time_ago(comment.timestamp) }}</span>
                                {% set cliked = CommentLike.query.filter_by(comment_id=comment.id, user_id=current_user.id).first() %}
                                {% set clike_count = CommentLike.query.filter_by(comment_id=comment.id).count() %}
                                <button onclick="toggleCommentLike({{ comment.id }}, this)" class="font-semibold {{ 'text-blue-400' if cliked else 'text-gray-400' }} hover:text-blue-400">
                                    Like <span class="clike-count font-normal">({{ clike_count }})</span>
                                </button>
                                <button onclick="toggleReplyBox({{ comment.id }})" class="font-semibold text-gray-400 hover:text-blue-400">Reply</button>
                            </div>
                            <!-- NESTED REPLIES -->
                            {% if comment.replies %}
                            <div class="mt-2 ml-2 space-y-2 border-l-2 border-gray-600 pl-3">
                                {% for reply in comment.replies %}
                                <div class="flex gap-2">
                                    {% if reply.user.profile_pic %}
                                    <img src="data:image/png;base64,{{ reply.user.profile_pic }}" class="w-6 h-6 rounded-full object-cover flex-shrink-0 cursor-pointer" onclick="openProfile({{ reply.user.id }})">
                                    {% else %}
                                    <div class="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 cursor-pointer" onclick="openProfile({{ reply.user.id }})">{{ reply.user.full_name[0] }}</div>
                                    {% endif %}
                                    <div class="flex-1 min-w-0">
                                        <div class="bg-gray-700/70 rounded-2xl rounded-tl-sm px-3 py-1.5 inline-block max-w-full">
                                            <div class="flex items-center justify-between gap-3">
                                                <p class="text-xs font-semibold hover:text-blue-400 cursor-pointer" onclick="openProfile({{ reply.user.id }})">{{ reply.user.full_name }}</p>
                                                {% if reply.user_id == current_user.id or current_user.is_admin %}
                                                <button onclick="deleteComment({{ reply.id }})" class="text-gray-400 hover:text-red-400 text-xs flex-shrink-0">&times;</button>
                                                {% endif %}
                                            </div>
                                            <p class="text-sm text-gray-100 break-words">{{ reply.content }}</p>
                                        </div>
                                        <div class="flex items-center gap-3 mt-1 px-1 text-xs">
                                            <span class="text-gray-500">{{ time_ago(reply.timestamp) }}</span>
                                            {% set rliked = CommentLike.query.filter_by(comment_id=reply.id, user_id=current_user.id).first() %}
                                            {% set rlike_count = CommentLike.query.filter_by(comment_id=reply.id).count() %}
                                            <button onclick="toggleCommentLike({{ reply.id }}, this)" class="font-semibold {{ 'text-blue-400' if rliked else 'text-gray-400' }} hover:text-blue-400">
                                                Like <span class="clike-count font-normal">({{ rlike_count }})</span>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                            {% endif %}
                            <!-- REPLY INPUT -->
                            <div id="reply-box-{{ comment.id }}" class="hidden mt-2 flex gap-2 items-center">
                                {% if current_user.profile_pic %}
                                <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-6 h-6 rounded-full object-cover flex-shrink-0">
                                {% else %}
                                <div class="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">{{ current_user.full_name[0] }}</div>
                                {% endif %}
                                <input type="text" id="reply-input-{{ comment.id }}" placeholder="Write a reply..." class="flex-1 bg-gray-700 border border-gray-600 rounded-full px-4 py-1.5 text-sm focus:outline-none focus:border-blue-500" onkeypress="if(event.key==='Enter')addReply({{ post.id }}, {{ comment.id }})">
                                <button onclick="addReply({{ post.id }}, {{ comment.id }})" class="text-blue-400 text-sm font-semibold flex-shrink-0">Reply</button>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                    <!-- NEW COMMENT INPUT -->
                    <div class="flex gap-3 pt-1">
                        {% if current_user.profile_pic %}
                        <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-8 h-8 rounded-full object-cover flex-shrink-0">
                        {% else %}
                        <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0">{{ current_user.full_name[0] }}</div>
                        {% endif %}
                        <input type="text" id="comment-input-{{ post.id }}" placeholder="Write a comment..." class="flex-1 bg-gray-700 border border-gray-600 rounded-full px-4 py-2 text-sm focus:outline-none focus:border-blue-500" onkeypress="if(event.key==='Enter')addComment({{ post.id }})">
                    </div>
                </div>
            </div>
        </div>
        {% endfor %}
        {% if not posts %}
        <div class="text-center py-16 text-gray-400">
            <svg class="w-16 h-16 mx-auto mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/></svg>
            <p>No posts yet. Be the first to share something!</p>
        </div>
        {% endif %}
    </div>
</div>
''' + POST_CARD_JS + '''
{% endblock %}
'''

HOMECOMING_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-3xl mx-auto p-4 lg:p-6">
    <h1 class="text-2xl font-bold mb-6">My Homecoming</h1>

    {% if homecoming %}
    <div class="fade-in">
        {% if homecoming.status == 'near' %}
        <div class="bg-gradient-to-br from-yellow-500 to-orange-600 rounded-3xl p-8 shadow-2xl">
        {% elif homecoming.status == 'completed' %}
        <div class="bg-gradient-to-br from-gray-600 to-gray-800 rounded-3xl p-8 shadow-2xl">
        {% else %}
        <div class="bg-gradient-to-br from-blue-600 to-purple-700 rounded-3xl p-8 shadow-2xl">
        {% endif %}
            <div class="text-center text-white">
                <h2 class="text-3xl font-bold mb-2">Batch {{ homecoming.batch }}</h2>
                <p class="text-blue-100 mb-6">SLSU Alumni Homecoming</p>

                <div class="bg-white/20 backdrop-blur rounded-2xl p-6 mb-6">
                    <p class="text-sm text-blue-100 mb-1">Your Homecoming Year</p>
                    <p class="text-5xl font-bold">{{ homecoming.homecoming_year }}</p>
                </div>

                <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                    <div class="bg-white/10 rounded-xl p-4">
                        <p class="text-xs text-blue-100">Years After Grad</p>
                        <p class="text-2xl font-bold">{{ homecoming.years_before }}</p>
                    </div>
                    <div class="bg-white/10 rounded-xl p-4">
                        <p class="text-xs text-blue-100">Status</p>
                        <p class="text-lg font-bold">
                            {% if homecoming.status == 'near' %}Coming Soon!
                            {% elif homecoming.status == 'completed' %}Completed
                            {% else %}Upcoming{% endif %}
                        </p>
                    </div>
                    {% if homecoming.event_date %}
                    <div class="bg-white/10 rounded-xl p-4">
                        <p class="text-xs text-blue-100">Event Date</p>
                        <p class="text-lg font-bold">{{ homecoming.event_date }}</p>
                    </div>
                    {% endif %}
                    {% if homecoming.years_away > 0 and homecoming.status != 'completed' %}
                    <div class="bg-white/10 rounded-xl p-4">
                        <p class="text-xs text-blue-100">Years to Go</p>
                        <p class="text-2xl font-bold">{{ homecoming.years_away }}</p>
                    </div>
                    {% endif %}
                </div>

                {% if homecoming.venue %}
                <div class="bg-white/10 rounded-xl p-4 mb-4 text-left">
                    <p class="text-xs text-blue-100">Venue</p>
                    <p class="font-bold">{{ homecoming.venue }}</p>
                </div>
                {% endif %}

                {% if homecoming.details %}
                <div class="bg-white/10 rounded-xl p-4 mb-4 text-left">
                    <p class="text-xs text-blue-100">Details</p>
                    <p>{{ homecoming.details }}</p>
                </div>
                {% endif %}

                {% if homecoming.deadline %}
                <p class="text-sm text-blue-100">Registration deadline: <strong>{{ homecoming.deadline }}</strong></p>
                {% endif %}
            </div>
        </div>

        <div class="mt-6 bg-gray-800 rounded-2xl p-6">
            <h3 class="font-bold text-lg mb-3">How it works</h3>
            <ul class="space-y-2 text-gray-300 text-sm">
                <li>- Homecoming is automatically calculated as <strong>Year Graduated + {{ homecoming.years_before }} years</strong></li>
                <li>- Admin can change the number of years anytime - your schedule updates automatically</li>
                <li>- You'll receive notifications when your homecoming is near or when details change</li>
                <li>- Each batch has its own homecoming schedule based on graduation year</li>
            </ul>
        </div>
    </div>
    {% endif %}
</div>
{% endblock %}
'''

PROFILE_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-3xl mx-auto p-4 lg:p-6">
    <!-- PROFILE HEADER -->
    <div class="bg-gray-800 rounded-2xl overflow-hidden shadow-lg mb-6">
        <div class="h-32 bg-gradient-to-r from-blue-600 to-purple-600"></div>
        <div class="px-6 pb-6 -mt-12">
            <div class="flex items-end justify-between mb-4 flex-wrap gap-3">
                {% if profile_user.profile_pic %}
                <img src="data:image/png;base64,{{ profile_user.profile_pic }}" class="w-24 h-24 rounded-2xl border-4 border-gray-800 object-cover">
                {% else %}
                <div class="w-24 h-24 rounded-2xl border-4 border-gray-800 bg-blue-600 flex items-center justify-center text-4xl font-bold">{{ profile_user.full_name[0] }}</div>
                {% endif %}
                {% if profile_user.id == current_user.id %}
                <button onclick="document.getElementById('editProfile').classList.toggle('hidden')" class="btn-primary px-4 py-2 rounded-lg text-sm font-medium">Edit Profile</button>
                {% elif current_user.is_approved %}
                <button onclick="openChat({{ profile_user.id }})" class="btn-primary px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                    Message
                </button>
                {% endif %}
            </div>
            <h1 class="text-2xl font-bold">{{ profile_user.full_name }}</h1>
            {% if profile_user.maiden_name %}
            <p class="text-gray-400 text-sm">({{ profile_user.maiden_name }})</p>
            {% endif %}
            <div class="flex flex-wrap gap-4 mt-3 text-sm text-gray-300">
                <span>Batch {{ profile_user.year_graduated }}</span>
                <span>{{ profile_user.course }}</span>
                {% if profile_user.current_job %}<span>{{ profile_user.current_job }}</span>{% endif %}
                {% if profile_user.location %}<span>{{ profile_user.location }}</span>{% endif %}
            </div>
            {% if homecoming %}
            <div class="mt-4 bg-blue-900/30 border border-blue-700 rounded-xl p-4">
                <p class="text-sm text-blue-300">Homecoming Year: <strong>{{ homecoming.homecoming_year }}</strong>
                {% if homecoming.status == 'near' %}<span class="text-yellow-400 ml-2">Coming Soon!</span>
                {% elif homecoming.status == 'completed' %}<span class="text-gray-400 ml-2">Completed</span>
                {% else %}<span class="ml-2">({{ homecoming.years_away }} years to go)</span>{% endif %}
                </p>
            </div>
            {% endif %}

            <!-- EDIT FORM -->
            {% if profile_user.id == current_user.id %}
            <div id="editProfile" class="hidden mt-6 pt-6 border-t border-gray-700">
                <form method="POST" action="/api/profile/update" enctype="multipart/form-data" class="space-y-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">Course</label>
                            <input type="text" name="course" value="{{ profile_user.course }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">Current Job</label>
                            <input type="text" name="current_job" value="{{ profile_user.current_job }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">Location</label>
                            <input type="text" name="location" value="{{ profile_user.location }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">Profile Picture</label>
                            <input type="file" name="profile_pic" accept="image/*" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 text-sm file:mr-2 file:py-1 file:px-3 file:rounded file:border-0 file:bg-blue-600 file:text-white">
                        </div>
                    </div>
                    <button type="submit" class="btn-primary px-6 py-2 rounded-lg font-medium">Save Changes</button>
                </form>
            </div>
            {% endif %}
        </div>
    </div>

    <!-- USER'S POSTS (INTERACTIVE, FB-style) -->
    <h2 class="text-xl font-bold mb-4">Posts by {{ profile_user.full_name.split()[0] }} ({{ posts|length }})</h2>
    <div class="space-y-6">
        {% for post in posts %}
        <div id="post-{{ post.id }}" class="bg-gray-800 rounded-2xl shadow-lg card-hover fade-in">
            <div class="p-4">
                <div class="flex items-start justify-between mb-3">
                    <div class="flex items-center gap-3">
                        {% if post.user.profile_pic %}
                        <img src="data:image/png;base64,{{ post.user.profile_pic }}" class="w-10 h-10 rounded-full object-cover">
                        {% else %}
                        <div class="w-10 h-10 bg-blue-600 rounded-full flex items-center justify-center font-bold">{{ post.user.full_name[0] }}</div>
                        {% endif %}
                        <div>
                            <p class="font-medium">{{ post.user.full_name }}</p>
                            <p class="text-xs text-gray-400">Batch {{ post.user.year_graduated }} - {{ time_ago(post.timestamp) }}</p>
                        </div>
                    </div>
                    {% if post.user_id == current_user.id or current_user.is_admin %}
                    <button onclick="deletePost({{ post.id }})" class="text-gray-400 hover:text-red-400 p-1">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                    {% endif %}
                </div>
                <p class="text-gray-100 mb-3 whitespace-pre-wrap">{{ post.content }}</p>
                {% if post.image %}
                <img src="data:image/png;base64,{{ post.image }}" class="rounded-xl max-h-96 w-full object-cover mb-3">
                {% endif %}
                <div class="flex items-center gap-4 pt-3 border-t border-gray-700 text-sm">
                    {% set liked = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first() %}
                    {% set like_count = Like.query.filter_by(post_id=post.id).count() %}
                    <button onclick="toggleLike({{ post.id }}, this)" class="flex items-center gap-2 {{ 'text-red-400' if liked else 'text-gray-400' }} hover:text-red-400">
                        <svg class="w-5 h-5" fill="{{ 'currentColor' if liked else 'none' }}" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"/></svg>
                        <span class="like-count">{{ like_count }}</span>
                    </button>
                    {% set top_comment_count = post.comments|selectattr('parent_id', 'none')|list|length %}
                    <button onclick="toggleComments({{ post.id }})" class="flex items-center gap-2 text-gray-400 hover:text-blue-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                        <span>{{ top_comment_count }} Comments</span>
                    </button>
                    {% if post.user_id != current_user.id %}
                    <button onclick="openChat({{ post.user.id }})" class="flex items-center gap-2 text-gray-400 hover:text-green-400 ml-auto">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                        <span class="hidden sm:inline">Message</span>
                    </button>
                    {% endif %}
                </div>
                <!-- COMMENTS SECTION (with replies + reactions) -->
                <div id="comments-{{ post.id }}" class="hidden mt-4 pt-4 border-t border-gray-700 space-y-3">
                    {% for comment in post.comments if comment.parent_id is none %}
                    <div class="flex gap-3">
                        {% if comment.user.profile_pic %}
                        <img src="data:image/png;base64,{{ comment.user.profile_pic }}" class="w-8 h-8 rounded-full object-cover flex-shrink-0 cursor-pointer" onclick="openProfile({{ comment.user.id }})">
                        {% else %}
                        <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 cursor-pointer" onclick="openProfile({{ comment.user.id }})">{{ comment.user.full_name[0] }}</div>
                        {% endif %}
                        <div class="flex-1 min-w-0">
                            <div class="bg-gray-700 rounded-2xl rounded-tl-sm px-3 py-2 inline-block max-w-full">
                                <div class="flex items-center justify-between gap-3">
                                    <p class="text-sm font-semibold hover:text-blue-400 cursor-pointer" onclick="openProfile({{ comment.user.id }})">{{ comment.user.full_name }}</p>
                                    {% if comment.user_id == current_user.id or current_user.is_admin %}
                                    <button onclick="deleteComment({{ comment.id }})" class="text-gray-400 hover:text-red-400 text-xs flex-shrink-0">&times;</button>
                                    {% endif %}
                                </div>
                                <p class="text-sm text-gray-100 break-words">{{ comment.content }}</p>
                            </div>
                            <div class="flex items-center gap-4 mt-1 px-1 text-xs">
                                <span class="text-gray-500">{{ time_ago(comment.timestamp) }}</span>
                                {% set cliked = CommentLike.query.filter_by(comment_id=comment.id, user_id=current_user.id).first() %}
                                {% set clike_count = CommentLike.query.filter_by(comment_id=comment.id).count() %}
                                <button onclick="toggleCommentLike({{ comment.id }}, this)" class="font-semibold {{ 'text-blue-400' if cliked else 'text-gray-400' }} hover:text-blue-400">
                                    Like <span class="clike-count font-normal">({{ clike_count }})</span>
                                </button>
                                <button onclick="toggleReplyBox({{ comment.id }})" class="font-semibold text-gray-400 hover:text-blue-400">Reply</button>
                            </div>
                            {% if comment.replies %}
                            <div class="mt-2 ml-2 space-y-2 border-l-2 border-gray-600 pl-3">
                                {% for reply in comment.replies %}
                                <div class="flex gap-2">
                                    {% if reply.user.profile_pic %}
                                    <img src="data:image/png;base64,{{ reply.user.profile_pic }}" class="w-6 h-6 rounded-full object-cover flex-shrink-0 cursor-pointer" onclick="openProfile({{ reply.user.id }})">
                                    {% else %}
                                    <div class="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 cursor-pointer" onclick="openProfile({{ reply.user.id }})">{{ reply.user.full_name[0] }}</div>
                                    {% endif %}
                                    <div class="flex-1 min-w-0">
                                        <div class="bg-gray-700/70 rounded-2xl rounded-tl-sm px-3 py-1.5 inline-block max-w-full">
                                            <div class="flex items-center justify-between gap-3">
                                                <p class="text-xs font-semibold hover:text-blue-400 cursor-pointer" onclick="openProfile({{ reply.user.id }})">{{ reply.user.full_name }}</p>
                                                {% if reply.user_id == current_user.id or current_user.is_admin %}
                                                <button onclick="deleteComment({{ reply.id }})" class="text-gray-400 hover:text-red-400 text-xs flex-shrink-0">&times;</button>
                                                {% endif %}
                                            </div>
                                            <p class="text-sm text-gray-100 break-words">{{ reply.content }}</p>
                                        </div>
                                        <div class="flex items-center gap-3 mt-1 px-1 text-xs">
                                            <span class="text-gray-500">{{ time_ago(reply.timestamp) }}</span>
                                            {% set rliked = CommentLike.query.filter_by(comment_id=reply.id, user_id=current_user.id).first() %}
                                            {% set rlike_count = CommentLike.query.filter_by(comment_id=reply.id).count() %}
                                            <button onclick="toggleCommentLike({{ reply.id }}, this)" class="font-semibold {{ 'text-blue-400' if rliked else 'text-gray-400' }} hover:text-blue-400">
                                                Like <span class="clike-count font-normal">({{ rlike_count }})</span>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                            {% endif %}
                            <div id="reply-box-{{ comment.id }}" class="hidden mt-2 flex gap-2 items-center">
                                {% if current_user.profile_pic %}
                                <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-6 h-6 rounded-full object-cover flex-shrink-0">
                                {% else %}
                                <div class="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0">{{ current_user.full_name[0] }}</div>
                                {% endif %}
                                <input type="text" id="reply-input-{{ comment.id }}" placeholder="Write a reply..." class="flex-1 bg-gray-700 border border-gray-600 rounded-full px-4 py-1.5 text-sm focus:outline-none focus:border-blue-500" onkeypress="if(event.key==='Enter')addReply({{ post.id }}, {{ comment.id }})">
                                <button onclick="addReply({{ post.id }}, {{ comment.id }})" class="text-blue-400 text-sm font-semibold flex-shrink-0">Reply</button>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                    <div class="flex gap-3 pt-1">
                        {% if current_user.profile_pic %}
                        <img src="data:image/png;base64,{{ current_user.profile_pic }}" class="w-8 h-8 rounded-full object-cover flex-shrink-0">
                        {% else %}
                        <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0">{{ current_user.full_name[0] }}</div>
                        {% endif %}
                        <input type="text" id="comment-input-{{ post.id }}" placeholder="Write a comment..." class="flex-1 bg-gray-700 border border-gray-600 rounded-full px-4 py-2 text-sm focus:outline-none focus:border-blue-500" onkeypress="if(event.key==='Enter')addComment({{ post.id }})">
                    </div>
                </div>
            </div>
        </div>
        {% endfor %}
        {% if not posts %}
        <div class="text-center py-12 text-gray-400 bg-gray-800 rounded-2xl">
            <p>No posts yet.</p>
        </div>
        {% endif %}
    </div>
</div>
''' + POST_CARD_JS + '''
{% endblock %}
'''

DIRECTORY_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-5xl mx-auto p-4 lg:p-6">
    <h1 class="text-2xl font-bold mb-6">Alumni Directory</h1>

    <!-- FILTERS -->
    <form method="GET" class="bg-gray-800 rounded-2xl p-4 mb-6 flex flex-wrap gap-3">
        <input type="text" name="search" placeholder="Search by name..." value="{{ request.args.get('search', '') }}" class="flex-1 min-w-48 bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
        <select name="batch" class="bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
            <option value="">All Batches</option>
            {% for b in batches %}
            <option value="{{ b }}" {% if request.args.get('batch') == b|string %}selected{% endif %}>Batch {{ b }}</option>
            {% endfor %}
        </select>
        <select name="course" class="bg-gray-700 border border-gray-600 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500">
            <option value="">All Courses</option>
            {% for c in courses %}
            <option value="{{ c }}" {% if request.args.get('course') == c %}selected{% endif %}>{{ c }}</option>
            {% endfor %}
        </select>
        <button type="submit" class="btn-primary px-6 py-2 rounded-lg font-medium">Search</button>
    </form>
    <!-- RESULTS -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {% for u in users %}
        <div onclick="openProfile({{ u.id }})" class="bg-gray-800 rounded-2xl p-4 cursor-pointer card-hover">
            <div class="flex items-center gap-3 mb-3">
                {% if u.profile_pic %}
                <img src="data:image/png;base64,{{ u.profile_pic }}" class="w-12 h-12 rounded-xl object-cover">
                {% else %}
                <div class="w-12 h-12 rounded-xl bg-blue-600 flex items-center justify-center text-xl font-bold">{{ u.full_name[0] }}</div>
                {% endif %}
                <div class="min-w-0">
                    <p class="font-medium truncate">{{ u.full_name }}</p>
                    <p class="text-xs text-gray-400">Batch {{ u.year_graduated }}</p>
                </div>
            </div>
            <p class="text-sm text-gray-300 truncate">{{ u.course }}</p>
            {% if u.current_job %}
            <p class="text-sm text-gray-400 truncate mt-1">{{ u.current_job }}</p>
            {% endif %}
            {% if u.location %}
            <p class="text-sm text-gray-400 truncate">{{ u.location }}</p>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    {% if not users %}
    <div class="text-center py-16 text-gray-400 bg-gray-800 rounded-2xl">
        <p>No alumni found matching your criteria.</p>
    </div>
    {% endif %}
</div>
{% endblock %}
'''

MESSAGES_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-4xl mx-auto p-4 lg:p-6">
    <h1 class="text-2xl font-bold mb-6">Messages</h1>
    <div id="conversationsList" class="space-y-2">
        <p class="text-gray-400 text-center py-16">Loading conversations...</p>
    </div>
</div>
<script>
fetch('/api/conversations')
    .then(r => r.json())
    .then(data => {
        const list = document.getElementById('conversationsList');
        if (data.conversations.length === 0) {
            list.innerHTML = '<div class="bg-gray-800 rounded-2xl p-12 text-center text-gray-400"><p>No conversations yet.</p><p class="text-sm mt-2">Visit the alumni directory to start chatting!</p></div>';
        } else {
            list.innerHTML = data.conversations.map(c => `
                <div onclick="openChat(${c.user_id})" class="bg-gray-800 rounded-2xl p-4 flex items-center gap-4 cursor-pointer card-hover">
                    <div class="w-12 h-12 bg-blue-600 rounded-full flex items-center justify-center text-xl font-bold">${c.name[0]}</div>
                    <div class="flex-1 min-w-0">
                        <p class="font-medium">${c.name}</p>
                        <p class="text-sm text-gray-400 truncate">${c.last_message}</p>
                    </div>
                    ${c.unread > 0 ? '<span class="bg-blue-600 text-xs px-3 py-1 rounded-full font-medium">' + c.unread + ' new</span>' : ''}
                </div>
            `).join('');
        }
    });
</script>
{% endblock %}
'''

ADMIN_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="max-w-5xl mx-auto p-4 lg:p-6">
    <h1 class="text-2xl font-bold mb-6 text-yellow-400">Admin Panel</h1>

    <!-- TABS -->
    <div class="flex gap-2 mb-6 overflow-x-auto pb-2">
        <button onclick="showTab('pending')" id="tab-pending" class="tab-btn px-4 py-2 rounded-lg font-medium bg-yellow-600 text-white whitespace-nowrap">Pending ({{ pending|length }})</button>
        <button onclick="showTab('users')" id="tab-users" class="tab-btn px-4 py-2 rounded-lg font-medium bg-gray-700 hover:bg-gray-600 whitespace-nowrap">All Users ({{ all_users|length }})</button>
        <button onclick="showTab('settings')" id="tab-settings" class="tab-btn px-4 py-2 rounded-lg font-medium bg-gray-700 hover:bg-gray-600 whitespace-nowrap">Homecoming Settings</button>
        <button onclick="showTab('posts')" id="tab-posts" class="tab-btn px-4 py-2 rounded-lg font-medium bg-gray-700 hover:bg-gray-600 whitespace-nowrap">Recent Posts</button>
    </div>
    <!-- PENDING APPROVALS -->
    <div id="content-pending" class="tab-content space-y-3">
        {% if pending %}
        {% for u in pending %}
        <div class="bg-gray-800 rounded-2xl p-4 flex items-center justify-between">
            <div class="flex items-center gap-4">
                {% if u.profile_pic %}
                <img src="data:image/png;base64,{{ u.profile_pic }}" class="w-12 h-12 rounded-xl object-cover">
                {% else %}
                <div class="w-12 h-12 rounded-xl bg-blue-600 flex items-center justify-center text-xl font-bold">{{ u.full_name[0] }}</div>
                {% endif %}
                <div>
                    <p class="font-medium">{{ u.full_name }}</p>
                    <p class="text-sm text-gray-400">{{ u.student_number }} - Batch {{ u.year_graduated }} - {{ u.course }}</p>
                </div>
            </div>
            <div class="flex gap-2">
                <button onclick="approveUser({{ u.id }})" class="bg-green-600 hover:bg-green-700 px-4 py-2 rounded-lg text-sm font-medium">Approve</button>
                <button onclick="rejectUser({{ u.id }})" class="bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg text-sm font-medium">Reject</button>
            </div>
        </div>
        {% endfor %}
        {% else %}
        <div class="bg-gray-800 rounded-2xl p-12 text-center text-gray-400">
            <p class="text-lg">No pending approvals</p>
            <p class="text-sm mt-2">All registrations have been reviewed.</p>
        </div>
        {% endif %}
    </div>
    <!-- ALL USERS -->
    <div id="content-users" class="tab-content hidden space-y-2">
        {% for u in all_users %}
        <div class="bg-gray-800 rounded-2xl p-4 flex items-center justify-between">
            <div class="flex items-center gap-4 cursor-pointer" onclick="openProfile({{ u.id }})">
                {% if u.profile_pic %}
                <img src="data:image/png;base64,{{ u.profile_pic }}" class="w-10 h-10 rounded-lg object-cover">
                {% else %}
                <div class="w-10 h-10 rounded-lg bg-blue-600 flex items-center justify-center font-bold">{{ u.full_name[0] }}</div>
                {% endif %}
                <div>
                    <p class="font-medium">{{ u.full_name }}</p>
                    <p class="text-xs text-gray-400">{{ u.student_number }} - Batch {{ u.year_graduated }} - {{ u.course }}</p>
                </div>
            </div>
            <span class="px-3 py-1 rounded-full text-xs font-medium {{ 'bg-green-900 text-green-300' if u.is_approved else 'bg-yellow-900 text-yellow-300' }}">
                {{ 'Approved' if u.is_approved else 'Pending' }}
            </span>
        </div>
        {% endfor %}
    </div>
    <!-- HOMECOMING SETTINGS -->
    <div id="content-settings" class="tab-content hidden">
        <div class="bg-gray-800 rounded-2xl p-6">
            <h3 class="font-bold text-lg mb-4">Homecoming Configuration</h3>
            <form method="POST" action="/api/admin/settings" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Years Before Homecoming</label>
                        <input type="number" name="years_before" value="{{ settings.years_before }}" min="1" max="100" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 focus:outline-none focus:border-blue-500">
                        <p class="text-xs text-gray-500 mt-1">e.g., 10 = Homecoming on 10th year after graduation</p>
                    </div>
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Event Date</label>
                        <input type="date" name="event_date" value="{{ settings.event_date }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Venue</label>
                        <input type="text" name="venue" value="{{ settings.venue }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Registration Deadline</label>
                        <input type="date" name="deadline" value="{{ settings.deadline }}" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 focus:outline-none focus:border-blue-500">
                    </div>
                </div>
                <div>
                    <label class="block text-sm text-gray-400 mb-1">Additional Details</label>
                    <textarea name="details" rows="3" class="w-full bg-gray-700 border border-gray-600 rounded-lg px-4 py-3 focus:outline-none focus:border-blue-500">{{ settings.details }}</textarea>
                </div>
                <button type="submit" class="btn-primary px-6 py-3 rounded-lg font-medium">Save Settings</button>
            </form>
            <div class="mt-6 p-4 bg-blue-900/20 border border-blue-700 rounded-xl">
                <p class="text-sm text-blue-300">
                    <strong>Note:</strong> Changing "Years Before Homecoming" will automatically recalculate and notify ALL alumni of their new homecoming year.
                </p>
            </div>
        </div>
    </div>
    <!-- RECENT POSTS -->
    <div id="content-posts" class="tab-content hidden space-y-4">
        {% for post in posts %}
        <div class="bg-gray-800 rounded-2xl p-4">
            <div class="flex items-center justify-between mb-2">
                <p class="font-medium cursor-pointer hover:text-blue-400" onclick="openProfile({{ post.user.id }})">{{ post.user.full_name }} <span class="text-gray-400 text-sm font-normal"> - {{ time_ago(post.timestamp) }}</span></p>
                <button onclick="deletePost({{ post.id }})" class="text-red-400 hover:text-red-300 text-sm">Delete</button>
            </div>
            <p class="text-gray-300 text-sm">{{ post.content[:200] }}{% if post.content|length > 200 %}...{% endif %}</p>
        </div>
        {% endfor %}
    </div>
</div>
<script>
function showTab(tab) {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.remove('bg-yellow-600', 'text-white');
        b.classList.add('bg-gray-700');
    });
    document.getElementById('content-' + tab).classList.remove('hidden');
    document.getElementById('tab-' + tab).classList.remove('bg-gray-700');
    document.getElementById('tab-' + tab).classList.add('bg-yellow-600', 'text-white');
}
function approveUser(userId) {
    fetch(`/api/admin/approve/${userId}`, { method: 'POST' })
        .then(() => location.reload());
}
function rejectUser(userId) {
    if (confirm('Reject and delete this registration?')) {
        fetch(`/api/admin/reject/${userId}`, { method: 'POST' })
            .then(() => location.reload());
    }
}
function deletePost(postId) {
    if (confirm('Delete this post?')) {
        fetch(`/api/posts/${postId}/delete`, { method: 'POST' })
            .then(() => location.reload());
    }
}
</script>
{% endblock %}
'''

# ============== TEMPLATE LOADER ==============
from jinja2 import BaseLoader, TemplateNotFound
class StringLoader(BaseLoader):
    def get_source(self, environment, template):
        if template == 'base':
            return (BASE_TEMPLATE, None, lambda: True)
        raise TemplateNotFound(template)
app.jinja_loader = StringLoader()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
