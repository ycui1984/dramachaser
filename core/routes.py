from datetime import datetime
from core import app
import redis
from flask import request
from enum import Enum
from cronjob import scheduler
from flask import render_template, flash, redirect, url_for, request
from flask_login import login_user, logout_user, current_user, login_required
from werkzeug.urls import url_parse
from core import app, db
from core.forms import DramaChasingForm,LoginForm, RegistrationForm, EditProfileForm, ResetPasswordRequestForm, ResetPasswordForm
from core.models import User
from core.email import send_password_reset_email

class DRAMAOP(Enum):
    CHASE = 1
    ABANDON = 2
    
def get_user_id(request):
    user_id = request.args.get('user_id', type = int)
    if user_id is None:
        raise Exception('user id is required to chase or abandon drama')
    return user_id

def get_drama_id(request):
    drama_id = request.args.get('drama_id', type = str) 
    if drama_id is None:
        raise Exception('drama id is required to chase or abandon drama')
    return drama_id

def update_drama(user_id, drama_id, op):
    r = redis.Redis(host='localhost', port=6379, db=0)
    pipe = r.pipeline()
    while True:
        try:
            pipe.watch(user_id)
            pipe.multi()
            if op == DRAMAOP.CHASE:
                pipe.sadd(user_id, drama_id)
                pipe.sadd(scheduler.get_all_users_key(), user_id)
            else:
                pipe.srem(user_id, drama_id)
                pipe.srem(scheduler.get_all_users_key(), user_id)
            pipe.execute()
            break
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    
def chase(user_id, drama_id):
    update_drama(user_id, drama_id, DRAMAOP.CHASE)
    return {'user_id' : user_id, 'drama_id' : drama_id, 'action': 'chase'}
    
def abandon(user_id, drama_id):
    update_drama(user_id, drama_id, DRAMAOP.ABANDON)
    return {'user_id' : user_id, 'drama_id': drama_id, 'action': 'abandon'}

@app.before_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.utcnow()
        db.session.commit()


@app.route('/', methods=['POST', 'GET'])
@app.route('/index', methods=['POST', 'GET'])
@login_required
def index():
    form = DramaChasingForm()
    user_id = current_user.username
    if form.validate_on_submit():
        drama_id = form.drama_id.data
        drama_name = form.drama_name.data
        chase(user_id, drama_id)
        flash('Start to chase drama {}'.format(drama_name))
    drama_ids = list(scheduler.get_drama_ids(user_id))
    return render_template('index.html', title='Home', drama_ids=drama_ids, form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('index')
        return redirect(next_page)
    return render_template('login.html', title='Sign In', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Congratulations, you are now a registered user!')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)

@app.route('/user/<username>')
@login_required
def user(username):
    user = User.query.filter_by(username=username).first_or_404()
    return render_template('user.html', user=user)


@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm(current_user.username)
    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.about_me = form.about_me.data
        db.session.commit()
        flash('Your changes have been saved.')
        return redirect(url_for('edit_profile'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.about_me.data = current_user.about_me
    return render_template('edit_profile.html', title='Edit Profile',
                           form=form)


@app.route('/reset_password_request', methods=['GET', 'POST'])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            send_password_reset_email(user)
        flash('Check your email for the instructions to reset your password')
        return redirect(url_for('login'))
    return render_template('reset_password_request.html',
                           title='Reset Password', form=form)


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    user = User.verify_reset_password_token(token)
    if not user:
        return redirect(url_for('index'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been reset.')
        return redirect(url_for('login'))
    return render_template('reset_password.html', form=form)

