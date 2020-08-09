from core import app
import redis
from flask import request
from enum import Enum
from cronjob import scheduler

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
    
@app.route('/')
@app.route('/index')
def index():
    all_users = scheduler.get_all_users()
    user_to_dramas = [{'user_id': u, 'drama_ids': list(scheduler.get_drama_ids(u))} for u in all_users]
    return {'response': user_to_dramas}


@app.route('/drama/chase', methods=['POST'])
def chase():
    user_id, drama_id = get_user_id(request), get_drama_id(request)
    update_drama(user_id, drama_id, DRAMAOP.CHASE)
    return {'user_id' : user_id, 'drama_id' : drama_id, 'action': 'chase'}
    
@app.route('/drama/abandon', methods=['POST'])
def abandon():
    user_id, drama_id = get_user_id(request), get_drama_id(request)
    update_drama(user_id, drama_id, DRAMAOP.ABANDON)
    return {'user_id' : user_id, 'drama_id': drama_id, 'action': 'abandon'}