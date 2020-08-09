from selenium import webdriver
import time
import redis
import re
from enum import Enum
import smtplib
import logging
import configparser

class VOD(Enum):
    IFVOID = 1

def get_all_users_key():
    return 'users'
    
def get_drama_url(vod, drama_id):
    if vod == VOD.IFVOD:
        return "https://www.ifvod.tv/detail?id={}".format(drama_id)
    raise Exception('VOD except IFVOD is not supported')

def parse_ifvod_page(page):
    try:
        # wait to load all pages
        time.sleep(3)
        match_obj = re.search('<app-media-list.*?>(.*?)</app-media-list>', page)
        return re.findall(r'\"/play\?id=(.*?)\">.*?</a>', match_obj.group(1))
    except Exception as ex:
        logging.error(ex)

def get_report(drama_id, updated_show_list):
    r = redis.Redis(host='localhost', port=6379, db=0)
    pipe = r.pipeline()
    report = None
    updated_show_msg = ','.join(updated_show_list)
    while True:
        try:
            pipe.watch(drama_id)
            pipe.multi()
            show_msg = pipe.get(drama_id)
            if updated_show_msg != show_msg:
                report = 'Show list for drama {} has been updated, go to watch!'.format(drama_id)
                pipe.set(drama_id, updated_show_msg)
            pipe.execute()
            break
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    return report

def get_drama_report(vod, drama_id):
    driver = webdriver.Chrome()
    driver.get(get_drama_url(vod, drama_id))
    try:
        if vod == VOD.IFVOID:
            updated_show_list = parse_ifvod_page(driver.page_source)
            return get_report(drama_id, updated_show_list)
        raise Exception('VOD except IFVOD is not supported')
    finally:            
        driver.close()

def get_drama_ids(user_id):
    return redis.Redis(host='localhost', port=6379, db=0).get(user_id)

def get_all_drama_reports(vod, user_id):
    drama_ids = get_drama_ids(user_id)
    if isinstance(drama_ids, list):
        return [get_drama_report(vod, drama_id) for drama_id in drama_ids]
    return []

def get_all_users():
    return redis.Redis(host='localhost', port=6379, db=0).get(get_all_users_key())

def send_email(reports):
    meaningful_reports = filter(lambda x: x is not None, reports)
    if len(meaningful_reports) == 0:
        logging.info('No meaningful reports, exit')
        return
    config = configparser.ConfigParser()
    config.read('config.ini')
    try:
        smtp_server, smtp_port = config['SMTP']['server'], config['SMTP']['port']
        sender, receiver, password = config['SMTP']['sender'], config['SMTP']['receiver'], config['SMTP']['password']
    except Exception as e:
        logging.error(e)
        return
        
    msg = '\n'.join(meaningful_reports)
    logging.info('sending {} to notify users'.format(msg))
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.login(sender, password)
        server.sendmail(sender, receiver, msg)
    except Exception as e:
        logging.error(e)
    finally:
        server.quit()

def run():
    all_users = get_all_users()
    if not isinstance(all_users, list):
        logging.info('No user chase drama, exit')
        return
    [send_email(get_all_drama_reports(VOD.IFVOID, u)) for u in all_users]
    

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
