from selenium import webdriver
import time
import redis
import re
from enum import Enum
import smtplib
import logging
import configparser
import pickle
from core import app, mail
from core.email import send_email
from flask import render_template
import requests

class VOD(Enum):
    IFVOD = 1

def get_all_users_key():
    return 'users'
    
def get_drama_url(vod, drama_id):
    if vod == VOD.IFVOD:
        return "https://www.ifvod.tv/detail?id={}".format(drama_id)
    raise Exception('VOD except IFVOD is not supported')

def parse_ifvod_page(page):
    try:
        match_obj = re.search('<app-media-list.*?>(.*?)</app-media-list>', page)
        return re.findall(r'\"/play\?id=(.*?)\">.*?</a>', match_obj.group(1))
    except Exception as ex:
        logging.error(ex)

def parse_metadata_page(page):
    match_obj = re.search('<meta.*?name="title".*?content="(.*?) - IFVOD".*?/>', page)
    return match_obj.group(1)

def parse_drama_name(drama_id):
    url = get_drama_url(VOD.IFVOD, drama_id)
    page = requests.get(url)
    return parse_metadata_page(page.text)

def get_user_generated_content_key(user_id, drama_id):
    return '{}:{}'.format(user_id, drama_id)

def get_user_generated_content(user_id, drama_id):
    r = redis.Redis(host='localhost', port=6379, db=0)
    ugc_key = get_user_generated_content_key(user_id, drama_id)
    ugc_value = r.get(ugc_key)
    return None if ugc_value is None else pickle.loads(r.get(ugc_key))

def get_drama_obj(drama_id):
    r = redis.Redis(host='localhost', port=6379, db=0)
    serialized_drama_obj = r.get(drama_id)
    return None if serialized_drama_obj is None else pickle.loads(serialized_drama_obj)

# key => drama_id
# value => dict {
#   last_updated_time:<last_updated_time>, current_show_list:<current_show_list>, delta_show_list:<delta_show_list>
# }
def get_drama_updates(vod, drama_id):
    r = redis.Redis(host='localhost', port=6379, db=0)
    old_obj = get_drama_obj(drama_id)
    if old_obj is not None and time.time() - old_obj['last_updated_time'] <= 3600:
        return old_obj['delta_show_list']
    current_show_list = get_current_show_list(vod, drama_id)
    old_show_list = old_obj['current_show_list'] if old_obj is not None else None
    delta_show_list = get_delta_show_list(current_show_list, old_show_list)
    obj = {}
    obj['last_updated_time'] = time.time()
    obj['current_show_list'] = current_show_list
    obj['delta_show_list'] = delta_show_list
    r.set(drama_id, pickle.dumps(obj))
    return delta_show_list

def get_delta_show_list(current_show_list, old_show_list):
    return [] if old_show_list is None else filter(lambda x: x not in old_show_list, current_show_list)

def get_current_show_list(vod, drama_id):
    driver = webdriver.Chrome()
    try:
        driver.get(get_drama_url(vod, drama_id))
        if vod == VOD.IFVOD:
            # wait to load all pages
            time.sleep(3)
            return parse_ifvod_page(driver.page_source)
        raise Exception('VOD except IFVOD is not supported')
    finally:            
        driver.close()

def get_drama_ids(user_id):
    return redis.Redis(host='localhost', port=6379, db=0).smembers(user_id)

def get_all_drama_reports(vod, user_id):
    drama_ids = get_drama_ids(user_id)
    reports = {}
    if not isinstance(drama_ids, set):
        return reports
    
    for drama_id in drama_ids:
        updates = get_drama_updates(vod, drama_id)
        if len(updates) != 0:
            reports[drama_id] = updates
    return reports

def get_all_users():
    return redis.Redis(host='localhost', port=6379, db=0).smembers(get_all_users_key())

def notify_user_by_email(email, reports):
    if len(reports) == 0:
        return
    with app.app_context():
        send_email(
            '[DramaChaser] Drama updates',
            sender=app.config['ADMINS'][0], 
            recipients=[email],
            text_body=render_template('email/drama_updates.txt', reports=reports),
            html_body=render_template('email/drama_updates.html', reports=reports)
        ) 

def run():
    all_users = get_all_users()
    if not isinstance(all_users, set):
        logging.info('No user chase drama, exit')
        return
    for user in all_users:
        reports = get_all_drama_reports(VOD.IFVOD, user)
        notify_user_by_email(user, reports)
    
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
